// TrashService — recycle bin (7-day retention by default).
//
// Owns:
//   - soft-delete a profile (move to .trash + write manifest)
//   - restore (move back + remove deletedAt markers)
//   - hard-delete (purge)
//   - list with retention countdown
//   - periodic sweep of expired entries
//
// Delegates all I/O to ProfileStorage.

import fs from 'node:fs/promises';
import path from 'node:path';
import { atomicWriteJson } from '../../../lib/atomicFs.js';
import { validateProfileShape } from '../../../lib/profileSchema.js';

export const TRASH_RETENTION_DAYS = 7;

export class TrashService {
  /**
   * @param {object} opts
   * @param {import('./ProfileStorage.js').ProfileStorage} opts.storage
   * @param {object} [opts.logger]
   * @param {number} [opts.retentionDays=7]
   */
  constructor({ storage, logger = null, retentionDays = TRASH_RETENTION_DAYS }) {
    this.storage = storage;
    this.logger = logger;
    this.retentionDays = retentionDays;
    this.retentionMs = retentionDays * 86400_000;
  }

  /**
   * Soft-delete a profile.
   * @param {object} profile — the in-memory profile object
   * @returns {Promise<{trashed: boolean, retentionDays: number, expiresAt: number} | null>}
   *   null if the profile was not found on disk.
   */
  async trash(profile) {
    if (!profile) return null;
    const id = profile.id;
    if (!await this.storage.moveToTrash(id)) return null;

    const now = Date.now();
    const trashed = {
      id,
      name: profile.name || null,
      number: profile.number ?? null,
      deletedAt: now,
      originalPath: this.storage.dir(id),
      meta: profile,
    };

    await atomicWriteJson(this._manifestPath(id), trashed);
    this.logger?.info?.({ profileId: id, retentionDays: this.retentionDays }, 'profile trashed');
    return { trashed: true, retentionDays: this.retentionDays, expiresAt: now + this.retentionMs };
  }

  /**
   * Restore a trashed profile.
   * @returns {Promise<boolean>} true on success, false if no trash entry.
   * @throws 409 if the live path is already occupied.
   */
  async restore(id) {
    if (!await this.storage.moveFromTrash(id)) return false;

    // Read the live meta.json, strip the trash markers, write back.
    const meta = await this.storage.readMeta(id);
    if (meta) {
      delete meta.deletedAt;
      delete meta.originalPath;
      await atomicWriteJson(this.storage.metaPath(id), meta);
    }
    this.logger?.info?.({ profileId: id }, 'profile restored from trash');
    return true;
  }

  /**
   * Permanently remove a trashed profile.
   */
  async purge(id) {
    const ok = await this.storage.removeFromTrash(id);
    if (ok) this.logger?.info?.({ profileId: id }, 'profile purged from trash');
    return ok;
  }

  /**
   * Enumerate every profile currently in the trash.
   * Each entry includes `id`, `name`, `number`, `deletedAt`, `expiresAt`, `daysLeft`.
   */
  async list() {
    const ids = await this.storage.listTrashDirs();
    const out = [];
    for (const id of ids) {
      let info = null;
      try {
        const raw = await fs.readFile(this._manifestPath(id), 'utf8');
        info = JSON.parse(raw);
      } catch {
        // Fallback: synthesise from meta.json if present
        try {
          const metaRaw = await fs.readFile(this.storage.metaPath(id), 'utf8');
          const meta = JSON.parse(metaRaw);
          info = { id, name: meta.name, number: meta.number, deletedAt: null };
        } catch {
          info = { id, name: null, number: null, deletedAt: null };
        }
      }
      const deletedAt = info.deletedAt || null;
      const expiresAt = deletedAt ? deletedAt + this.retentionMs : null;
      out.push({
        id,
        name: info.name || null,
        number: info.number ?? null,
        deletedAt,
        expiresAt,
        daysLeft: expiresAt != null ? Math.max(0, Math.ceil((expiresAt - Date.now()) / 86400_000)) : null,
        originalPath: info.originalPath || null,
      });
    }
    out.sort((a, b) => (b.deletedAt || 0) - (a.deletedAt || 0));
    return out;
  }

  /**
   * Hard-delete trash items past the retention window. Returns counts.
   */
  async purgeExpired() {
    const items = await this.list();
    const now = Date.now();
    let purged = 0;
    let kept = 0;
    for (const item of items) {
      if (item.expiresAt != null && item.expiresAt <= now) {
        await this.purge(item.id);
        purged++;
      } else {
        kept++;
      }
    }
    if (purged > 0) this.logger?.info?.({ purged, kept }, 'expired trash purged');
    return { purged, kept };
  }

  _manifestPath(id) {
    const path = require('node:path');
    return path.join(this.storage.trashPath(id), 'trash.json');
  }
}
