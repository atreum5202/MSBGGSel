/**
 * Cookie Warmer API
 *
 * Позволяет внешним инструментам (GGselV7 и др.) запускать прогрев куков
 * через MSB, не имея прямого доступа к браузерам.
 *
 * POST /api/warmer/start          — запустить прогрев (можно передать profileIds[])
 * GET  /api/warmer/status         — текущий статус прогрева
 * POST /api/warmer/stop           — остановить текущий прогрев
 */

const TARGET_URL = 'https://ggsel.net';
const QRATOR_KEYS = new Set(['cf_clearance', '__ddg1_', '__ddg2_', 'qrator_jsid', 'qrator_ssid', '_ym_uid']);
const WARM_DELAY_MS = 4000;
const NAVIGATE_WAIT_MS = 8000;

// Глобальное состояние прогрева (один прогрев за раз)
let warmerState = {
  running: false,
  stopRequested: false,
  startedAt: null,
  finishedAt: null,
  total: 0,
  current: 0,
  currentProfileId: null,
  rows: [],      // { id, name, status, ggsel, hasQrator, error }
};

async function checkProfileCookies(profileId, cookieStore) {
  try {
    const all = await cookieStore.getCookies(profileId);
    const cookies = Array.isArray(all) ? all : (all?.data ?? []);
    const ggsel = cookies.filter(c => c.domain?.includes('ggsel'));
    const keys = ggsel.map(c => c.name).filter(Boolean);
    const hasQrator = keys.some(k => QRATOR_KEYS.has(k));
    return { total: cookies.length, ggsel: ggsel.length, keys, hasQrator };
  } catch {
    return { total: 0, ggsel: 0, keys: [], hasQrator: false };
  }
}

async function warmProfile(row, { profileManager, browserLauncher, cookieStore, logger }) {
  const updateRow = (patch) => {
    const idx = warmerState.rows.findIndex(r => r.id === row.id);
    if (idx >= 0) Object.assign(warmerState.rows[idx], patch);
  };

  warmerState.currentProfileId = row.id;

  // 1. Проверяем куки — может уже прогрет
  const before = await checkProfileCookies(row.id, cookieStore);
  if (before.hasQrator) {
    updateRow({ status: 'ok', ggsel: before.ggsel, hasQrator: true });
    logger?.info({ profileId: row.id }, 'warmer: already has Qrator cookies, skipping');
    return;
  }

  // 2. Запускаем профиль если не запущен
  updateRow({ status: 'running' });
  let startedByUs = false;
  const isRunning = browserLauncher.isRunning(row.id);
  if (!isRunning) {
    try {
      const profile = profileManager.get(row.id);
      await browserLauncher.start(profile, { headless: false });
      startedByUs = true;
      await new Promise(r => setTimeout(r, 2500)); // ждём запуск браузера
    } catch (e) {
      updateRow({ status: 'error', error: `start: ${e.message}` });
      logger?.warn({ profileId: row.id, err: e.message }, 'warmer: failed to start profile');
      return;
    }
  }

  // 3. Навигация на ggsel.net через goto
  updateRow({ status: 'navigate' });
  let gotoOk = false;
  try {
    await browserLauncher.goto(row.id, TARGET_URL);
    gotoOk = true;
  } catch (e) {
    logger?.debug({ profileId: row.id, err: e.message }, 'warmer: goto failed, waiting anyway');
  }

  const waitMs = gotoOk ? NAVIGATE_WAIT_MS : NAVIGATE_WAIT_MS * 1.5;
  await new Promise(r => setTimeout(r, waitMs));

  // 4. Проверяем куки после навигации
  updateRow({ status: 'checking' });
  const after = await checkProfileCookies(row.id, cookieStore);

  // 5. Если Qrator ещё не появился — ждём немного (JS Qrator может быть медленным)
  let final = after;
  if (!after.hasQrator && after.ggsel > 0) {
    await new Promise(r => setTimeout(r, 4000));
    final = await checkProfileCookies(row.id, cookieStore);
  }

  // 6. Закрываем браузер если мы его открыли
  if (startedByUs) {
    try { await browserLauncher.stop(row.id); } catch {}
  }

  // 7. Фиксируем результат
  if (final.hasQrator) {
    updateRow({ status: 'ok', ggsel: final.ggsel, hasQrator: true });
  } else if (final.ggsel > 0) {
    updateRow({ status: 'partial', ggsel: final.ggsel, hasQrator: false });
  } else {
    updateRow({ status: 'empty', ggsel: 0, hasQrator: false });
  }

  logger?.info({ profileId: row.id, status: warmerState.rows.find(r => r.id === row.id)?.status }, 'warmer: profile done');
}

async function runWarmer({ profileIds, profileManager, browserLauncher, cookieStore, logger }) {
  let profiles = profileManager.list();
  if (profileIds?.length) {
    const idSet = new Set(profileIds.map(String));
    profiles = profiles.filter(p => idSet.has(String(p.id)));
  }

  warmerState = {
    running: true,
    stopRequested: false,
    startedAt: new Date().toISOString(),
    finishedAt: null,
    total: profiles.length,
    current: 0,
    currentProfileId: null,
    rows: profiles.map(p => ({ id: p.id, name: p.name, status: 'waiting', ggsel: 0, hasQrator: false, error: '' })),
  };

  logger?.info({ count: profiles.length }, 'warmer: starting');

  for (let i = 0; i < warmerState.rows.length; i++) {
    if (warmerState.stopRequested) {
      warmerState.rows.slice(i).forEach(r => { r.status = 'skipped'; });
      break;
    }
    warmerState.current = i + 1;
    await warmProfile(warmerState.rows[i], { profileManager, browserLauncher, cookieStore, logger });
    if (i < warmerState.rows.length - 1 && !warmerState.stopRequested) {
      await new Promise(r => setTimeout(r, WARM_DELAY_MS));
    }
  }

  warmerState.running = false;
  warmerState.currentProfileId = null;
  warmerState.finishedAt = new Date().toISOString();
  logger?.info({ done: warmerState.rows.filter(r => r.status === 'ok').length }, 'warmer: finished');
}

export function registerWarmerRoutes({ app, profileManager, browserLauncher, cookieStore, logger }) {
  // POST /api/warmer/start
  app.post('/api/warmer/start', {
    schema: {
      summary: 'Start cookie warming for all (or specific) profiles',
      body: {
        type: 'object',
        properties: {
          profileIds: {
            type: 'array',
            items: { type: 'string' },
            description: 'Subset of profile IDs to warm. Omit to warm ALL profiles.',
          },
        },
      },
    },
  }, async (req, reply) => {
    if (warmerState.running) {
      return reply.code(409).send({ ok: false, error: 'Warmer already running', state: warmerState });
    }
    const { profileIds } = req.body || {};

    // Запускаем в фоне — не ждём завершения
    runWarmer({ profileIds, profileManager, browserLauncher, cookieStore, logger }).catch(err => {
      logger?.error({ err: err.message }, 'warmer: unexpected error');
      warmerState.running = false;
      warmerState.finishedAt = new Date().toISOString();
    });

    const total = profileIds?.length || profileManager.list().length;
    return { ok: true, message: 'Warmer started', total };
  });

  // GET /api/warmer/status
  app.get('/api/warmer/status', {
    schema: { summary: 'Get current cookie warmer status' },
  }, async () => {
    const summary = {
      ok:      warmerState.rows.filter(r => r.status === 'ok').length,
      partial: warmerState.rows.filter(r => r.status === 'partial').length,
      empty:   warmerState.rows.filter(r => r.status === 'empty').length,
      error:   warmerState.rows.filter(r => r.status === 'error').length,
      skipped: warmerState.rows.filter(r => r.status === 'skipped').length,
      waiting: warmerState.rows.filter(r => r.status === 'waiting').length,
    };
    return { ...warmerState, summary };
  });

  // POST /api/warmer/stop
  app.post('/api/warmer/stop', {
    schema: { summary: 'Request warmer to stop after current profile' },
  }, async (_req, reply) => {
    if (!warmerState.running) {
      return reply.code(400).send({ ok: false, error: 'Warmer is not running' });
    }
    warmerState.stopRequested = true;
    return { ok: true, message: 'Stop requested' };
  });
}
