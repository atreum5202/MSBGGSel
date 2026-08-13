import fs from 'node:fs';
import path from 'node:path';
import { Writable } from 'node:stream';
import { EventEmitter } from 'node:events';
import pino from 'pino';
import PinoPretty from 'pino-pretty';
import { DEFAULTS } from './constants.js';

export class LogBroker extends EventEmitter {
  constructor({ ringSize = DEFAULTS.RING_SIZE } = {}) {
    super();
    this.setMaxListeners(0);
    this.ringSize = ringSize;
    this.appRing = [];
    this.rings = new Map();
  }

  push(scope, entry) {
    const ring = scope === 'app' ? this.appRing : this._ringFor(scope);
    ring.push(entry);
    if (ring.length > this.ringSize) ring.shift();
    this.emit(`log:${scope}`, entry);
    this.emit('log', { scope, entry });
  }

  history(scope) {
    if (scope === 'app') return [...this.appRing];
    return [...(this.rings.get(scope) || [])];
  }

  clear(scope) {
    if (scope === 'app') this.appRing = [];
    else this.rings.delete(scope);
  }

  _ringFor(id) {
    let r = this.rings.get(id);
    if (!r) {
      r = [];
      this.rings.set(id, r);
    }
    return r;
  }
}

export function createRootLogger({ dir, broker }) {
  fs.mkdirSync(dir, { recursive: true });
  const appFile = path.join(dir, 'app.log');
  const level = process.env.MSB_LOG_LEVEL || DEFAULTS.LOG_LEVEL;

  const streams = [
    { stream: pino.destination({ dest: appFile, sync: false, mkdir: true }) },
    { level, stream: makePrettyStream() },
    { stream: makeBrokerStream(broker, 'app') },
  ];

  const base = pino({ level }, pino.multistream(streams));
  base.info({ appLog: appFile }, 'root logger ready');

  base.forProfile = function forProfile(profile, { logsDir }) {
    fs.mkdirSync(logsDir, { recursive: true });
    const sessionFile = path.join(logsDir, `session-${Date.now()}.log`);
    const scoped = pino(
      { level, base: { profileId: profile.id, profileName: profile.name } },
      pino.multistream([
        { stream: pino.destination({ dest: sessionFile, sync: false, mkdir: true }) },
        { stream: makeBrokerStream(broker, profile.id) },
        { level, stream: makePrettyStream() },
      ])
    );
    scoped.sessionFile = sessionFile;
    return scoped;
  };

  return base;
}

function makePrettyStream() {
  try {
    return PinoPretty({
      colorize: true,
      translateTime: 'HH:MM:ss.l',
      ignore: 'pid,hostname',
    });
  } catch {
    return process.stdout;
  }
}

function makeBrokerStream(broker, scope) {
  return new Writable({
    write(chunk, _enc, cb) {
      const text = chunk.toString('utf8').trim();
      for (const line of text.split('\n')) {
        if (!line) continue;
        try {
          broker.push(scope, JSON.parse(line));
        } catch {
          broker.push(scope, { level: 30, time: Date.now(), msg: line });
        }
      }
      cb();
    },
  });
}
