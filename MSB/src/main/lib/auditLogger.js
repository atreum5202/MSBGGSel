import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const AUDIT_DIR = process.env.MSB_AUDIT_DIR || path.join(os.homedir(), 'AppData', 'Roaming', 'MSB', 'audit');

class AuditLogger {
  constructor() {
    this.logs = [];
    this.maxInMemory = 1000;
    this.initialized = false;
  }

  async init() {
    if (this.initialized) return;
    try {
      await fs.mkdir(AUDIT_DIR, { recursive: true });
      this.initialized = true;
    } catch (e) {
      console.error('Failed to initialize audit logger:', e.message);
    }
  }

  async log(action, details) {
    const entry = {
      timestamp: new Date().toISOString(),
      action,
      details,
      pid: process.pid,
    };

    this.logs.push(entry);
    if (this.logs.length > this.maxInMemory) {
      this.logs.shift();
    }

    if (this.initialized) {
      const date = new Date().toISOString().split('T')[0];
      const logFile = path.join(AUDIT_DIR, `audit-${date}.log`);
      try {
        await fs.appendFile(logFile, JSON.stringify(entry) + '\n');
      } catch (e) {
        console.error('Failed to write audit log:', e.message);
      }
    }

    return entry;
  }

  async getLogs(filters = {}) {
    let filtered = [...this.logs];

    if (filters.action) {
      filtered = filtered.filter(log => log.action === filters.action);
    }
    if (filters.since) {
      filtered = filtered.filter(log => new Date(log.timestamp) >= new Date(filters.since));
    }
    if (filters.limit) {
      filtered = filtered.slice(-filters.limit);
    }

    return filtered;
  }

  async clearLogs(beforeDate) {
    if (!this.initialized) return;
    
    try {
      const files = await fs.readdir(AUDIT_DIR);
      for (const file of files) {
        if (file.startsWith('audit-') && file.endsWith('.log')) {
          const fileDate = file.slice(6, -4); // Extract date from filename
          if (fileDate < beforeDate) {
            const filePath = path.join(AUDIT_DIR, file);
            await fs.unlink(filePath);
          }
        }
      }
    } catch (e) {
      console.error('Failed to clear audit logs:', e.message);
    }
  }
}

export const auditLogger = new AuditLogger();
