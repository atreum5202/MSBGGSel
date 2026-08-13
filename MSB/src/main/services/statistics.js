import fs from 'node:fs/promises';
import path from 'node:path';
import { profileDir } from '../core/paths.js';

const empty = () => ({
  starts: 0,
  stops: 0,
  crashes: 0,
  restarts: 0,
  totalSessionMs: 0,
  longestSessionMs: 0,
  scenarios: {},
  lastStartedAt: null,
  lastEndedAt: null,
});

export class Statistics {
  constructor({ profilesDir, logger }) {
    this.profilesDir = profilesDir;
    this.mem = new Map();
    this.sessions = new Map();
    this.logger = logger?.child?.({ mod: 'statistics' }) || logger || console;
  }

  _file(id) {
    return path.join(profileDir(this.profilesDir, id), 'stats.json');
  }

  async _load(id) {
    if (this.mem.has(id)) return this.mem.get(id);
    try {
      const raw = await fs.readFile(this._file(id), 'utf8');
      const parsed = { ...empty(), ...JSON.parse(raw) };
      this.mem.set(id, parsed);
      return parsed;
    } catch {
      const fresh = empty();
      this.mem.set(id, fresh);
      return fresh;
    }
  }

  async _save(id) {
    const data = this.mem.get(id);
    if (!data) return;
    const p = this._file(id);
    try {
      await fs.mkdir(path.dirname(p), { recursive: true });
      await fs.writeFile(p, JSON.stringify(data, null, 2));
    } catch (err) {
      this.logger.warn({ profileId: id, err: err.message }, 'failed to persist statistics');
    }
  }

  async recordStart(id, { restarted = false } = {}) {
    const s = await this._load(id);
    s.starts += 1;
    if (restarted) s.restarts += 1;
    s.lastStartedAt = Date.now();
    this.sessions.set(id, Date.now());
    await this._save(id);
  }

  async recordStop(id, { crashed = false } = {}) {
    const s = await this._load(id);
    s.stops += 1;
    if (crashed) s.crashes += 1;
    const start = this.sessions.get(id);
    if (start) {
      const dur = Date.now() - start;
      s.totalSessionMs += dur;
      if (dur > s.longestSessionMs) s.longestSessionMs = dur;
      this.sessions.delete(id);
    }
    s.lastEndedAt = Date.now();
    await this._save(id);
  }

  async recordScenario(id, name, { success }) {
    const s = await this._load(id);
    const bucket = (s.scenarios[name] ||= { runs: 0, successes: 0, failures: 0 });
    bucket.runs += 1;
    if (success) bucket.successes += 1;
    else bucket.failures += 1;
    await this._save(id);
  }

  async get(id) {
    const s = await this._load(id);
    const avgSessionMs = s.stops > 0 ? Math.round(s.totalSessionMs / s.stops) : 0;
    return { ...s, avgSessionMs };
  }

  async summary() {
    const ids = Array.from(this.mem.keys());
    const rows = await Promise.all(ids.map((id) => this.get(id)));
    return {
      totalProfiles: rows.length,
      totalStarts: rows.reduce((n, r) => n + r.starts, 0),
      totalCrashes: rows.reduce((n, r) => n + r.crashes, 0),
      totalSessionMs: rows.reduce((n, r) => n + r.totalSessionMs, 0),
    };
  }
}
