import fs from 'node:fs/promises';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { MSB_DATA_DIR } from '../core/paths.js';

export class ProxyStore {
  constructor({ logger } = {}) {
    this.filePath = path.join(MSB_DATA_DIR, 'proxies.json');
    this.proxies = [];
    this.logger = logger?.child?.({ mod: 'proxyStore' }) || logger || console;
  }

  async init() {
    try {
      await fs.mkdir(MSB_DATA_DIR, { recursive: true });
      const data = await fs.readFile(this.filePath, 'utf8');
      this.proxies = JSON.parse(data);
      if (!Array.isArray(this.proxies)) {
        this.proxies = [];
      }
      this.logger.info(`Loaded ${this.proxies.length} proxies from store`);
    } catch (err) {
      if (err.code !== 'ENOENT') {
        this.logger.error({ err: err.message }, 'Failed to read proxies.json, initializing empty');
      }
      this.proxies = [];
      await this.save();
    }
  }

  list() {
    return this.proxies;
  }

  get(id) {
    return this.proxies.find(p => p.id === id) || null;
  }

  async add(data) {
    const host = data.host;
    const port = parseInt(data.port, 10);
    const protocol = data.protocol || 'socks5';
    
    // Check for duplicate
    const exists = this.proxies.some(p => p.host === host && parseInt(p.port, 10) === port);
    if (exists) {
      throw new Error(`Proxy ${host}:${port} already exists in pool`);
    }

    const item = {
      id: randomUUID(),
      protocol,
      host,
      port,
      username: data.username || undefined,
      password: data.password || undefined,
      label: data.label || undefined,
      addedAt: Date.now()
    };

    this.proxies.push(item);
    await this.save();
    return item;
  }

  async addBulk(data) {
    const lines = data?.lines;
    const proxies = data?.proxies;
    let added = 0;
    let skipped = 0;

    const itemsToProcess = [];

    // Parse lines if present
    if (lines && typeof lines === 'string') {
      const lineArray = lines.split(/\r?\n/);
      for (const line of lineArray) {
        const parsed = this.parseProxyLine(line);
        if (parsed) {
          itemsToProcess.push(parsed);
        }
      }
    }

    // Add proxies array if present
    if (Array.isArray(proxies)) {
      for (const p of proxies) {
        if (p && p.host && p.port) {
          itemsToProcess.push({
            protocol: p.protocol || 'socks5',
            host: p.host,
            port: parseInt(p.port, 10),
            username: p.username || undefined,
            password: p.password || undefined,
            label: p.label || undefined
          });
        }
      }
    }

    for (const item of itemsToProcess) {
      const existsInStore = this.proxies.some(p => p.host === item.host && parseInt(p.port, 10) === item.port);
      const existsInQueue = itemsToProcess.slice(0, itemsToProcess.indexOf(item)).some(p => p.host === item.host && parseInt(p.port, 10) === item.port);
      
      if (existsInStore || existsInQueue) {
        skipped++;
        continue;
      }

      this.proxies.push({
        id: randomUUID(),
        protocol: item.protocol,
        host: item.host,
        port: item.port,
        username: item.username,
        password: item.password,
        label: item.label,
        addedAt: Date.now()
      });
      added++;
    }

    if (added > 0) {
      await this.save();
    }

    return { added, skipped };
  }

  async remove(id) {
    const idx = this.proxies.findIndex(p => p.id === id);
    if (idx === -1) return false;
    this.proxies.splice(idx, 1);
    await this.save();
    return true;
  }

  async save() {
    try {
      await fs.writeFile(this.filePath, JSON.stringify(this.proxies, null, 2), 'utf8');
    } catch (err) {
      this.logger.error({ err: err.message }, 'Failed to save proxies.json');
      throw err;
    }
  }

  parseProxyLine(line) {
    if (!line || typeof line !== 'string') return null;
    const str = line.trim();
    if (!str) return null;

    const urlMatch = str.match(/^([a-zA-Z0-9]+):\/\/(?:([^:@]+)(?::([^@]+))?@)?([^:]+):(\d+)$/);
    if (urlMatch) {
      const protocol = urlMatch[1].toLowerCase();
      const username = urlMatch[2] || undefined;
      const password = urlMatch[3] || undefined;
      const host = urlMatch[4];
      const port = parseInt(urlMatch[5], 10);
      return { protocol, host, port, username, password };
    }

    const fourParts = str.split(':');
    if (fourParts.length === 4) {
      const host = fourParts[0];
      const port = parseInt(fourParts[1], 10);
      if (!isNaN(port)) {
        return {
          protocol: 'socks5',
          host,
          port,
          username: fourParts[2],
          password: fourParts[3]
        };
      }
    }

    if (fourParts.length === 2) {
      const host = fourParts[0];
      const port = parseInt(fourParts[1], 10);
      if (!isNaN(port)) {
        return {
          protocol: 'socks5',
          host,
          port
        };
      }
    }

    return null;
  }
}
