import { DEFAULTS } from '../core/constants.js';

export class Supervisor {
  constructor({ browserLauncher, logger, maxRetries = DEFAULTS.SUPERVISOR_MAX_RETRIES, windowMs = DEFAULTS.SUPERVISOR_WINDOW_MS }) {
    this.browserLauncher = browserLauncher;
    this.logger = logger?.child?.({ mod: 'supervisor' }) || logger || console;
    this.maxRetries = maxRetries;
    this.windowMs = windowMs;
    this.tracked = new Map();
  }

  track(profile, options, context) {
    const entry = this.tracked.get(profile.id) || { profile, options, retries: [], stopping: false };
    entry.profile = profile;
    entry.options = options;
    entry.stopping = false;
    this.tracked.set(profile.id, entry);
    this.logger.debug?.({ profileId: profile.id }, 'supervisor tracking profile');
    context.on('close', () => this._onClose(profile.id));
  }

  markStopping(profileId) {
    const entry = this.tracked.get(profileId);
    if (entry) entry.stopping = true;
  }

  async _onClose(profileId) {
    const entry = this.tracked.get(profileId);
    if (!entry) return;
    if (entry.stopping) {
      this.tracked.delete(profileId);
      return;
    }
    const now = Date.now();
    entry.retries = entry.retries.filter((t) => now - t < this.windowMs);
    if (entry.retries.length >= this.maxRetries) {
      this.logger.error({ profileId, retries: entry.retries.length }, 'supervisor giving up (too many restarts)');
      this.tracked.delete(profileId);
      return;
    }
    entry.retries.push(now);
    const backoff = Math.min(30_000, 1000 * 2 ** entry.retries.length);
    this.logger.warn({ profileId, backoffMs: backoff }, 'restarting profile');
    await new Promise((r) => setTimeout(r, backoff));
    try {
      await this.browserLauncher.start(entry.profile, entry.options);
    } catch (err) {
      this.logger.error({ profileId, err: err.message }, 'restart failed');
    }
  }
}
