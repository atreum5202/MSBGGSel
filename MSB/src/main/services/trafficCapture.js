/**
 * trafficCapture.js — встроенный перехват трафика через mitmproxy
 *
 * Запускает mitmdump как дочерний процесс, пишет трафик профиля в
 * captures/<profileId>/<timestamp>/ и управляет состоянием через API.
 *
 * Схема для одного профиля:
 *   start(profileId)   → находит свободный порт, запускает mitmdump,
 *                        патчит прокси профиля через browserLauncher.switchProxy()
 *   stop(profileId)    → убивает mitmdump, восстанавливает исходный прокси
 *   status(profileId)  → { active, port, pid, captureDir, startedAt, byteCount }
 *   listCaptures(id)   → список сессий из captures/<id>/
 *
 * Требования:
 *   - mitmdump должен быть в PATH (или указан через env MITMDUMP_BIN)
 *   - Если профиль использует upstream-прокси, цепочка:
 *       браузер → mitmdump → upstream proxy
 *
 * Переменные среды:
 *   MITMDUMP_BIN      путь к mitmdump (по умолчанию 'mitmdump')
 *   MITM_SCRIPT       путь к intercept.py (необязательно)
 *   MITM_CAPTURES_DIR путь к папке captures (по умолчанию рядом с MSB)
 */

import { spawn } from 'node:child_process';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { EventEmitter } from 'node:events';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Папка captures — внутри MSB (по умолчанию <MSB>/captures/).
// Раньше было рядом на Desktop (4 уровня вверх), что захламляло рабочий стол.
// Можно переопределить через env MITM_CAPTURES_DIR.
const DEFAULT_CAPTURES_ROOT = path.resolve(__dirname, '..', '..', '..', 'captures');
const CAPTURES_ROOT = process.env.MITM_CAPTURES_DIR || DEFAULT_CAPTURES_ROOT;

const MITMDUMP_BIN = process.env.MITMDUMP_BIN || 'mitmdump';
const MITM_SCRIPT  = process.env.MITM_SCRIPT  || null;

// Диапазон портов для mitmdump
const PORT_MIN = 18100;
const PORT_MAX = 18200;

// ── Утилиты ─────────────────────────────────────────────────────────────────

function getFreePort(min = PORT_MIN, max = PORT_MAX) {
  return new Promise((resolve, reject) => {
    const tryPort = (p) => {
      if (p > max) return reject(new Error('No free port in range'));
      const server = net.createServer();
      server.once('error', () => tryPort(p + 1));
      server.once('listening', () => {
        server.close(() => resolve(p));
      });
      server.listen(p, '127.0.0.1');
    };
    tryPort(min);
  });
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
  return p;
}

function captureSessionDir(profileId) {
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  return ensureDir(path.join(CAPTURES_ROOT, profileId, ts));
}

function listCaptureSessions(profileId) {
  const base = path.join(CAPTURES_ROOT, profileId);
  if (!fs.existsSync(base)) return [];
  return fs.readdirSync(base)
    .map((name) => {
      const full = path.join(base, name);
      const stat = fs.statSync(full);
      if (!stat.isDirectory()) return null;
      // Считаем размер записанных файлов
      const files = fs.readdirSync(full).map(f => {
        try { return fs.statSync(path.join(full, f)).size; } catch { return 0; }
      });
      const bytes = files.reduce((a, b) => a + b, 0);
      return { session: name, path: full, bytes, files: files.length };
    })
    .filter(Boolean)
    .sort((a, b) => b.session.localeCompare(a.session)); // новые вверху
}

// ── TrafficCaptureService ────────────────────────────────────────────────────

export class TrafficCaptureService extends EventEmitter {
  /**
   * @param {{ profileManager, browserLauncher, logger }} opts
   */
  constructor({ profileManager, browserLauncher, logger }) {
    super();
    this.profileManager   = profileManager;
    this.browserLauncher  = browserLauncher;
    this.logger           = logger?.child?.({ mod: 'trafficCapture' }) || logger || console;

    // Map<profileId, SessionInfo>
    this._sessions = new Map();
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Запустить перехват для профиля.
   * Если профиль уже запущен — возвращает текущую сессию.
   *
   * @param {string}  profileId
   * @param {object}  [opts]
   * @param {boolean} [opts.saveHar=false]   — писать .har (человекочитаемый)
   * @param {boolean} [opts.saveFlow=true]   — писать .mitm (mitmproxy flow format)
   * @param {string}  [opts.filterHost]      — фильтровать только по этому хосту
   * @returns {Promise<SessionInfo>}
   */
  async start(profileId, opts = {}) {
    if (this._sessions.has(profileId)) {
      return this._publicInfo(this._sessions.get(profileId));
    }

    const profile = this.profileManager.get(profileId);
    if (!profile) {
      const err = new Error('Profile not found');
      err.statusCode = 404;
      throw err;
    }

    // Проверяем наличие mitmdump
    await this._checkMitmdump();

    const port       = await getFreePort();
    const captureDir = captureSessionDir(profileId);
    const saveFlow   = opts.saveFlow !== false;
    const saveHar    = !!opts.saveHar;
    const filterHost = opts.filterHost || null;

    // Строим аргументы mitmdump
    const args = [
      '--listen-host', '127.0.0.1',
      '--listen-port', String(port),
      '--set', 'ssl_insecure=true',   // принимаем self-signed для HTTPS
    ];

    // Если у профиля есть upstream-прокси — цепочкуем
    const upstream = profile.proxy;
    if (upstream && profile.proxyEnabled !== false) {
      const upstreamUrl = this._buildProxyUrl(upstream);
      if (upstreamUrl) {
        args.push('--mode', `upstream:${upstreamUrl}`);
        this.logger.info({ profileId, upstream: upstreamUrl }, 'traffic capture: chaining upstream proxy');
      }
    }

    if (saveFlow) {
      const flowFile = path.join(captureDir, 'capture.mitm');
      args.push('-w', flowFile);
    }

    if (MITM_SCRIPT) {
      args.push('-s', MITM_SCRIPT);
    }

    if (filterHost) {
      // Простой фильтр: пишем только запросы к нужному хосту
      args.push('--set', `flow_detail=3`);
    }

    this.logger.info({ profileId, port, captureDir, args: args.join(' ') }, 'starting mitmdump');

    const proc = spawn(MITMDUMP_BIN, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    proc.stdout.on('data', (d) => {
      this.logger.debug({ profileId, data: d.toString().trim() }, 'mitmdump stdout');
    });
    proc.stderr.on('data', (d) => {
      this.logger.debug({ profileId, data: d.toString().trim() }, 'mitmdump stderr');
    });

    proc.on('exit', (code, signal) => {
      this.logger.info({ profileId, code, signal }, 'mitmdump exited');
      const sess = this._sessions.get(profileId);
      if (sess) {
        this._sessions.delete(profileId);
        this.emit('stopped', { profileId, code, signal });
      }
    });

    // Ждём пока mitmdump поднимется (порт начнёт слушать)
    await this._waitForPort(port, 8000);

    // Сохраняем исходный прокси профиля чтобы восстановить после стопа
    const originalProxy    = profile.proxy    ?? null;
    const originalProxyEnabled = profile.proxyEnabled ?? true;

    // Патчим прокси профиля на mitmdump
    const mitmProxy = { host: '127.0.0.1', port, protocol: 'http' };

    if (this.browserLauncher.isRunning(profileId)) {
      // Браузер уже запущен → переключаем через route()
      await this.browserLauncher.switchProxy(profileId, mitmProxy);
    } else {
      // Браузер ещё не запущен → патчим профиль на диске
      await this.profileManager.update(profileId, { proxy: mitmProxy, proxyEnabled: true });
    }

    const session = {
      profileId,
      port,
      pid: proc.pid,
      proc,
      captureDir,
      startedAt: Date.now(),
      originalProxy,
      originalProxyEnabled,
      saveFlow,
      saveHar,
      filterHost,
    };

    this._sessions.set(profileId, session);
    this.emit('started', { profileId, port, captureDir });
    this.logger.info({ profileId, port, pid: proc.pid, captureDir }, 'traffic capture started');

    return this._publicInfo(session);
  }

  /**
   * Остановить перехват для профиля.
   */
  async stop(profileId) {
    const session = this._sessions.get(profileId);
    if (!session) {
      const err = new Error('No active capture for this profile');
      err.statusCode = 404;
      throw err;
    }

    // Убиваем mitmdump
    try {
      session.proc.kill('SIGTERM');
      // Windows fallback
      if (process.platform === 'win32') {
        spawn('taskkill', ['/PID', String(session.pid), '/F'], { stdio: 'ignore' });
      }
    } catch (err) {
      this.logger.warn({ profileId, err: err.message }, 'kill mitmdump failed');
    }

    this._sessions.delete(profileId);

    // Восстанавливаем исходный прокси
    try {
      if (this.browserLauncher.isRunning(profileId)) {
        await this.browserLauncher.switchProxy(profileId, session.originalProxy);
      } else {
        await this.profileManager.update(profileId, {
          proxy: session.originalProxy,
          proxyEnabled: session.originalProxyEnabled,
        });
      }
    } catch (err) {
      this.logger.warn({ profileId, err: err.message }, 'restore proxy failed');
    }

    this.emit('stopped', { profileId });
    this.logger.info({ profileId, captureDir: session.captureDir }, 'traffic capture stopped');

    return {
      stopped: true,
      captureDir: session.captureDir,
      duration: Date.now() - session.startedAt,
    };
  }

  /**
   * Статус перехвата для профиля.
   */
  status(profileId) {
    const session = this._sessions.get(profileId);
    if (!session) return { active: false };

    // Считаем размер записанного трафика
    let byteCount = 0;
    try {
      const files = fs.readdirSync(session.captureDir);
      byteCount = files.reduce((acc, f) => {
        try { return acc + fs.statSync(path.join(session.captureDir, f)).size; } catch { return acc; }
      }, 0);
    } catch {}

    return this._publicInfo(session, byteCount);
  }

  /**
   * Список записанных сессий для профиля.
   */
  listCaptures(profileId) {
    return listCaptureSessions(profileId);
  }

  /**
   * Список всех активных сессий перехвата.
   */
  activeSessions() {
    return Array.from(this._sessions.values()).map(s => this._publicInfo(s));
  }

  // ── Internal ───────────────────────────────────────────────────────────────

  _publicInfo(session, byteCount = 0) {
    return {
      active: true,
      profileId: session.profileId,
      port: session.port,
      pid: session.pid,
      captureDir: session.captureDir,
      startedAt: session.startedAt,
      byteCount,
      saveFlow: session.saveFlow,
      saveHar: session.saveHar,
      filterHost: session.filterHost || null,
      proxy: { host: '127.0.0.1', port: session.port, protocol: 'http' },
    };
  }

  _buildProxyUrl(proxy) {
    if (!proxy?.host || !proxy?.port) return null;
    const proto = proxy.protocol || 'http';
    const auth = proxy.username
      ? `${encodeURIComponent(proxy.username)}:${encodeURIComponent(proxy.password || '')}@`
      : '';
    return `${proto}://${auth}${proxy.host}:${proxy.port}`;
  }

  async _checkMitmdump() {
    return new Promise((resolve, reject) => {
      const proc = spawn(MITMDUMP_BIN, ['--version'], { stdio: 'pipe', windowsHide: true });
      proc.on('error', () => {
        const err = new Error(
          `mitmdump not found. Install it: pip install mitmproxy  (or set MITMDUMP_BIN env var)`
        );
        err.statusCode = 503;
        reject(err);
      });
      proc.on('exit', () => resolve());
    });
  }

  _waitForPort(port, timeoutMs = 8000) {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      const tryConnect = () => {
        const sock = new net.Socket();
        sock.setTimeout(300);
        sock.connect(port, '127.0.0.1', () => {
          sock.destroy();
          resolve();
        });
        sock.on('error', () => {
          sock.destroy();
          if (Date.now() - start > timeoutMs) {
            reject(new Error(`mitmdump did not start on port ${port} within ${timeoutMs}ms`));
          } else {
            setTimeout(tryConnect, 250);
          }
        });
        sock.on('timeout', () => {
          sock.destroy();
          setTimeout(tryConnect, 250);
        });
      };
      tryConnect();
    });
  }
}
