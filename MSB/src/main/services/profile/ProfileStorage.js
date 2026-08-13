// ProfileStorage — low-level disk I/O for profiles.
//
// Owns:
//   - meta.json read/write (with schema migration on read)
//   - index.json read/write (atomic)
//   - directory layout (profilesDir/<id>/meta.json, profilesDir/.trash/<id>/...)
//   - orphan recovery (scan profilesDir for profiles not in index)
//
// Stateless beyond path constants. No cache, no business rules. The
// ProfileManager layer above this handles caching + CRUD semantics.

import fs from 'node:fs/promises';
import fssync from 'node:fs';
import path from 'node:path';
import { profileMetaFile, profileIndexFile, profileDir } from '../../../core/paths.js';
import { atomicWriteJson, readJsonSafe } from '../../../lib/atomicFs.js';
import { migrateProfile, PROFILE_SCHEMA_VERSION } from '../../../lib/profileSchema.js';

const INDEX_FILE = 'index.json';
const TRASH_DIR_NAME = '.trash';

/**
 * Single source of truth for the on-disk layout.
 */
export class ProfileStorage {
  /**
   * @param {object} opts
   * @param {string} opts.profilesDir
   * @param {object} [opts.logger]
   */
  constructor({ profilesDir, logger = null }) {
    this.profilesDir = profilesDir;
    this.logger = logger;
  }

  // ─── Paths ───────────────────────────────────────────────────────────────
  dir(id) {
    return profileDir(this.profilesDir, id);
  }

  metaPath(id) {
    return profileMetaFile(this.profilesDir, id);
  }

  indexPath() {
    return profileIndexFile(this.profilesDir);
  }

  trashDir() {
    return path.join(this.profilesDir, TRASH_DIR_NAME);
  }

  trashPath(id) {
    return path.join(this.trashDir(), id);
  }

  // ─── Index ───────────────────────────────────────────────────────────────
  /**
   * Read index.json. Returns null on missing/empty/corrupt.
   * Caller is responsible for recovery via scanOrphans().
   */
  async readIndex() {
    return readJsonSafe(this.indexPath());
  }

  /**
   * Write index.json atomically. Caller passes the in-memory view.
   */
  async writeIndex(index) {
    // Always stamp the current schema version on write.
    const body = { schemaVersion: PROFILE_SCHEMA_VERSION, ...index };
    await atomicWriteJson(this.indexPath(), body);
  }

  // ─── Meta ────────────────────────────────────────────────────────────────
  /**
   * Read a single profile's meta.json, applying schema migrations.
   * Returns null on missing. Throws on migration failure (caller logs +
   * skips).
   */
  async readMeta(id) {
    const meta = await readJsonSafe(this.metaPath(id));
    if (meta == null) return null;
    try {
      return migrateProfile(meta);
    } catch (err) {
      this.logger?.error?.({ profileId: id, err: err.message }, 'profile migration failed');
      return null;
    }
  }

  /**
   * Atomically write meta.json for a profile.
   */
  async writeMeta(profile) {
    const dir = this.dir(profile.id);
    await fs.mkdir(dir, { recursive: true });
    await atomicWriteJson(this.metaPath(profile.id), profile);
  }

  /**
   * Delete a profile's directory (recursive).
   */
  async remove(id) {
    await fs.rm(this.dir(id), { recursive: true, force: true });
  }

  // ─── Scan / recover ──────────────────────────────────────────────────────
  /**
   * Walk profilesDir and return every profile's meta.json that exists
   * on disk. Used to recover from a lost index.json.
   * Skips the .trash directory.
   */
  async scanOrphans() {
    const found = [];
    let entries;
    try {
      entries = await fs.readdir(this.profilesDir, { withFileTypes: true });
    } catch (err) {
      if (err.code === 'ENOENT') return found;
      throw err;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      if (entry.name === TRASH_DIR_NAME) continue;
      const meta = await this.readMeta(entry.name);
      if (meta && meta.id) found.push(meta);
    }
    return found;
  }

  /**
   * Ensure the profilesDir exists. Call once at startup.
   */
  async ensureDir() {
    await fs.mkdir(this.profilesDir, { recursive: true });
  }

  // ─── Trash primitives ────────────────────────────────────────────────────
  /**
   * Move a profile's directory to .trash/<id> using fs.rename (atomic
   * on same volume; falls back to copy+rm on cross-device).
   */
  async moveToTrash(id) {
    const src = this.dir(id);
    const dest = this.trashPath(id);
    if (!fssync.existsSync(src)) return false;
    await fs.mkdir(this.trashDir(), { recursive: true });
    if (fssync.existsSync(dest)) {
      await fs.rm(dest, { recursive: true, force: true });
    }
    try {
      await fs.rename(src, dest);
    } catch (err) {
      if (err.code === 'EXDEV') {
        await this._copyDir(src, dest);
        await fs.rm(src, { recursive: true, force: true });
      } else {
        throw err;
      }
    }
    return true;
  }

  /**
   * Move a trashed profile back to its live location. Refuses if the
   * live path already exists (caller must handle the conflict).
   * @returns {boolean} true on success, false if trash entry missing.
   * @throws on conflict (live path already occupied)
   */
  async moveFromTrash(id) {
    const src = this.trashPath(id);
    const dest = this.dir(id);
    if (!fssync.existsSync(src)) return false;
    if (fssync.existsSync(dest)) {
      const err = new Error(`Profile ${id} already exists at live path; cannot restore`);
      err.statusCode = 409;
      throw err;
    }
    try {
      await fs.rename(src, dest);
    } catch (err) {
      if (err.code === 'EXDEV') {
        await this._copyDir(src, dest);
        await fs.rm(src, { recursive: true, force: true });
      } else {
        throw err;
      }
    }
    return true;
  }

  /**
   * Permanently remove a trashed profile.
   */
  async removeFromTrash(id) {
    const dest = this.trashPath(id);
    if (!fssync.existsSync(dest)) return false;
    await fs.rm(dest, { recursive: true, force: true });
    return true;
  }

  /**
   * Enumerate trash entries (folder walk).
   */
  async listTrashDirs() {
    const trashDir = this.trashDir();
    if (!fssync.existsSync(trashDir)) return [];
    const entries = await fs.readdir(trashDir, { withFileTypes: true });
    return entries.filter((e) => e.isDirectory()).map((e) => e.name);
  }

  async _copyDir(src, dest) {
    await fs.mkdir(dest, { recursive: true });
    const entries = await fs.readdir(src, { withFileTypes: true });
    for (const e of entries) {
      const s = path.join(src, e.name);
      const d = path.join(dest, e.name);
      if (e.isDirectory()) {
        await this._copyDir(s, d);
      } else if (e.isFile()) {
        await fs.copyFile(s, d);
      }
    }
  }
}
