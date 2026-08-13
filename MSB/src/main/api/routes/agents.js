const providers = ['chatgpt', 'claude', 'gemini', 'minimax'];
const latestResponses = new Map();
const responding = new Map();

export function registerAgentRoutes({ app, profileManager, browserLauncher, logger }) {
  app.get('/api/agents', async () => profileManager.list().map((profile) => toAgent(profile, browserLauncher)));

  app.post('/api/agents/:id/start', async (req) => {
    const profile = requireProfile(profileManager, req.params.id);
    await browserLauncher.start(profile, req.body || {});
    return toAgent(profileManager.get(req.params.id) || profile, browserLauncher);
  });

  app.post('/api/agents/:id/stop', async (req) => {
    await browserLauncher.stop(req.params.id);
    return { stopped: true };
  });

  app.post('/api/agents/:id/navigate', async (req) => {
    if (!req.body?.url) throw new Error('url required');
    await browserLauncher.goto(req.params.id, req.body.url);
    return { navigated: true };
  });

  app.post('/api/agents/:id/prompt', async (req) => {
    const profile = requireProfile(profileManager, req.params.id);
    const provider = inferProvider(profile);
    const text = req.body?.text || req.body?.prompt || '';
    if (!text) throw new Error('text required');
    responding.set(req.params.id, true);
    try {
      const result = await browserLauncher.runScenario(req.params.id, `${provider}-send`, { text, taskPackage: req.body?.taskPackage || null });
      const answer = typeof result?.text === 'string' ? result.text : JSON.stringify(result ?? {});
      latestResponses.set(req.params.id, answer);
      return { sent: true, provider, response: answer };
    } finally {
      responding.set(req.params.id, false);
    }
  });

  app.get('/api/agents/:id/response/latest', async (req) => ({ text: latestResponses.get(req.params.id) || '' }));
  app.get('/api/agents/:id/responding', async (req) => ({ responding: !!responding.get(req.params.id) }));

  app.post('/api/agents/:id/interrupt', async (req) => {
    const info = browserLauncher.getRunning(req.params.id);
    if (info?.page?.keyboard?.press) await info.page.keyboard.press('Escape').catch(() => {});
    responding.set(req.params.id, false);
    return { interrupted: true };
  });

  app.get('/api/providers/:name/health', async (req, reply) => {
    const name = String(req.params.name || '').toLowerCase();
    if (!providers.includes(name)) return reply.code(404).send({ ok: false, error: 'provider not found' });
    return {
      name,
      lastSuccessAt: 0,
      lastFailureAt: 0,
      consecutiveFailures: 0,
      totalAttempts: 0,
      totalSuccesses: 0,
      selectorVersions: { default: 1 },
    };
  });
}

function requireProfile(profileManager, id) {
  const profile = profileManager.get(id);
  if (!profile) throw new Error(`Profile ${id} not found`);
  return profile;
}

function toAgent(profile, browserLauncher) {
  return {
    id: String(profile.id),
    label: profile.name || String(profile.id),
    provider: inferProvider(profile),
    status: browserLauncher.isRunning(profile.id) ? (responding.get(profile.id) ? 'busy' : 'ready') : 'stopped',
  };
}

function inferProvider(profile) {
  const notes = (profile.notes || '').toLowerCase();
  const tagMatch = notes.match(/tag:\s*([^|]+)/i);
  const tags = tagMatch ? tagMatch[1].split(';').map(t => t.trim().toLowerCase()) : [];
  if (tags.includes('minimax')) return 'minimax';
  if (tags.includes('gemini')) return 'gemini';
  if (tags.includes('claude')) return 'claude';
  const value = `${profile.provider || ''} ${profile.startUrl || ''} ${profile.name || ''}`.toLowerCase();
  if (value.includes('claude')) return 'claude';
  if (value.includes('gemini') || value.includes('bard.google')) return 'gemini';
  if (value.includes('minimax') || value.includes('hailuoai')) return 'minimax';
  return 'chatgpt';
}
