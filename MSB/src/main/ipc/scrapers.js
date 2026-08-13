/**
 * scrapers.js — IPC handlers for the Scrapers page in MSB UI.
 *
 * Layout (поддерживается и плоский, и nested):
 *   $APPDATA/MSB/scrapers/
 *     <scraperId>/manifest.json              ← плоский (legacy)
 *     <group>/<scraperId>/manifest.json      ← nested (новые группы)
 *
 * В response каждый scraper имеет:
 *   - id:     уникальный ключ (для flat = folder, для nested = "group/folder")
 *   - name:   имя папки (для UI)
 *   - group:  null | string (для группировки в UI)
 *   - path:   абсолютный путь к папке scraper-а
 *   - manifest: { ... } из manifest.json
 *
 * IPC channels:
 *   msb:scrapers:list        — return [{ id, name, group, path, manifest }, ...]
 *   msb:scrapers:get         — return full manifest for one scraper (по id)
 *   msb:scrapers:read-text   — return contents of a text file (по relative path)
 *   msb:scrapers:read-jsonl  — parse a .jsonl file and return last N entries
 *   msb:scrapers:open-path   — open file or folder in OS default app
 *   msb:scrapers:run         — spawn the scraper, return pid + log file path
 *   msb:scrapers:read-output — tail the log file of a running scraper
 *   msb:scrapers:kill        — kill a running scraper by id
 */

import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs/promises';
import fssync from 'node:fs';
import { spawn } from 'node:child_process';
import { IPC } from '../core/constants.js';

const IPC_SCRAPERS = IPC.SCRAPERS;

const MSB_APPDATA = process.env.MSB_APPDATA
  || path.join(os.homedir(), 'AppData', 'Roaming', 'MSB');
const SCRAPERS_ROOT = process.env.MSB_SCRAPERS_DIR
  || path.join(MSB_APPDATA, 'scrapers');

const RUNS_ROOT = path.join(MSB_APPDATA, 'scrapers', '.runs');

// In-memory state
const _running = new Map();           // scraperId -> { proc, logFile, ... }
let _dirByIdCache = new Map();        // scraperId -> { dir, group, manifest } (refreshed on list)
let _dirCacheStamp = 0;               // mtime of SCRAPERS_ROOT when cache was built

function isInsideRoot(child, root) {
  const rel = path.relative(root, child);
  return rel && !rel.startsWith('..') && !path.isAbsolute(rel);
}

function safeReadManifest(dir) {
  const mf = path.join(dir, 'manifest.json');
  if (!fssync.existsSync(mf)) return null;
  try {
    return JSON.parse(fssync.readFileSync(mf, 'utf-8'));
  } catch (err) {
    return null;
  }
}

/**
 * Рекурсивно обходит 1 уровень: прямые подпапки с manifest.json — flat scrapers;
 * папки без manifest.json, но с подпапками-с manifest.json — group folders.
 */
async function listScrapers() {
  _dirByIdCache = new Map();

  if (!fssync.existsSync(SCRAPERS_ROOT)) {
    try { _dirCacheStamp = fssync.statSync(SCRAPERS_ROOT).mtimeMs; } catch { _dirCacheStamp = 0; }
    return [];
  }

  const out = [];
  const topEntries = await fs.readdir(SCRAPERS_ROOT, { withFileTypes: true });

  for (const e of topEntries) {
    if (!e.isDirectory() || e.name.startsWith('.')) continue;
    const topDir = path.join(SCRAPERS_ROOT, e.name);

    // Case 1: flat scraper (has own manifest.json)
    const flatManifest = safeReadManifest(topDir);
    if (flatManifest) {
      const id = e.name;
      // Уважаем manifest.group, если он задан (для обратной совместимости с
      // hand-written scraper-ами вроде ggseller-autoreg, у которых group в манифесте)
      const groupFromManifest = (flatManifest.group && typeof flatManifest.group === 'string')
        ? flatManifest.group
        : null;
      out.push({ id, name: e.name, group: groupFromManifest, path: topDir, manifest: flatManifest });
      _dirByIdCache.set(id, { dir: topDir, group: groupFromManifest, manifest: flatManifest });
      continue;
    }

    // Case 2: group folder (no own manifest, but has subdirs with manifest.json)
    const subEntries = await fs.readdir(topDir, { withFileTypes: true }).catch(() => []);
    let hasAny = false;
    for (const sub of subEntries) {
      if (!sub.isDirectory() || sub.name.startsWith('.')) continue;
      const subDir = path.join(topDir, sub.name);
      const subManifest = safeReadManifest(subDir);
      if (!subManifest) continue;
      hasAny = true;
      const id = `${e.name}/${sub.name}`;
      out.push({ id, name: sub.name, group: e.name, path: subDir, manifest: subManifest });
      _dirByIdCache.set(id, { dir: subDir, group: e.name, manifest: subManifest });
    }
    if (!hasAny) {
      // пустая группа или нет manifest-ов — пропускаем
    }
  }

  try { _dirCacheStamp = fssync.statSync(SCRAPERS_ROOT).mtimeMs; } catch { _dirCacheStamp = 0; }
  return out;
}

/**
 * Ищет абсолютный путь к scraper-у по его id. Поддерживает:
 *   - flat: id = folder name
 *   - nested: id = "group/folder"
 * Возвращает { dir, group } или null.
 */
function resolveScraperDir(scraperId) {
  if (!scraperId || typeof scraperId !== 'string') return null;
  if (scraperId.includes('/') || scraperId.includes('\\')) {
    // nested: ищем прямо в кэше или пробуем на файловой системе
    const cached = _dirByIdCache.get(scraperId);
    if (cached) return { dir: cached.dir, group: cached.group };
    // fallback: прямой путь от корня
    const direct = path.join(SCRAPERS_ROOT, scraperId);
    if (isInsideRoot(direct, SCRAPERS_ROOT) && fssync.existsSync(path.join(direct, 'manifest.json'))) {
      const group = scraperId.includes('/') ? scraperId.split('/')[0] : null;
      return { dir: direct, group };
    }
    return null;
  }
  // flat id
  const cached = _dirByIdCache.get(scraperId);
  if (cached) return { dir: cached.dir, group: cached.group };
  const flatDir = path.join(SCRAPERS_ROOT, scraperId);
  if (isInsideRoot(flatDir, SCRAPERS_ROOT) && fssync.existsSync(path.join(flatDir, 'manifest.json'))) {
    return { dir: flatDir, group: null };
  }
  return null;
}

export function registerScraperHandlers(ipcMain, { logger }) {
  const log = logger?.child?.({ mod: 'ipc:scrapers' }) || logger || console;

  // Ensure runs dir exists
  fssync.mkdirSync(RUNS_ROOT, { recursive: true });

  ipcMain.handle(IPC_SCRAPERS.LIST, async () => {
    return listScrapers();
  });

  ipcMain.handle(IPC_SCRAPERS.GET, async (_e, scraperId) => {
    if (!scraperId || typeof scraperId !== 'string') return null;
    const resolved = resolveScraperDir(scraperId);
    if (!resolved) return null;
    return safeReadManifest(resolved.dir);
  });

  ipcMain.handle(IPC_SCRAPERS.READ_TEXT, async (_e, relPath) => {
    if (!relPath || typeof relPath !== 'string') return { error: 'invalid path' };
    // Only allow reading inside SCRAPERS_ROOT
    const abs = path.isAbsolute(relPath) ? relPath : path.join(SCRAPERS_ROOT, relPath);
    if (!isInsideRoot(abs, SCRAPERS_ROOT)) return { error: 'path outside scrapers root' };
    if (!fssync.existsSync(abs)) return { error: 'file not found' };
    const stat = await fs.stat(abs);
    if (stat.size > 5 * 1024 * 1024) return { error: 'file too large (>5MB)' };
    return { content: await fs.readFile(abs, 'utf-8') };
  });

  ipcMain.handle(IPC_SCRAPERS.READ_JSONL, async (_e, { scraperId, file, limit = 50 }) => {
    if (!scraperId || !file) return { error: 'missing scraperId or file' };
    const resolved = resolveScraperDir(scraperId);
    if (!resolved) return { error: 'invalid scraper' };
    const abs = path.join(resolved.dir, file);
    if (!isInsideRoot(abs, resolved.dir)) return { error: 'path traversal blocked' };
    if (!fssync.existsSync(abs)) return { entries: [], count: 0 };
    const text = await fs.readFile(abs, 'utf-8');
    const lines = text.split('\n').filter(Boolean);
    const last = lines.slice(-Math.max(1, Math.min(1000, limit)));
    const entries = [];
    for (const l of last) {
      try { entries.push(JSON.parse(l)); } catch { /* skip malformed line */ }
    }
    return { entries, count: entries.length, totalLines: lines.length };
  });

  ipcMain.handle(IPC_SCRAPERS.OPEN_PATH, async (_e, relPath) => {
    if (!relPath || typeof relPath !== 'string') return { ok: false, error: 'invalid path' };
    const abs = path.isAbsolute(relPath) ? relPath : path.join(SCRAPERS_ROOT, relPath);
    if (!isInsideRoot(abs, SCRAPERS_ROOT)) return { ok: false, error: 'path outside scrapers root' };
    if (!fssync.existsSync(abs)) return { ok: false, error: 'path not found' };
    // require electron shell lazily
    try {
      const { shell } = await import('electron');
      const err = await shell.openPath(abs);
      if (err) return { ok: false, error: err };
      return { ok: true };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  });

  ipcMain.handle(IPC_SCRAPERS.RUN, async (_e, { scraperId, command }) => {
    if (!scraperId || !command) return { error: 'missing scraperId or command' };
    const resolved = resolveScraperDir(scraperId);
    if (!resolved) return { error: 'scraper not found' };
    const dir = resolved.dir;

    // If already running, refuse
    if (_running.has(scraperId)) {
      return { error: 'already running', pid: _running.get(scraperId).proc.pid };
    }

    // Write a fresh log file (sanitize scraperId for filename — replace / with __)
    fssync.mkdirSync(RUNS_ROOT, { recursive: true });
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const safeId = String(scraperId).replace(/[\\/:*?"<>|]/g, '__');
    const logFile = path.join(RUNS_ROOT, `${safeId}__${ts}.log`);

    // Run with cmd.exe on Windows so .cmd / .bat / npm.cmd shims work
    const isWin = process.platform === 'win32';
    const cmd = isWin ? 'cmd.exe' : '/bin/sh';
    const args = isWin
      ? ['/c', command]
      : ['-c', command];

    const proc = spawn(cmd, args, {
      cwd: dir,
      env: { ...process.env, MSB_SCRAPER_ID: scraperId, MSB_SCRAPER_DIR: dir },
      windowsHide: true,
    });

    const ws = fssync.createWriteStream(logFile, { flags: 'a' });
    ws.write(`> ${command}\n> cwd=${dir}\n> startedAt=${new Date().toISOString()}\n> pid=${proc.pid}\n\n`);
    proc.stdout.on('data', (d) => ws.write(d));
    proc.stderr.on('data', (d) => ws.write(d));
    proc.on('exit', (code, signal) => {
      ws.write(`\n> exited code=${code} signal=${signal} at=${new Date().toISOString()}\n`);
      ws.end();
      _running.delete(scraperId);
      log.info?.({ scraperId, code, signal }, 'scraper process exited');
    });

    _running.set(scraperId, { proc, logFile, startedAt: Date.now(), command });
    log.info?.({ scraperId, pid: proc.pid, logFile }, 'scraper started');
    return { ok: true, pid: proc.pid, logFile, startedAt: Date.now() };
  });

  ipcMain.handle(IPC_SCRAPERS.READ_OUTPUT, async (_e, { logFile, offset = 0, limit = 200 }) => {
    if (!logFile) return { error: 'missing logFile' };
    if (!isInsideRoot(logFile, RUNS_ROOT)) return { error: 'log path outside runs dir' };
    if (!fssync.existsSync(logFile)) return { content: '', size: 0, eof: true };
    const stat = fssync.statSync(logFile);
    const fh = fssync.openSync(logFile, 'r');
    try {
      const start = Math.max(0, Math.min(offset, stat.size));
      const len = Math.min(limit * 200, stat.size - start); // ~200 bytes/line
      const buf = Buffer.alloc(len);
      fssync.readSync(fh, buf, 0, len, start);
      return {
        content: buf.toString('utf-8'),
        size: stat.size,
        offset: start + len,
        eof: start + len >= stat.size,
      };
    } finally {
      fssync.closeSync(fh);
    }
  });

  ipcMain.handle(IPC_SCRAPERS.KILL, async (_e, { scraperId }) => {
    if (!scraperId) return { error: 'missing scraperId' };
    const sess = _running.get(scraperId);
    if (!sess) return { error: 'not running' };
    try {
      if (process.platform === 'win32') {
        spawn('taskkill', ['/PID', String(sess.proc.pid), '/T', '/F'], { stdio: 'ignore' });
      } else {
        sess.proc.kill('SIGTERM');
      }
      return { ok: true, pid: sess.proc.pid };
    } catch (err) {
      return { error: err.message };
    }
  });
}
