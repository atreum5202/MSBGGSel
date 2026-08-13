export function registerCookieRoutes({ app, cookieStore, browserLauncher, logger }) {
  app.get('/profiles/:id/cookies', async (req) => {
    const format = req.query?.format;
    try {
      const result = await cookieStore.exportRunning(req.params.id, browserLauncher, { format });
      logger?.debug({ profileId: req.params.id, format: result.format, count: result.count, via: 'rest' }, 'cookies exported via API');
      return result;
    } catch (e) {
      if (e.message.includes('not running')) {
        const snapshot = await cookieStore.loadSnapshot(req.params.id);
        if (snapshot) {
          return { format: 'json', data: snapshot, count: snapshot.length };
        }
      }
      throw e;
    }
  });

  app.post('/profiles/:id/cookies', async (req) => {
    const result = await cookieStore.importRunning(req.params.id, req.body || {}, browserLauncher);
    logger?.info({ profileId: req.params.id, imported: result.imported, via: 'rest' }, 'cookies imported via API');
    return result;
  });

  app.delete('/profiles/:id/cookies', async (req) => {
    const result = await cookieStore.clearRunning(req.params.id, browserLauncher);
    logger?.info({ profileId: req.params.id, cleared: result.cleared, via: 'rest' }, 'cookies cleared via API');
    return result;
  });
}
