export function registerStorageRoutes({ app, profileManager, browserLauncher, logger }) {
  // Get all storage data for a profile
  app.get('/profiles/:id/storage', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const isRunning = browserLauncher.isRunning(req.params.id);
    if (!isRunning) {
      return reply.code(400).send({ ok: false, error: 'Profile must be running to access storage' });
    }
    
    try {
      const runningInfo = browserLauncher.getRunning(req.params.id);
      const page = runningInfo?.page;
      
      if (!page) {
        return reply.code(400).send({ ok: false, error: 'No active page' });
      }
      
      // Get LocalStorage
      const localStorage = await page.evaluate(() => {
        const data = {};
        for (let i = 0; i < localStorage.length; i++) {
          const key = localStorage.key(i);
          data[key] = localStorage.getItem(key);
        }
        return data;
      });
      
      // Get SessionStorage
      const sessionStorage = await page.evaluate(() => {
        const data = {};
        for (let i = 0; i < sessionStorage.length; i++) {
          const key = sessionStorage.key(i);
          data[key] = sessionStorage.getItem(key);
        }
        return data;
      });
      
      // Get IndexedDB info (simplified)
      const indexedDB = await page.evaluate(() => {
        return new Promise((resolve) => {
          const databases = [];
          const request = indexedDB.databases();
          request.then((dbs) => {
            dbs.forEach(db => {
              databases.push({
                name: db.name,
                version: db.version,
              });
            });
            resolve(databases);
          }).catch(() => resolve([]));
        });
      });
      
      return { 
        ok: true, 
        data: {
          localStorage: { 
            keys: Object.keys(localStorage),
            size: JSON.stringify(localStorage).length,
            data: localStorage 
          },
          sessionStorage: { 
            keys: Object.keys(sessionStorage),
            size: JSON.stringify(sessionStorage).length,
            data: sessionStorage 
          },
          indexedDB: {
            databases,
            count: indexedDB.length,
          },
        }
      };
    } catch (e) {
      logger?.error({ profileId: req.params.id, err: e.message }, 'storage access failed');
      return reply.code(500).send({ ok: false, error: e.message });
    }
  });

  // Clear LocalStorage
  app.delete('/profiles/:id/storage/local', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const isRunning = browserLauncher.isRunning(req.params.id);
    if (!isRunning) {
      return reply.code(400).send({ ok: false, error: 'Profile must be running' });
    }
    
    try {
      const runningInfo = browserLauncher.getRunning(req.params.id);
      const page = runningInfo?.page;
      
      if (!page) {
        return reply.code(400).send({ ok: false, error: 'No active page' });
      }
      
      await page.evaluate(() => localStorage.clear());
      
      logger?.info({ profileId: req.params.id, via: 'rest' }, 'localStorage cleared via API');
      return { ok: true, cleared: true };
    } catch (e) {
      logger?.error({ profileId: req.params.id, err: e.message }, 'localStorage clear failed');
      return reply.code(500).send({ ok: false, error: e.message });
    }
  });

  // Clear SessionStorage
  app.delete('/profiles/:id/storage/session', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const isRunning = browserLauncher.isRunning(req.params.id);
    if (!isRunning) {
      return reply.code(400).send({ ok: false, error: 'Profile must be running' });
    }
    
    try {
      const runningInfo = browserLauncher.getRunning(req.params.id);
      const page = runningInfo?.page;
      
      if (!page) {
        return reply.code(400).send({ ok: false, error: 'No active page' });
      }
      
      await page.evaluate(() => sessionStorage.clear());
      
      logger?.info({ profileId: req.params.id, via: 'rest' }, 'sessionStorage cleared via API');
      return { ok: true, cleared: true };
    } catch (e) {
      logger?.error({ profileId: req.params.id, err: e.message }, 'sessionStorage clear failed');
      return reply.code(500).send({ ok: false, error: e.message });
    }
  });

  // Set LocalStorage item
  app.post('/profiles/:id/storage/local', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const { key, value } = req.body || {};
    if (!key || value === undefined) {
      return reply.code(400).send({ ok: false, error: 'key and value required' });
    }
    
    const isRunning = browserLauncher.isRunning(req.params.id);
    if (!isRunning) {
      return reply.code(400).send({ ok: false, error: 'Profile must be running' });
    }
    
    try {
      const runningInfo = browserLauncher.getRunning(req.params.id);
      const page = runningInfo?.page;
      
      if (!page) {
        return reply.code(400).send({ ok: false, error: 'No active page' });
      }
      
      await page.evaluate((k, v) => localStorage.setItem(k, v), key, String(value));
      
      logger?.info({ profileId: req.params.id, key, via: 'rest' }, 'localStorage item set via API');
      return { ok: true, set: true };
    } catch (e) {
      logger?.error({ profileId: req.params.id, err: e.message }, 'localStorage set failed');
      return reply.code(500).send({ ok: false, error: e.message });
    }
  });

  // Delete IndexedDB database
  app.delete('/profiles/:id/storage/indexeddb/:dbName', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const isRunning = browserLauncher.isRunning(req.params.id);
    if (!isRunning) {
      return reply.code(400).send({ ok: false, error: 'Profile must be running' });
    }
    
    try {
      const runningInfo = browserLauncher.getRunning(req.params.id);
      const page = runningInfo?.page;
      
      if (!page) {
        return reply.code(400).send({ ok: false, error: 'No active page' });
      }
      
      await page.evaluate((dbName) => {
        return new Promise((resolve, reject) => {
          const request = indexedDB.deleteDatabase(dbName);
          request.onsuccess = () => resolve(true);
          request.onerror = () => reject(request.error);
        });
      }, req.params.dbName);
      
      logger?.info({ profileId: req.params.id, dbName: req.params.dbName, via: 'rest' }, 'IndexedDB database deleted via API');
      return { ok: true, deleted: true };
    } catch (e) {
      logger?.error({ profileId: req.params.id, err: e.message }, 'IndexedDB delete failed');
      return reply.code(500).send({ ok: false, error: e.message });
    }
  });

  // Get History
  app.get('/profiles/:id/storage/history', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const isRunning = browserLauncher.isRunning(req.params.id);
    if (!isRunning) {
      return reply.code(400).send({ ok: false, error: 'Profile must be running' });
    }
    
    try {
      const runningInfo = browserLauncher.getRunning(req.params.id);
      const page = runningInfo?.page;
      
      if (!page) {
        return reply.code(400).send({ ok: false, error: 'No active page' });
      }
      
      const limit = req.query?.limit ? parseInt(req.query.limit) : 50;
      const history = await page.evaluate((lim) => {
        return new Promise((resolve) => {
          chrome.history.search({ text: '', maxResults: lim }, (results) => {
            resolve(results.map(item => ({
              url: item.url,
              title: item.title,
              lastVisitTime: item.lastVisitTime,
              visitCount: item.visitCount,
            })));
          });
        });
      }, limit);
      
      return { ok: true, data: { history, count: history.length } };
    } catch (e) {
      logger?.error({ profileId: req.params.id, err: e.message }, 'history access failed');
      return reply.code(500).send({ ok: false, error: e.message });
    }
  });

  // Clear History
  app.delete('/profiles/:id/storage/history', async (req, reply) => {
    const profile = profileManager.get(req.params.id);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });
    
    const isRunning = browserLauncher.isRunning(req.params.id);
    if (!isRunning) {
      return reply.code(400).send({ ok: false, error: 'Profile must be running' });
    }
    
    try {
      const runningInfo = browserLauncher.getRunning(req.params.id);
      const page = runningInfo?.page;
      
      if (!page) {
        return reply.code(400).send({ ok: false, error: 'No active page' });
      }
      
      await page.evaluate(() => {
        return new Promise((resolve) => {
          chrome.history.deleteAll(() => resolve(true));
        });
      });
      
      logger?.info({ profileId: req.params.id, via: 'rest' }, 'history cleared via API');
      return { ok: true, cleared: true };
    } catch (e) {
      logger?.error({ profileId: req.params.id, err: e.message }, 'history clear failed');
      return reply.code(500).send({ ok: false, error: e.message });
    }
  });
}
