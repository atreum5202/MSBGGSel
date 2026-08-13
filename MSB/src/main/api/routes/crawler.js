/**
 * crawler.js — REST API для CrawlerService (Crawlee + browser-use walker).
 *
 *   GET  /automation/crawl/capabilities      — что доступно (crawlee / python / browser-use)
 *   POST /automation/crawl/start             — запустить walker
 *   GET  /automation/crawl/jobs              — список jobs (latest first)
 *   GET  /automation/crawl/jobs/:id          — статус одного job
 *   GET  /automation/crawl/jobs/:id/log      — лог (с sinceTs)
 *   GET  /automation/crawl/jobs/:id/results  — собранные данные
 *   POST /automation/crawl/jobs/:id/abort    — прервать walker
 *
 * Walker шаги также доступны как pipeline steps в /automation/pipeline/run
 * (см. routes/automation.js — type: "crawl" | "llm").
 */

const START_SCHEMA = {
  type: 'object',
  required: ['profileId'],
  properties: {
    profileId:   { type: 'string', description: 'ID запущенного профиля (его CDP используется)' },
    mode:        { type: 'string', enum: ['crawlee', 'llm'], default: 'crawlee' },
    // crawlee params
    urls:        { type: 'array', items: { type: 'string' }, description: 'Start URLs (crawlee)' },
    url:         { type: 'string', description: 'Single start URL — алиас для urls:[url]' },
    maxPages:    { type: 'number', default: 25 },
    maxDepth:    { type: 'number', default: 2 },
    linkPattern: { type: 'string', description: 'RegExp — фильтр ссылок' },
    globs:       { type: 'array', items: { type: 'string' } },
    linkSelector:{ type: 'string' },
    extract:     {
      type: 'object',
      description: '{ name?, selector, type: "text"|"html"|"attr", attr? }',
      properties: {
        name:     { type: 'string' },
        selector: { type: 'string' },
        type:     { type: 'string', enum: ['text', 'html', 'attr'] },
        attr:     { type: 'string' },
      },
    },
    outputFile:  { type: 'string' },
    // llm params
    task:        { type: 'string', description: 'Natural-language goal (browser-use)' },
    goal:        { type: 'string' },
    maxSteps:    { type: 'number', default: 30 },
    model:       { type: 'string', description: 'LLM model id (browser-use default if null)' },
  },
};

export function registerCrawlerRoutes({ app, crawler, logger }) {
  if (!crawler) {
    logger?.warn('CrawlerService not provided — /automation/crawl routes disabled');
    return;
  }

  app.get('/automation/crawl/capabilities', {
    schema: { summary: 'Что доступно прямо сейчас: crawlee, python, browser-use' },
  }, async () => {
    const caps = await crawler.capabilities();
    return { ok: true, data: caps };
  });

  app.post('/automation/crawl/start', {
    schema: {
      summary: 'Запустить walker (Crawlee или LLM/browser-use) на запущенном профиле',
      body: START_SCHEMA,
    },
  }, async (req) => {
    const { jobId } = crawler.startWalker(req.body);
    return { ok: true, data: { jobId } };
  });

  app.get('/automation/crawl/jobs', {
    schema: { summary: 'Список всех walker jobs (latest first)' },
  }, async () => ({ ok: true, data: crawler.listJobs() }));

  app.get('/automation/crawl/jobs/:id', {
    schema: { summary: 'Статус walker job' },
  }, async (req) => {
    const data = crawler.jobStatus(req.params.id);
    if (!data) return { ok: false, error: 'job not found' };
    return { ok: true, data };
  });

  app.get('/automation/crawl/jobs/:id/log', {
    schema: { summary: 'Лог walker job (с sinceTs)' },
  }, async (req) => {
    const since = Number(req.query?.sinceTs) || 0;
    const limit = Math.min(2000, Number(req.query?.limit) || 200);
    const data = crawler.jobLog(req.params.id, { sinceTs: since, limit });
    if (!data) return { ok: false, error: 'job not found' };
    return { ok: true, data };
  });

  app.get('/automation/crawl/jobs/:id/results', {
    schema: { summary: 'Собранные данные walker job' },
  }, async (req) => {
    const limit = Math.min(2000, Number(req.query?.limit) || 200);
    const data = crawler.jobResults(req.params.id, { limit });
    if (!data) return { ok: false, error: 'job not found' };
    return { ok: true, data };
  });

  app.post('/automation/crawl/jobs/:id/abort', {
    schema: { summary: 'Прервать running/queued walker job' },
  }, async (req) => {
    const r = crawler.abort(req.params.id);
    if (!r.ok) return { ok: false, error: r.error };
    return { ok: true, data: r };
  });

  logger?.info('crawler routes registered');
}
