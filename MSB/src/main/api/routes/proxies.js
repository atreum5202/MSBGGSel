export function registerProxyRoutes({ app, proxyStore, profileManager, logger }) {
  // GET /proxies - list all proxies
  app.get('/proxies', async (req, reply) => {
    try {
      const list = proxyStore.list();
      return { ok: true, data: list };
    } catch (err) {
      logger.error({ err: err.message }, 'Failed to list proxies');
      return reply.code(500).send({ ok: false, error: err.message });
    }
  });

  // POST /proxies - add one proxy
  app.post('/proxies', async (req, reply) => {
    try {
      const data = req.body || {};
      if (!data.host || !data.port) {
        return reply.code(400).send({ ok: false, error: 'Host and port are required' });
      }
      const item = await proxyStore.add(data);
      return { ok: true, data: item };
    } catch (err) {
      logger.error({ err: err.message }, 'Failed to add proxy');
      return reply.code(400).send({ ok: false, error: err.message });
    }
  });

  // POST /proxies/bulk - add bulk proxies
  app.post('/proxies/bulk', async (req, reply) => {
    try {
      const { lines, proxies } = req.body || {};
      const result = await proxyStore.addBulk({ lines, proxies });
      return { ok: true, data: result };
    } catch (err) {
      logger.error({ err: err.message }, 'Failed to add bulk proxies');
      return reply.code(400).send({ ok: false, error: err.message });
    }
  });

  // DELETE /proxies/:id - remove a proxy
  app.delete('/proxies/:id', async (req, reply) => {
    try {
      const { id } = req.params;
      const removed = await proxyStore.remove(id);
      if (!removed) {
        return reply.code(404).send({ ok: false, error: 'Proxy not found' });
      }
      return { ok: true, data: { success: true } };
    } catch (err) {
      logger.error({ err: err.message }, 'Failed to remove proxy');
      return reply.code(500).send({ ok: false, error: err.message });
    }
  });

  // POST /proxies/:id/assign/:profileId - assign proxy to profile
  app.post('/proxies/:id/assign/:profileId', async (req, reply) => {
    try {
      const { id, profileId } = req.params;
      const proxy = proxyStore.get(id);
      if (!proxy) {
        return reply.code(404).send({ ok: false, error: 'Proxy not found in pool' });
      }
      
      const profile = await profileManager.get(profileId);
      if (!profile) {
        return reply.code(404).send({ ok: false, error: 'Profile not found' });
      }

      const profileProxy = {
        protocol: proxy.protocol,
        host: proxy.host,
        port: proxy.port,
        username: proxy.username || undefined,
        password: proxy.password || undefined,
      };

      await profileManager.update(profileId, { proxy: profileProxy });
      logger.info({ profileId, proxyId: id }, 'Assigned proxy to profile');
      
      return { ok: true, data: { success: true } };
    } catch (err) {
      logger.error({ err: err.message }, 'Failed to assign proxy');
      return reply.code(500).send({ ok: false, error: err.message });
    }
  });
}
