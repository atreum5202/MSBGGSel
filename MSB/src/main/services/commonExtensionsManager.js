import fs from 'node:fs/promises';
import { commonExtensionsFile } from '../core/paths.js';

export class CommonExtensionsManager {
  constructor({ profilesDir, logger }) {
    this.profilesDir = profilesDir;
    this.filePath = commonExtensionsFile(profilesDir);
    this.extensions = [];
    this.logger = logger?.child?.({ mod: 'commonExtensionsManager' }) || logger || console;
  }

  async init() {
    try {
      const raw = await fs.readFile(this.filePath, 'utf8');
      const data = JSON.parse(raw);
      this.extensions = Array.isArray(data.extensions) ? data.extensions : [];
      this.logger.info({ count: this.extensions.length }, 'common extensions loaded');
    } catch (err) {
      if (err.code !== 'ENOENT') {
        this.logger.warn({ err: err.message }, 'common-extensions.json unreadable, starting empty');
      }
      this.extensions = [];
    }
  }

  list() {
    return [...this.extensions];
  }

  async add(extPath) {
    const norm = extPath.trim().replace(/[\\/]+$/, '');
    if (this.extensions.includes(norm)) return { added: false, extensions: this.list() };
    this.extensions.push(norm);
    await this._save();
    this.logger.info({ extPath: norm }, 'common extension added');
    return { added: true, extensions: this.list() };
  }

  async remove(extPath) {
    const norm = extPath.trim().replace(/[\\/]+$/, '');
    const before = this.extensions.length;
    this.extensions = this.extensions.filter((p) => p !== norm);
    if (this.extensions.length === before) return { removed: false, extensions: this.list() };
    await this._save();
    this.logger.info({ extPath: norm }, 'common extension removed');
    return { removed: true, extensions: this.list() };
  }

  async clear() {
    this.extensions = [];
    await this._save();
    this.logger.info('common extensions cleared');
    return { extensions: [] };
  }

  async _save() {
    const tmp = `${this.filePath}.${process.pid}.${Date.now()}.tmp`;
    await fs.writeFile(tmp, JSON.stringify({ extensions: this.extensions }, null, 2));
    await fs.rename(tmp, this.filePath);
  }
}
