// Health/readiness check.
//
// Returns:
//   { status: 'ok' | 'degraded' | 'down', checks: {...}, version, uptimeSec }
//
// `status === 'ok'`       — every check passed.
// `status === 'degraded'` — at least one non-critical check failed (e.g. optional engine missing).
// `status === 'down'`    — at least one critical check failed (e.g. profiles dir unwritable).
//
// Used by the renderer to show a status pill, and by external monitoring
// (Prometheus blackbox, etc.) via HTTP HEAD/GET.

import { promises as fs, statfsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { profileDir, paths } from '../../core/paths.js';
import { DEFAULTS } from '../../core/constants.js';
import { PROFILE_SCHEMA_VERSION } from '../../lib/profileSchema.js';

function getProfilesDir() {
  return paths.profiles;
}

/**
 * Synchronous disk space check (uses statfs).
 * Returns { freeBytes, totalBytes, usedPct } or { error }.
 */
function diskInfo() {
  try {
    const s = statfsSync(getProfilesDir());
    const freeBytes = s.bavail * s.bsize;
    const totalBytes = s.blocks * s.bsize;
    const usedPct = totalBytes > 0 ? Math.round(((totalBytes - s.bfree * s.bsize) / totalBytes) * 1000) / 10 : null;
    return { freeBytes, totalBytes, usedPct };
  } catch (err) {
    return { error: err.message };
  }
}

/**
 * Try to load engines via the lazy loader. Returns per-engine availability.
 *   engines: { patchright: 'ok' | 'missing', cloakbrowser: 'ok' | 'missing' }
 */
async function engineAvailability() {
  const out = {};
  try {
    const pr = await import('patchright');
    out.patchright = pr?.chromium ? 'ok' : 'missing';
  } catch (err) {
    out.patchright = `error: ${err.message?.slice(0, 80) || 'unknown'}`;
  }
  try {
    const cb = await import('cloakbrowser');
    out.cloakbrowser = cb?.launchPersistentContext ? 'ok' : 'missing';
  } catch (err) {
    out.cloakbrowser = `error: ${err.message?.slice(0, 80) || 'unknown'}`;
  }
  return out;
}

const START_TIME = Date.now();

export function registerHealthRoutes({ app, profileManager = null, browserLauncher = null }) {
  // Liveness: cheap, always-200
  app.get('/health/live', { schema: { summary: 'Liveness — process is up' } }, async () => ({
    status: 'ok',
    uptimeSec: Math.floor((Date.now() - START_TIME) / 1000),
  }));

  // Readiness: deeper checks
  app.get('/health', { schema: { summary: 'Readiness — engines, port, disk, profile dir' } }, async (req, reply) => {
    const checks = {};
    let down = false;
    let degraded = false;

    // 1. profilesDir writable — CRITICAL
    try {
      await fs.mkdir(getProfilesDir(), { recursive: true });
      const probe = path.join(getProfilesDir(), `.msb-health-${process.pid}-${Date.now()}`);
      await fs.writeFile(probe, 'ok', 'utf8');
      await fs.unlink(probe);
      checks.profilesDir = { status: 'ok', path: getProfilesDir() };
    } catch (err) {
      checks.profilesDir = { status: 'down', error: err.message };
      down = true;
    }

    // 2. disk space — DEGRADED if < 500 MB free, DOWN if < 50 MB
    const disk = diskInfo();
    if (disk.error) {
      checks.disk = { status: 'unknown', error: disk.error };
    } else {
      const freeMb = Math.floor(disk.freeBytes / 1_048_576);
      let s = 'ok';
      if (freeMb < 50) { s = 'down'; down = true; }
      else if (freeMb < 500) { s = 'degraded'; degraded = true; }
      checks.disk = { status: s, freeMb, totalMb: Math.floor(disk.totalBytes / 1_048_576), usedPct: disk.usedPct };
    }

    // 3. engines availability — DEGRADED if a non-default engine is missing
    //    (patchright is default; cloak is optional enhancement)
    const engines = await engineAvailability();
    const enginesStatus = (engines.patchright === 'ok') ? 'ok' : 'down';
    if (enginesStatus === 'down') down = true;
    if (engines.cloakbrowser !== 'ok') degraded = true;
    checks.engines = { status: enginesStatus, detail: engines };

    // 4. API port — informational only
    checks.api = {
      status: 'ok',
      host: DEFAULTS.API_HOST,
      port: Number(process.env.MSB_API_PORT || DEFAULTS.API_PORT),
      tokenProtected: !!process.env.MSB_API_TOKEN,
    };

    // 5. profile schema version — informational; we log if a migration is pending
    checks.schema = {
      status: 'ok',
      profileSchemaVersion: PROFILE_SCHEMA_VERSION,
      knownProfiles: profileManager?.cache?.size ?? null,
    };

    // 6. running browsers count
    if (browserLauncher) {
      const running = browserLauncher.status();
      checks.runningBrowsers = { status: 'ok', count: running.length };
    }

    // 7. memory
    const mem = process.memoryUsage();
    checks.memory = {
      status: 'ok',
      rssMb: Math.floor(mem.rss / 1_048_576),
      heapUsedMb: Math.floor(mem.heapUsed / 1_048_576),
    };

    const status = down ? 'down' : degraded ? 'degraded' : 'ok';
    if (status === 'down') reply.code(503);
    return {
      status,
      version: process.env.npm_package_version || 'unknown',
      uptimeSec: Math.floor((Date.now() - START_TIME) / 1000),
      pid: process.pid,
      hostname: os.hostname(),
      requestId: req.requestId,
      checks,
    };
  });
}
