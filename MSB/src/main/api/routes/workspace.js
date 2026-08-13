// AI Workspace — single-click launcher for the local GGselMSB project.
//
//   POST /workspace/launch
//     1. Убедиться, что профиль "AI Workspace" существует (создать при
//        отсутствии). У него startUrl = GGSELLER_URL, без прокси, тег
//        "ai-workspace" для однозначной идентификации.
//     2. Проверить, отвечает ли Flask на GGSELLER_URL. Если нет —
//        запустить `python app.py` из GGSELLER_PROJECT_DIR (detached,
//        чтобы пережил MSB) и дождаться ответа.
//     3. Поднять браузер профиля и перейти на GGSELLER_URL.
//
//   GET /workspace/status
//     Возвращает { profile, flask } — что есть и в каком состоянии,
//     без сайд-эффектов. Используется UI для раскраски кнопки.
//
// Concurrency: простой in-memory lock на запуск Flask, чтобы два
// параллельных клика не подняли две копии. Lock автоматически снимается
// при завершении/ошибке.

import { spawn } from 'node:child_process';
import { DEFAULTS } from '../../core/constants.js';
import { generateFingerprint } from '../../lib/fingerprint.js';

// Resolve config once (env can override at startup).
function resolveConfig() {
  return {
    projectDir: process.env.MSB_GGSELLER_PROJECT_DIR || DEFAULTS.GGSELLER_PROJECT_DIR,
    url: process.env.MSB_GGSELLER_URL || DEFAULTS.GGSELLER_URL,
    python: process.env.MSB_GGSELLER_PYTHON || DEFAULTS.GGSELLER_PYTHON,
    startCmd: process.env.MSB_GGSELLER_START_CMD || DEFAULTS.GGSELLER_START_CMD,
    healthTimeoutMs: Number(process.env.MSB_GGSELLER_HEALTH_TIMEOUT_MS) || DEFAULTS.GGSELLER_HEALTH_TIMEOUT_MS,
    healthIntervalMs: Number(process.env.MSB_GGSELLER_HEALTH_INTERVAL_MS) || DEFAULTS.GGSELLER_HEALTH_INTERVAL_MS,
    profileName: DEFAULTS.GGSELLER_PROFILE_NAME,
    profileGroup: DEFAULTS.GGSELLER_PROFILE_GROUP,
    profileTags: DEFAULTS.GGSELLER_PROFILE_TAGS,
  };
}

// ─── Flask liveness ──────────────────────────────────────────────────────

async function checkFlask(url, timeoutMs = 1500) {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    const res = await fetch(url, { signal: ctrl.signal, method: 'GET' });
    clearTimeout(t);
    // Flask / отвечает 200 на index; любая 2xx/3xx/4xx (но не 5xx и не network error)
    // означает, что процесс поднялся. 5xx тоже ок — приложение работает, просто
    // маршрут сломан. Считаем живым любой ответ.
    return res.status > 0;
  } catch {
    return false;
  }
}

async function waitForFlask(url, timeoutMs, intervalMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await checkFlask(url, 1500)) return true;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}

// ─── Flask launcher ──────────────────────────────────────────────────────

let _flaskLaunchInFlight = null;   // Promise — одиночный запуск
let _flaskChild = null;            // текущий child-процесс (для diagnostics)

function spawnFlask(cfg, logger) {
  const args = [cfg.startCmd];
  const child = spawn(cfg.python, args, {
    cwd: cfg.projectDir,
    detached: true,           // переживёт MSB, станет independent process group
    stdio: 'ignore',          // логи Flask пишут сам (logs/app.log)
    windowsHide: true,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
  });
  child.on('error', (err) => {
    logger?.warn?.({ err: err.message }, 'workspace: flask child error');
    _flaskChild = null;
  });
  child.on('exit', (code) => {
    logger?.info?.({ code }, 'workspace: flask child exited');
    if (_flaskChild === child) _flaskChild = null;
  });
  child.unref();
  _flaskChild = child;
  logger?.info?.({ pid: child.pid, cwd: cfg.projectDir, cmd: `${cfg.python} ${cfg.startCmd}` }, 'workspace: flask spawned');
  return child;
}

async function ensureFlask(cfg, logger) {
  // Уже отвечает — не трогаем.
  if (await checkFlask(cfg.url)) {
    return { started: false, reason: 'already-running' };
  }
  // Уже кто-то запускает — ждём того же промпса.
  if (_flaskLaunchInFlight) {
    await _flaskLaunchInFlight;
    return { started: false, reason: 'launched-by-other' };
  }
  _flaskLaunchInFlight = (async () => {
    spawnFlask(cfg, logger);
    const ok = await waitForFlask(cfg.url, cfg.healthTimeoutMs, cfg.healthIntervalMs);
    if (!ok) {
      // Не фатально — UI покажет, что Flask не поднялся. Можно ретрайнуть.
      logger?.warn?.({ url: cfg.url, timeoutMs: cfg.healthTimeoutMs }, 'workspace: flask did not respond in time');
    }
  })();
  try {
    await _flaskLaunchInFlight;
  } finally {
    _flaskLaunchInFlight = null;
  }
  // После ожидания проверяем ещё раз — если и так не отвечает, считаем ошибкой.
  const alive = await checkFlask(cfg.url);
  return { started: true, alive, reason: alive ? 'launched' : 'timeout' };
}

// ─── Profile ensure ──────────────────────────────────────────────────────

function findWorkspaceProfile(profileManager) {
  const wantedTag = 'ai-workspace';
  const all = profileManager.list();
  // Сначала ищем по точному тегу (источник правды).
  const tagged = all.find((p) => Array.isArray(p.tags) && p.tags.includes(wantedTag));
  if (tagged) return tagged;
  // Fallback: по имени/группе, если кто-то удалил тег.
  const byName = all.find((p) => (p.name || '').toLowerCase() === DEFAULTS.GGSELLER_PROFILE_NAME.toLowerCase());
  if (byName) return byName;
  return null;
}

async function ensureWorkspaceProfile(profileManager, cfg, logger) {
  const existing = findWorkspaceProfile(profileManager);
  if (existing) {
    // Поддерживаем актуальность ключевых полей (стартовая страница, тег, имя).
    const needsPatch =
      (existing.startUrl || '') !== cfg.url ||
      !(Array.isArray(existing.tags) && existing.tags.includes('ai-workspace')) ||
      (existing.group || null) !== cfg.profileGroup ||
      (existing.name || '') !== cfg.profileName;
    if (needsPatch) {
      const updated = await profileManager.update(existing.id, {
        startUrl: cfg.url,
        tags: cfg.profileTags,
        group: cfg.profileGroup,
        name: cfg.profileName,
      });
      logger?.info?.({ profileId: updated.id, name: updated.name }, 'workspace: profile patched to match config');
      return updated;
    }
    return existing;
  }

  // Создаём чистый профиль: без прокси, минимальный фингерпринт, прямой старт.
  const created = await profileManager.create({
    name: cfg.profileName,
    group: cfg.profileGroup,
    notes:
      'AI Workspace — профиль для работы с GGSeller Flask панелью через ИИ-агента. ' +
      'startUrl = ' + cfg.url + '. Запускается кнопкой "🚀 ИИ Воркспейс" в топбаре.',
    engine: 'auto',
    humanize: true,
    aggressiveFingerprint: false,
    proxyEnabled: false,
    proxy: null,
    startUrl: cfg.url,
    tags: cfg.profileTags,
    flagged: false,
    sortOrder: 0,    // первым в списке
    fingerprint: generateFingerprint({ platform: 'Win32' }),
  });
  logger?.info?.({ profileId: created.id, name: created.name, number: created.number }, 'workspace: profile created');
  return created;
}

// ─── Route registration ──────────────────────────────────────────────────

export function registerWorkspaceRoutes({ app, profileManager, browserLauncher, logger }) {
  const log = logger?.child?.({ mod: 'workspace' }) || logger || console;

  app.post('/workspace/launch', {
    schema: { summary: 'Launch AI Workspace: ensure profile, start Flask, open browser' },
  }, async (req, reply) => {
    const cfg = resolveConfig();

    // 1) Профиль.
    let profile;
    try {
      profile = await ensureWorkspaceProfile(profileManager, cfg, log);
    } catch (err) {
      log?.error?.({ err: err.message }, 'workspace: ensure profile failed');
      return reply.code(500).send({ ok: false, error: 'profile-ensure-failed: ' + err.message });
    }

    // 2) Flask — поднять если не отвечает.
    let flaskResult;
    try {
      flaskResult = await ensureFlask(cfg, log);
    } catch (err) {
      log?.error?.({ err: err.message }, 'workspace: flask launch crashed');
      flaskResult = { started: false, alive: false, reason: 'crash: ' + err.message };
    }

    // 3) Браузер профиля.
    let browserResult = { started: false, alreadyRunning: false };
    try {
      const status = browserLauncher.status().find((r) => r.id === profile.id);
      if (status) {
        // Уже поднят — просто переходим на стартовую страницу.
        browserResult.alreadyRunning = true;
        try {
          await browserLauncher.goto(profile.id, cfg.url);
        } catch (e) {
          log?.warn?.({ err: e.message }, 'workspace: goto failed on already-running profile');
        }
      } else {
        await browserLauncher.start(profile, { headless: false });
        // startUrl уже в профиле, но дублируем goto — на случай если start
        // был без явного URL (бывает при -url).
        try {
          await browserLauncher.goto(profile.id, cfg.url);
        } catch (e) {
          log?.debug?.({ err: e.message }, 'workspace: post-start goto skipped');
        }
        browserResult.started = true;
      }
    } catch (err) {
      log?.error?.({ profileId: profile.id, err: err.message }, 'workspace: browser start failed');
      return reply.code(500).send({
        ok: false,
        error: 'browser-start-failed: ' + err.message,
        profile: { id: profile.id, name: profile.name, number: profile.number },
        flask: flaskResult,
      });
    }

    log?.info?.(
      {
        profileId: profile.id,
        profileNumber: profile.number,
        flask: flaskResult,
        browser: browserResult,
      },
      'workspace: launched',
    );

    return {
      ok: true,
      profile: {
        id: profile.id,
        name: profile.name,
        number: profile.number,
        startUrl: profile.startUrl,
        group: profile.group,
        tags: profile.tags,
      },
      flask: {
        url: cfg.url,
        projectDir: cfg.projectDir,
        ...flaskResult,
      },
      browser: browserResult,
    };
  });

  app.get('/workspace/status', {
    schema: { summary: 'Workspace status: profile + flask + browser, no side effects' },
  }, async () => {
    const cfg = resolveConfig();
    const profile = findWorkspaceProfile(profileManager);
    let browserRunning = false;
    if (profile) {
      browserRunning = !!browserLauncher.status().find((r) => r.id === profile.id);
    }
    const flaskAlive = await checkFlask(cfg.url, 1500);
    return {
      ok: true,
      config: {
        projectDir: cfg.projectDir,
        url: cfg.url,
        python: cfg.python,
        startCmd: cfg.startCmd,
      },
      profile: profile
        ? {
            id: profile.id,
            name: profile.name,
            number: profile.number,
            startUrl: profile.startUrl,
            group: profile.group,
            tags: profile.tags,
            running: browserRunning,
          }
        : null,
      flask: { running: flaskAlive, url: cfg.url },
      childPid: _flaskChild?.pid ?? null,
    };
  });
}
