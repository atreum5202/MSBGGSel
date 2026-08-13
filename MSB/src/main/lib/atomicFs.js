// Atomic filesystem operations.
//
// Why: writing JSON directly with `fs.writeFile(path, ...)` is NOT atomic —
// a crash, power loss, or kill -9 mid-write leaves a truncated / corrupt
// file. The `write-tmp + rename` pattern is the standard POSIX-safe way:
//
//   1. Write the new contents to "<path>.tmp.<random>"
//   2. fsync the tmp file (durability of the new bytes)
//   3. rename(tmp, real) — atomic on the same filesystem
//   4. fsync the parent directory (durability of the rename itself)
//
// On Windows, `fs.rename` is atomic on the same volume for files, so the
// same pattern works.
//
// All MSB profile persistence should go through these helpers — never
// `fs.writeFile` directly for `meta.json`, `index.json`, or `trash.json`.

import fs from 'node:fs/promises';
import fssync from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

/**
 * Atomically write a string or Buffer to `targetPath`.
 * Writes to "<targetPath>.tmp.<rand>" first, fsyncs, then renames.
 *
 * @param {string} targetPath
 * @param {string|Buffer|object} data — string/Buffer written verbatim, or object → JSON.stringify with 2-space indent
 * @param {object} [opts]
 * @param {number} [opts.mode=0o600] — POSIX mode (ignored on Windows)
 * @param {boolean} [opts.pretty=true] — JSON pretty-print when data is an object
 * @returns {Promise<void>}
 */
export async function atomicWriteFile(targetPath, data, opts = {}) {
  const mode = opts.mode ?? 0o600;
  const pretty = opts.pretty !== false;

  let bytes;
  if (typeof data === 'string' || Buffer.isBuffer(data)) {
    bytes = Buffer.isBuffer(data) ? data : Buffer.from(data, 'utf8');
  } else if (data === undefined) {
    bytes = Buffer.from('', 'utf8');
  } else {
    // Object → JSON
    bytes = Buffer.from(pretty ? JSON.stringify(data, null, 2) : JSON.stringify(data), 'utf8');
  }

  await fs.mkdir(path.dirname(targetPath), { recursive: true });

  const tmpPath = `${targetPath}.tmp.${crypto.randomBytes(6).toString('hex')}`;
  let fh;
  try {
    fh = await fs.open(tmpPath, 'w', mode);
    await fh.writeFile(bytes);
    // Force the kernel to flush to disk before the rename
    await fh.sync();
    await fh.close();
    fh = null;

    // Atomic on the same volume (POSIX rename, Windows MoveFileEx-style)
    await fs.rename(tmpPath, targetPath);

    // Best-effort fsync of the parent directory (POSIX-only, harmless on Windows).
    try {
      const dirFh = await fs.open(path.dirname(targetPath), 'r');
      try { await dirFh.sync(); } catch { /* some FS (Windows, FAT) don't support it */ }
      finally { await dirFh.close(); }
    } catch { /* ignore — durability of the directory entry is best-effort */ }
  } catch (err) {
    if (fh) {
      try { await fh.close(); } catch {}
    }
    // Best-effort cleanup of the tmp file
    try { await fs.unlink(tmpPath); } catch {}
    throw err;
  }
}

/**
 * Atomically write a JSON object. Shorthand for `atomicWriteFile(path, obj)`.
 * @param {string} targetPath
 * @param {object} obj
 * @param {object} [opts]
 */
export async function atomicWriteJson(targetPath, obj, opts = {}) {
  return atomicWriteFile(targetPath, obj, opts);
}

/**
 * Read a JSON file safely. Returns:
 *   - parsed object on success
 *   - null if the file does not exist
 *   - throws on parse error or other I/O error (caller decides)
 *
 * @param {string} targetPath
 * @param {object} [opts]
 * @param {*} [opts.fallback=null] — returned when file is missing
 * @returns {Promise<*>}
 */
export async function readJsonSafe(targetPath, { fallback = null } = {}) {
  let raw;
  try {
    raw = await fs.readFile(targetPath, 'utf8');
  } catch (err) {
    if (err.code === 'ENOENT') return fallback;
    throw err;
  }
  // Empty file → fallback (atomic-rename corruption can produce zero-byte
  // files if a crash happens *exactly* between unlink and rename; treat
  // that as missing data)
  if (!raw || raw.trim().length === 0) return fallback;
  return JSON.parse(raw);
}

/**
 * Atomic read-modify-write. Loads JSON, calls `mutator(obj)`, writes
 * atomically. `mutator` may return a new object or mutate in place.
 *
 * @param {string} targetPath
 * @param {(obj: any) => any | Promise<any>} mutator
 * @param {object} [opts]
 * @param {*} [opts.fallback={}] — initial value if file missing
 * @returns {Promise<any>} — the new value (whatever mutator returned)
 */
export async function atomicUpdateJson(targetPath, mutator, opts = {}) {
  const current = await readJsonSafe(targetPath, { fallback: opts.fallback ?? {} });
  const next = await mutator(current);
  await atomicWriteJson(targetPath, next === undefined ? current : next);
  return next === undefined ? current : next;
}

/**
 * Best-effort atomic read of binary/text file. Returns null on ENOENT,
 * throws on other errors.
 */
export async function readFileSafe(targetPath, { fallback = null, encoding = null } = {}) {
  try {
    return await fs.readFile(targetPath, encoding);
  } catch (err) {
    if (err.code === 'ENOENT') return fallback;
    throw err;
  }
}

/**
 * Ensure a directory exists. Returns the path.
 */
export async function ensureDir(dirPath) {
  await fs.mkdir(dirPath, { recursive: true });
  return dirPath;
}

/**
 * Test whether a path exists.
 */
export async function pathExists(p) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

// ─── Self-test (run on import in dev mode) ─────────────────────────────
// If the env var MSB_ATOMIC_FS_SELFTEST=1 is set, exercises a write-read-delete
// round trip. Useful for `node -e "import('./lib/atomicFs.js')..."` smoke tests.
const SELFTEST = process.env.MSB_ATOMIC_FS_SELFTEST === '1';
if (SELFTEST) {
  (async () => {
    const tmp = path.join(process.env.TEMP || '/tmp', `msb-atomic-selftest-${process.pid}.json`);
    const sample = { hello: 'world', n: 42, list: [1, 2, 3] };
    await atomicWriteJson(tmp, sample);
    const got = await readJsonSafe(tmp);
    const ok = JSON.stringify(got) === JSON.stringify(sample);
    await fs.unlink(tmp).catch(() => {});
    if (!ok) {
      console.error('atomicFs selftest FAILED:', { got, sample });
      process.exit(1);
    }
    console.log('atomicFs selftest OK');
  })().catch((e) => {
    console.error('atomicFs selftest threw:', e);
    process.exit(1);
  });
}
