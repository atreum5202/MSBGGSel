/**
 * automation.js — REST API для AutomationService.
 *
 *   POST /automation/profile/create   — создать профиль
 *   POST /automation/profile/start    — запустить + опционально перехват
 *   POST /automation/profile/stop     — остановить
 *   POST /automation/pipeline/run     — запустить цепочку шагов
 *   GET  /automation/jobs             — список всех jobs
 *   GET  /automation/jobs/:id         — статус одного job
 *   GET  /automation/jobs/:id/log     — лог (с sinceTs)
 */

export function registerAutomationRoutes({ app, automation, logger }) {
  if (!automation) {
    logger?.warn('AutomationService not provided — /automation routes disabled');
    return;
  }

  app.post('/automation/profile/create', {
    schema: {
      summary: 'Create a new profile (convenience for scrapers)',
      body: {
        type: 'object',
        required: ['name'],
        properties: {
          name:    { type: 'string' },
          group:   { type: 'string' },
          tags:    { type: 'array', items: { type: 'string' } },
          proxy:   { type: 'object' },
          notes:   { type: 'string' },
          provider:{ type: 'string' },
          engine:  { type: 'string' },
          headless:{ type: 'boolean' },
        },
      },
    },
  }, async (req) => ({ ok: true, data: await automation.profileCreate(req.body) }));

  app.post('/automation/profile/start', {
    schema: {
      summary: 'Start a profile and optionally enable traffic capture / network log',
      body: {
        type: 'object',
        required: ['profileId'],
        properties: {
          profileId: { type: 'string' },
          headless:  { type: 'boolean', default: true },
          traffic:   { type: 'boolean', default: false, description: 'Spawn mitmdump under the profile' },
          network:   { type: 'boolean', default: false, description: 'CDP ring buffer is on automatically' },
        },
      },
    },
  }, async (req) => ({ ok: true, data: await automation.profileStart(req.body.profileId, req.body) }));

  app.post('/automation/profile/stop', {
    schema: {
      summary: 'Stop a profile, stop its traffic capture, clear its network buffer',
      body: {
        type: 'object',
        required: ['profileId'],
        properties: { profileId: { type: 'string' } },
      },
    },
  }, async (req) => ({ ok: true, data: await automation.profileStop(req.body.profileId) }));

  app.post('/automation/pipeline/run', {
    schema: {
      summary: 'Run a multi-step pipeline (create, start, stop, wait, http, eval, screenshot, traffic, network, crawl, llm)',
      body: {
        type: 'object',
        required: ['steps'],
        properties: {
          steps: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                type:    { type: 'string' },
                // generic
                continueOnError: { type: 'boolean' },
                // 'create'
                name: { type: 'string' }, group: { type: 'string' }, proxy: { type: 'object' },
                // 'start' / 'stop'
                profileId: { type: 'string' },
                headless:  { type: 'boolean' },
                traffic:   { type: 'boolean' },
                network:   { type: 'boolean' },
                // 'wait'
                ms: { type: 'number' },
                // 'http'
                method: { type: 'string' }, url: { type: 'string' },
                headers: { type: 'object' }, body: { type: 'object' },
                // 'eval'
                code: { type: 'string' },
                // 'screenshot'
                path: { type: 'string' },
                // 'traffic' / 'network'
                action: { type: 'string' }, opts: { type: 'object' },
                // 'crawl' (Crawlee walker)
                urls:        { type: 'array', items: { type: 'string' } },
                url:         { type: 'string' },
                maxPages:    { type: 'number' },
                maxDepth:    { type: 'number' },
                linkPattern: { type: 'string' },
                globs:       { type: 'array', items: { type: 'string' } },
                linkSelector:{ type: 'string' },
                extract:     { type: 'object' },
                outputFile:  { type: 'string' },
                // 'llm' (browser-use walker)
                task:        { type: 'string' },
                goal:        { type: 'string' },
                maxSteps:    { type: 'number' },
                model:       { type: 'string' },
              },
            },
          },
        },
      },
    },
  }, async (req) => {
    const { jobId } = automation.runPipeline(req.body);
    return { ok: true, data: { jobId } };
  });

  app.get('/automation/jobs', async () => ({ ok: true, data: automation.listJobs() }));

  app.get('/automation/jobs/:id', async (req) => ({ ok: true, data: automation.jobStatus(req.params.id) }));

  app.get('/automation/jobs/:id/log', async (req) => {
    const since = Number(req.query?.sinceTs) || 0;
    const limit = Math.min(1000, Number(req.query?.limit) || 200);
    const data = automation.jobLog(req.params.id, { sinceTs: since, limit });
    if (!data) return { ok: false, error: 'job not found' };
    return { ok: true, data };
  });

  logger?.info('automation routes registered');
}
