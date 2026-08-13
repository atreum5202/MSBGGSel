import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { generateFingerprint } from '../lib/fingerprint.js';
import { normalizeProxy } from '../lib/proxy.js';
import { profileDir, userDataDir, profileMetaFile, profileIndexFile } from '../core/paths.js';
import { DEFAULTS } from '../core/constants.js';
import { atomicWriteJson, readJsonSafe } from '../lib/atomicFs.js';
import { migrateProfile, PROFILE_SCHEMA_VERSION, validateProfileShape } from '../lib/profileSchema.js';

const INDEX_FILE = 'index.json';

// ─── Account helpers ──────────────────────────────────────────────────────────

/**
 * Определяет тип почты по email-адресу.
 * @param {string} email
 * @returns {'gmail'|'outlook'|'other'}
 */
function detectEmailType(email) {
  if (!email) return 'other';
  const lower = email.toLowerCase();
  if (lower.includes('@gmail.')) return 'gmail';
  if (lower.includes('@outlook.') || lower.includes('@hotmail.') || lower.includes('@live.') || lower.includes('@msn.')) return 'outlook';
  return 'other';
}

/**
 * Определяет provider по email-адресу (alias для detectEmailType).
 * @param {string} email
 * @returns {'gmail'|'outlook'|'other'}
 */
function detectProvider(email) {
  return detectEmailType(email);
}

/**
 * Парсит теги из строки notes (используется только при миграции старых профилей).
 * Формат: "... | Tag: Claude;Cursor;Minimax ..."
 * @param {string} notes
 * @returns {string[]}
 */
function parseTagsFromNotes(notes) {
  if (!notes) return [];
  const match = notes.match(/Tag:\s*([^|]+)/i);
  if (!match) return [];
  return match[1].split(';').map(t => t.trim()).filter(Boolean);
}

/**
 * Извлекает группу из строки notes при миграции старых профилей.
 * Формат: "... | Group: GGSeller ..."
 * @param {string} notes
 * @returns {string|null}
 */
function extractGroupFromNotes(notes) {
  if (!notes) return null;
  const match = notes.match(/Group:\s*([^|\n]+)/i);
  if (!match) return null;
  return match[1].trim() || null;
}

/**
 * Удаляет маркер Group из notes, оставляя остальной текст.
 * @param {string} notes
 * @returns {string}
 */
function stripGroupFromNotes(notes) {
  if (!notes) return '';
  return notes.replace(/\|?\s*Group:\s*[^|\n]+/gi, '').replace(/\s*\|\s*$/, '').trim();
}

/**
 * Строит объект account из полей профиля.
 * Если account уже задан явно — возвращает его (с дозаполнением пропущенных полей).
 * @param {object} profile
 * @param {object} [existingAccount]
 * @returns {object}
 */
function buildAccount(profile, existingAccount = null) {
  // Берём email: явно заданный → из name (убираем суффикс (Transferred))
  const emailFromName = (profile.name || '').replace(/\(Transferred\)/gi, '').trim();
  const isEmail = emailFromName.includes('@');

  const email = existingAccount?.email || (isEmail ? emailFromName : '');
  const type = existingAccount?.type || detectEmailType(email);
  const password = existingAccount?.password || 'Professor.2000';
  const tags = existingAccount?.tags || parseTagsFromNotes(profile.notes || '');
  const loginStatus = existingAccount?.loginStatus || 'unknown';

  return { email, type, password, tags, loginStatus };
}

// ─────────────────────────────────────────────────────────────────────────────

export class ProfileManager {
  constructor({ profilesDir, logger }) {
    this.profilesDir = profilesDir;
    this.cache = new Map();
    // nextNumber — следующий свободный номер для нового профиля.
    // Монотонно растёт, не переиспользуется после удаления.
    // Сбрасывается в index.json через _writeIndex().
    this.nextNumber = 1;
    this.logger = logger?.child?.({ mod: 'profileManager' }) || logger || console;
  }

  async init() {
    await fs.mkdir(this.profilesDir, { recursive: true });
    const indexPath = path.join(this.profilesDir, INDEX_FILE);
    // Safe JSON read: ENOENT / empty file → null (we'll recover by scanning).
    const index = await readJsonSafe(indexPath);
    if (index === null) {
      this.logger.warn({ indexPath }, 'index.json missing or empty - scanning profilesDir to recover');
    }

    // Восстанавливаем nextNumber из index.json (если был)
    if (index && Number.isInteger(index.nextNumber) && index.nextNumber > 0) {
      this.nextNumber = index.nextNumber;
    } else {
      // nextNumber не задан в index.json — вычисляем из max существующих.
      // Это безопасно даже до загрузки профилей, т.к. мы делаем второй проход
      // ниже (после _scanOrphanedProfiles).
      this.nextNumber = 1;
    }

    if (index && Array.isArray(index.profiles)) {
      for (const stub of index.profiles) {
        try {
          const meta = await this._readMeta(stub.id);
          this.cache.set(meta.id, meta);
        } catch (err) {
          this.logger.warn({ profileId: stub.id, err: err.message }, 'failed to load profile listed in index.json');
        }
      }
    }

    if (!index || this.cache.size === 0 || this.cache.size !== (index.profiles || []).length) {
      const recovered = await this._scanOrphanedProfiles();
      let recoveredCount = 0;
      for (const meta of recovered) {
        if (!this.cache.has(meta.id)) {
          this.cache.set(meta.id, meta);
          recoveredCount += 1;
        }
      }
      if (recoveredCount) {
        this.logger.warn({ recovered: recoveredCount }, 'recovered profiles from disk that were missing from index.json');
      }
    }

    if (!index || this.cache.size !== (index.profiles || []).length) {
      await this._writeIndex();
    }

    // Если nextNumber не пришёл из index.json (т.е. это старая база), вычисляем
    // его как max(существующих номеров) + 1. Это гарантирует, что новые
    // профили не пересекутся с уже занятыми.
    if (!(index && Number.isInteger(index.nextNumber) && index.nextNumber > 0)) {
      let maxN = 0;
      for (const p of this.cache.values()) {
        if (Number.isInteger(p.number) && p.number > maxN) maxN = p.number;
      }
      this.nextNumber = Math.max(1, maxN + 1);
    }

    // Миграция: дозаполняем account, group, tags, provider, number у старых профилей
    let migrated = 0;
    for (const [id, profile] of this.cache) {
      let changed = false;
      let next = { ...profile };

      // account — если нет, строим из профиля
      if (!next.account) {
        next.account = buildAccount(profile, null);
        changed = true;
      }

      // provider — если нет, определяем по email
      if (!next.provider) {
        next.provider = detectProvider(next.account?.email || '');
        changed = true;
      }

      // tags — если нет на верхнем уровне, берём из account.tags или notes
      if (!Array.isArray(next.tags)) {
        next.tags = Array.isArray(next.account?.tags) && next.account.tags.length > 0
          ? [...next.account.tags]
          : parseTagsFromNotes(next.notes || '');
        changed = true;
      }

      // group — если нет или уже null, пытаемся извлечь кастомный маркер из notes.
      // ВАЖНО: provider-значения ("gmail", "outlook", "other") НЕ являются кастомными группами
      // и никогда не должны попадать в group. group и provider — ортогональные оси.
      // group — инициализируем если нет, или переносим кастомный маркер из notes
      const groupFromNotes = extractGroupFromNotes(next.notes || '');
      const PROVIDER_VALUES = new Set(['gmail', 'outlook', 'other']);
      const isCustomGroup = groupFromNotes && !PROVIDER_VALUES.has(groupFromNotes.toLowerCase());

      if (groupFromNotes) {
        next.notes = stripGroupFromNotes(next.notes || '');
        changed = true;
      }

      if (next.group === undefined) {
        next.group = isCustomGroup ? groupFromNotes : null;
        changed = true;
      } else if (isCustomGroup) {
        next.group = groupFromNotes;
        changed = true;
      }

      if (changed) {
        next.updatedAt = Date.now();
        await this._writeMeta(next);
        this.cache.set(id, next);
        migrated++;
      }
    }

    // Миграция number: профили без number получают номер по порядку createdAt.
    // Это даёт стабильный, монотонный список — даже если профили создавались
    // в разное время, после миграции у всех есть явный номер.
    const needNumber = Array.from(this.cache.values())
      .filter((p) => !Number.isInteger(p.number) || p.number <= 0)
      .sort((a, b) => {
        const ca = a.createdAt || 0;
        const cb = b.createdAt || 0;
        return ca - cb;
      });

    if (needNumber.length > 0) {
      // Убедимся, что nextNumber не пересечётся с уже занятыми номерами
      const taken = new Set(
        Array.from(this.cache.values())
          .map((p) => p.number)
          .filter((n) => Number.isInteger(n) && n > 0)
      );
      while (taken.has(this.nextNumber)) {
        this.nextNumber += 1;
      }

      for (const p of needNumber) {
        const assigned = this.nextNumber;
        p.number = assigned;
        this.nextNumber += 1;
        p.updatedAt = Date.now();
        await this._writeMeta(p);
        migrated++;
        this.logger.info({ profileId: p.id, name: p.name, number: assigned }, 'assigned profile number during migration');
      }
      await this._writeIndex();
    }

    if (migrated > 0) {
      this.logger.info({ migrated }, 'migrated profiles: account/group/tags/provider/number fields updated');
    }

    this.logger.info({ count: this.cache.size, nextNumber: this.nextNumber, dir: this.profilesDir }, 'profile manager ready');
  }

  async _scanOrphanedProfiles() {
    const found = [];
    let entries;
    try {
      entries = await fs.readdir(this.profilesDir, { withFileTypes: true });
    } catch {
      return found;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      try {
        const meta = await this._readMeta(entry.name);
        if (meta && meta.id) found.push(meta);
      } catch {
        // ignore
      }
    }
    return found;
  }

  list() {
    const items = Array.from(this.cache.values());

    const sorted = items
      .map((p, idx) => ({ p, idx }))
      .sort((a, b) => {
        const ao = typeof a.p.sortOrder === 'number' ? a.p.sortOrder : -Infinity;
        const bo = typeof b.p.sortOrder === 'number' ? b.p.sortOrder : -Infinity;
        if (ao !== bo) return ao - bo;
        return a.idx - b.idx;
      })
      .map((x) => x.p);
    return sorted.map((p) => this._toListItem(p));
  }

  get(id) {
    return this.cache.get(id) || null;
  }

  async create(input = {}) {
    const id = input.id || randomUUID();
    const fingerprint = input.fingerprint || generateFingerprint({ platform: input.platform });

    const account = buildAccount(
      { name: input.name || '', notes: input.notes || '' },
      input.account || null,
    );

    // Назначаем number: если передан явно (например, при импорте) — берём его,
    // иначе — следующий из счётчика. Счётчик двигаем до max(текущий, переданный+1),
    // чтобы будущие номера не пересеклись с явно заданным.
    let number;
    if (Number.isInteger(input.number) && input.number > 0) {
      number = input.number;
      if (this._isNumberTaken(number, id)) {
        throw new Error(`Profile number ${number} is already taken by another profile`);
      }
      if (number >= this.nextNumber) this.nextNumber = number + 1;
    } else {
      // Пропускаем номера, которые заняты (например, при восстановлении после сбоя)
      while (this._isNumberTaken(this.nextNumber, id)) {
        this.nextNumber += 1;
      }
      number = this.nextNumber;
      this.nextNumber += 1;
    }

    const profile = {
      id,
      number,
      name: input.name || `Profile #${number}`,
      notes: input.notes || '',
      group: input.group !== undefined ? (input.group || null) : null,
      tags: Array.isArray(input.tags) ? input.tags : (Array.isArray(account.tags) ? account.tags : []),
      provider: detectProvider(account.email || ''),
      createdAt: Date.now(),
      updatedAt: Date.now(),
      engine: input.engine || 'auto',
      humanize: input.humanize !== false,
      aggressiveFingerprint: input.aggressiveFingerprint !== false,
      proxy: normalizeProxy(input.proxy || null),
      fingerprint,
      startUrl: input.startUrl || DEFAULTS.START_URL,
      extensions: Array.isArray(input.extensions) ? input.extensions : [],
      account,
    };

    await fs.mkdir(path.join(this._dir(id), 'userData'), { recursive: true });
    await this._writeMeta(profile);
    this.cache.set(id, profile);
    await this._writeIndex();
    this.logger.info({ profileId: id, number: profile.number, name: profile.name, engine: profile.engine }, 'profile created');
    return profile;
  }

  /**
   * Проверяет, занят ли номер каким-то профилем (кроме excludeId — это для update).
   * @param {number} number
   * @param {string} [excludeId]
   * @returns {boolean}
   */
  _isNumberTaken(number, excludeId = null) {
    for (const p of this.cache.values()) {
      if (p.id === excludeId) continue;
      if (Number.isInteger(p.number) && p.number === number) return true;
    }
    return false;
  }

  async update(id, patch) {
    const current = this.cache.get(id);
    if (!current) throw new Error(`Profile ${id} not found`);

    const fingerprintFromPatch = {};
    if (patch.timezone) fingerprintFromPatch.timezone = patch.timezone;
    if (patch.locale) fingerprintFromPatch.locale = patch.locale;
    if (patch.language && !patch.locale) fingerprintFromPatch.locale = patch.language;

    // Если пришёл патч account — мержим с текущим, пересчитываем тип если сменился email
    let account = current.account || buildAccount(current, null);
    if (patch.account) {
      account = { ...account, ...patch.account };
      // Пересчитываем тип если email изменился и тип не задан явно
      if (patch.account.email && !patch.account.type) {
        account.type = detectEmailType(patch.account.email);
      }
    }

    // Обновляем provider если изменился email (account.email или patch прямой)
    const newEmail = account.email || '';
    const updatedProvider = patch.provider !== undefined
      ? patch.provider
      : (patch.account?.email ? detectProvider(newEmail) : (current.provider || detectProvider(newEmail)));
    // Если сменилось имя — пересчитываем email в account (если email не задан явно)
    if (patch.name && patch.name !== current.name && !patch.account?.email) {
      const emailFromName = (patch.name || '').replace(/\(Transferred\)/gi, '').trim();
      if (emailFromName.includes('@')) {
        account = { ...account, email: emailFromName, type: detectEmailType(emailFromName) };
      }
    }

    // Валидация number: целое > 0, уникальное в пределах всех профилей (кроме текущего)
    let nextNumber = current.number;
    if ('number' in patch) {
      const requested = patch.number;
      if (requested !== null && !Number.isInteger(requested)) {
        throw new Error('number must be an integer or null');
      }
      if (requested !== null && requested <= 0) {
        throw new Error('number must be > 0');
      }
      if (requested !== null && this._isNumberTaken(requested, id)) {
        throw new Error(`Profile number ${requested} is already taken by another profile`);
      }
      nextNumber = requested;
      // Двигаем счётчик, чтобы не пересечься с явно заданным
      if (nextNumber !== null && nextNumber >= this.nextNumber) {
        this.nextNumber = nextNumber + 1;
      }
    }

    const next = {
      ...current,
      ...patch,
      id: current.id,
      number: nextNumber,
      createdAt: current.createdAt,
      updatedAt: Date.now(),
      // group: если явно передан null — снимаем, если строка — ставим, если не передан — сохраняем текущий
      group: 'group' in patch ? (patch.group || null) : (current.group || null),
      // tags: если передан массив — ставим, иначе сохраняем текущий
      tags: Array.isArray(patch.tags) ? patch.tags : (Array.isArray(current.tags) ? current.tags : []),
      provider: updatedProvider,
      proxy: patch.proxy !== undefined ? normalizeProxy(patch.proxy) : current.proxy,
      fingerprint: {
        ...current.fingerprint,
        ...fingerprintFromPatch,
        ...(patch.fingerprint || {}),
      },
      account,
    };
    await this._writeMeta(next);
    this.cache.set(id, next);
    await this._writeIndex();
    this.logger.debug({ profileId: id, fields: Object.keys(patch) }, 'profile updated');
    return next;
  }

  async remove(id) {
    if (!this.cache.has(id)) return false;
    this.cache.delete(id);
    await this._writeIndex();
    await fs.rm(this._dir(id), { recursive: true, force: true });
    this.logger.info({ profileId: id }, 'profile removed');
    return true;
  }

  // ─── Recycle bin (soft delete with restore window) ─────────────────────────
  // MoreLogin keeps trashed profiles for 7 days in a "Recycle Bin".
  // We mirror that:
  //   trash(id)        — move the profile to <profilesDir>/.trash/<id>/ and mark deletedAt
  //   restore(id)      — move it back
  //   purge(id)        — hard delete (used by automatic 7-day cleanup and explicit purge)
  //   listTrash()      — list every trashed profile
  //   purgeExpired()   — hard-delete trash items older than the retention window
  //
  // The original `remove()` is kept as hard-delete for callers that explicitly
  // want it (e.g. `bulk-delete` from the UI). New callers should use `trash()`
  // so the data can be recovered.

  static TRASH_DIR_NAME = '.trash';
  static TRASH_RETENTION_DAYS = 7;

  _trashDir() {
    return path.join(this.profilesDir, ProfileManager.TRASH_DIR_NAME);
  }

  _trashPath(id) {
    return path.join(this._trashDir(), id);
  }

  /**
   * Soft-delete a profile. Moves its on-disk folder to <profilesDir>/.trash/<id>
   * and records `deletedAt` + `originalPath` in meta.json so we can resurrect it.
   * The profile is removed from the live cache (so it stops showing in lists).
   */
  async trash(id) {
    const profile = this.get(id);
    if (!profile) return false;
    const src = this._dir(id);
    if (!existsSync(src)) return false;

    const trashDir = this._trashDir();
    await fs.mkdir(trashDir, { recursive: true });

    const metaPath = path.join(src, 'meta.json');
    let meta = null;
    try {
      const raw = await fs.readFile(metaPath, 'utf8');
      meta = JSON.parse(raw);
    } catch { /* meta may be missing; we still move the folder */ }

    const now = Date.now();
    const trashed = {
      id,
      name: profile.name || meta?.name || null,
      number: profile.number ?? null,
      deletedAt: now,
      originalPath: src,
      meta: meta || profile || null,
    };

    const dest = this._trashPath(id);
    // If something with the same id is already in trash, replace it (rare).
    if (existsSync(dest)) {
      await fs.rm(dest, { recursive: true, force: true });
    }
    // fs.rename is atomic on same volume; fall back to copy+rm if cross-device.
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

    // Persist the trash manifest so listTrash() doesn't need a folder walk.
    const manifestPath = path.join(dest, 'trash.json');
    await atomicWriteJson(manifestPath, trashed);

    this.cache.delete(id);
    await this._writeIndex();
    this.logger.info({ profileId: id, retentionDays: ProfileManager.TRASH_RETENTION_DAYS }, 'profile trashed');
    return { trashed: true, retentionDays: ProfileManager.TRASH_RETENTION_DAYS, expiresAt: now + ProfileManager.TRASH_RETENTION_DAYS * 86400_000 };
  }

  /**
   * Restore a trashed profile back to its original location.
   */
  async restore(id) {
    const src = this._trashPath(id);
    if (!existsSync(src)) return false;
    const dest = this._dir(id);
    if (existsSync(dest)) {
      // Conflict — refuse rather than overwrite live profile
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

    // Reload into cache from the restored meta.json
    const metaPath = path.join(dest, 'meta.json');
    let meta = null;
    try {
      const raw = await fs.readFile(metaPath, 'utf8');
      meta = JSON.parse(raw);
    } catch {}
    if (meta) {
      // Strip any leftover trash markers
      delete meta.deletedAt;
      delete meta.originalPath;
      await atomicWriteJson(metaPath, meta);
      this.cache.set(id, meta);
      await this._writeIndex();
    } else {
      // meta was wiped — fall back to a hard refresh from disk
      await this._readAll();
    }
    this.logger.info({ profileId: id }, 'profile restored from trash');
    return true;
  }

  /**
   * Permanently delete a trashed profile. Returns false if not in trash.
   */
  async purge(id) {
    const dest = this._trashPath(id);
    if (!existsSync(dest)) return false;
    await fs.rm(dest, { recursive: true, force: true });
    this.logger.info({ profileId: id }, 'profile purged from trash');
    return true;
  }

  /**
   * Enumerate every profile currently in the trash.
   * Each entry includes `id`, `name`, `number`, `deletedAt`, `expiresAt`, `originalPath`.
   */
  async listTrash() {
    const trashDir = this._trashDir();
    if (!existsSync(trashDir)) return [];
    const out = [];
    const entries = await fs.readdir(trashDir, { withFileTypes: true });
    const retentionMs = ProfileManager.TRASH_RETENTION_DAYS * 86400_000;
    for (const e of entries) {
      if (!e.isDirectory()) continue;
      const id = e.name;
      let info = null;
      const manifestPath = path.join(trashDir, id, 'trash.json');
      try {
        const raw = await fs.readFile(manifestPath, 'utf8');
        info = JSON.parse(raw);
      } catch {
        // Fallback: synthesise from meta.json if present
        try {
          const metaRaw = await fs.readFile(path.join(trashDir, id, 'meta.json'), 'utf8');
          const meta = JSON.parse(metaRaw);
          info = { id, name: meta.name, number: meta.number, deletedAt: null };
        } catch {
          info = { id, name: null, number: null, deletedAt: null };
        }
      }
      const deletedAt = info.deletedAt || null;
      const expiresAt = deletedAt ? deletedAt + retentionMs : null;
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
   * Hard-delete every trash item past the retention window. Returns counts.
   */
  async purgeExpired() {
    const items = await this.listTrash();
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
    if (purged > 0) this.logger.info({ purged, kept }, 'expired trash purged');
    return { purged, kept };
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

  userDataDir(id) {
    return userDataDir(this.profilesDir, id);
  }

  async exportJson(id) {
    const profile = this.get(id);
    if (!profile) throw new Error(`Profile ${id} not found`);
    return JSON.stringify(profile, null, 2);
  }

  async importJson(json) {
    const parsed = typeof json === 'string' ? JSON.parse(json) : json;
    delete parsed.id;
    parsed.name = parsed.name || 'Imported profile';
    return this.create(parsed);
  }

  _dir(id) {
    return profileDir(this.profilesDir, id);
  }

  async _readMeta(id) {
    const meta = await readJsonSafe(profileMetaFile(this.profilesDir, id));
    if (meta == null) return null;
    // Apply schema migrations if needed (v0 → v1 → ... → current).
    try {
      return migrateProfile(meta);
    } catch (err) {
      this.logger.error({ profileId: id, err: err.message }, 'profile migration failed — skipping');
      return null;
    }
  }

  async _writeMeta(profile) {
    const dir = this._dir(profile.id);
    await fs.mkdir(dir, { recursive: true });
    // Atomic JSON write: tmp-file + fsync + rename. Survives crash mid-write.
    await atomicWriteJson(profileMetaFile(this.profilesDir, profile.id), profile);
  }

  async _writeIndex() {
    const index = {
      schemaVersion: PROFILE_SCHEMA_VERSION,
      nextNumber: this.nextNumber,
      profiles: Array.from(this.cache.values()).map((p) => ({
        id: p.id,
        number: Number.isInteger(p.number) ? p.number : null,
        name: p.name,
        updatedAt: p.updatedAt,
      })),
    };
    // Atomic JSON write: tmp-file + fsync + rename. Survives crash mid-write.
    await atomicWriteJson(profileIndexFile(this.profilesDir), index);
  }

  _toListItem(p) {
    return {
      id: p.id,
      number: Number.isInteger(p.number) ? p.number : null,
      name: p.name,
      notes: p.notes,
      // ── Grouping & tagging ───────────────────────────────────────────────
      group: p.group || null,
      tags: Array.isArray(p.tags) ? p.tags : [],
      provider: p.provider || detectProvider(p.account?.email || ''),
      // ── Engine & fingerprint ─────────────────────────────────────────────
      engine: p.engine,
      humanize: p.humanize,
      aggressiveFingerprint: p.aggressiveFingerprint,
      hasProxy: !!p.proxy,
      proxyLabel: p.proxy ? `${p.proxy.protocol}://${p.proxy.host}:${p.proxy.port}` : null,
      proxy: p.proxy || null,
      fingerprint: {
        userAgent: p.fingerprint?.userAgent,
        platform: p.fingerprint?.platform,
        timezone: p.fingerprint?.timezone,
        locale: p.fingerprint?.locale,
        viewport: p.fingerprint?.viewport,
      },
      startUrl: p.startUrl,
      createdAt: p.createdAt,
      updatedAt: p.updatedAt,
      flagged: !!p.flagged,
      sortOrder: typeof p.sortOrder === 'number' ? p.sortOrder : null,
      // ── Account ──────────────────────────────────────────────────────────
      account: p.account || null,
    };
  }
}


