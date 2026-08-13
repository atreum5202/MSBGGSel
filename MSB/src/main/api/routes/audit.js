import { auditLogger } from '../../lib/auditLogger.js';

export function registerAuditRoutes({ app, logger }) {
  // Initialize audit logger
  auditLogger.init();

  // Get audit logs
  app.get('/audit/logs', async (req, reply) => {
    const filters = {
      action: req.query?.action,
      since: req.query?.since,
      limit: req.query?.limit ? parseInt(req.query.limit) : 100,
    };

    const logs = await auditLogger.getLogs(filters);
    return { ok: true, data: { logs, count: logs.length } };
  });

  // Clear old audit logs
  app.delete('/audit/logs', async (req, reply) => {
    const beforeDate = req.body?.beforeDate || new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
    await auditLogger.clearLogs(beforeDate);
    logger?.info({ beforeDate }, 'audit logs cleared');
    return { ok: true, cleared: true };
  });

  // Get audit statistics
  app.get('/audit/stats', async (req, reply) => {
    const logs = await auditLogger.getLogs({ limit: 1000 });
    
    const stats = {
      total: logs.length,
      byAction: {},
      byHour: {},
      recent: logs.slice(-10),
    };

    logs.forEach(log => {
      stats.byAction[log.action] = (stats.byAction[log.action] || 0) + 1;
      
      const hour = new Date(log.timestamp).toISOString().slice(0, 13);
      stats.byHour[hour] = (stats.byHour[hour] || 0) + 1;
    });

    return { ok: true, data: stats };
  });
}
