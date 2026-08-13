/**
 * automationService.js — единая точка для скрейперов.
 *
 * Что внутри:
 *   - profileCreate({ name, group, proxy, ... })   — создать профиль с прокси
 *   - profileRun(profileId, { headless, traffic, network })  — запустить профиль + опционально перехват
 *   - profileStop(profileId)                        — остановить
 *   - runPipeline({ steps: [...] })                 — выполнить цепочку шагов
 *   - jobStatus(jobId)                              — статус длинной задачи
 *
 * Pipeline step types:
 *   { type: 'create', name, group, proxy, ... }
 *   { type: 'start',  profileId, headless, traffic, network }
 *   { type: 'stop',   profileId }
 *   { type: 'wait',   ms }
 *   { type: 'http',   method, url, profileId?, body? }   — через CDP-сессию
 *   { type: 'eval',   profileId, code }                 — выполнить JS в странице
 *   { type: 'screenshot', profileId, path }
 *   { type: 'crawl',  profileId, urls, ... }            — Crawlee walker (см. crawlerService)
 *   { type: 'llm',    profileId, task, ... }            — browser-use walker (LLM)
 *
 * Возвращает jobId. Статус и лог можно читать через jobStatus(jobId) и jobLog(jobId).
 */

import { EventEmitter } from 'node:events';
import fs from 'node:fs/promises';
import fssync from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';

const MSB_APPDATA = process.env.MSB_APPDATA
  || path.join(os.homedir(), 'AppData', 'Roaming', 'MSB');
const JOBS_DIR = path.join(MSB_APPDATA, 'automation', 'jobs');

export class AutomationService extends EventEmitter {
  constructor({ profileManager, browserLauncher, trafficCapture, networkCapture, crawlerService, logger }) {
    super();
    this.profileManager = profileManager;
    this.browserLauncher = browserLauncher;
    this.trafficCapture = trafficCapture;
    this.networkCapture = networkCapture;
    this.crawlerService = crawlerService || null;
    this.logger = logger?.child?.({ mod: 'automation' }) || logger || console;

    /** @type {Map<string, { status, steps, currentStep, log, startedAt, finishedAt, error }>} */
    this._jobs = new Map();
    fssync.mkdirSync(JOBS_DIR, { recursive: true });
  }

  // ── Profile helpers ────────────────────────────────────────────────

  async profileCreate(spec) {
    if (!spec?.name) throw new Error('profileCreate: name required');
    const created = await this.profileManager.create({
      name: spec.name,
      group: spec.group || null,
      tags: spec.tags || [],
      proxy: spec.proxy || null,
      notes: spec.notes || null,
      provider: spec.provider || 'outlook',
      // other sensible defaults
      engine: spec.engine || 'auto',
      headless: spec.headless !== false,
    });
    return created;
  }

  async profileStart(profileId, { headless = true, traffic = false, network = false } = {}) {
    const profile = this.profileManager.get(profileId);
    if (!profile) throw new Error(`profileStart: profile ${profileId} not found`);
    const session = await this.browserLauncher.start(profile, { headless });
    if (traffic) {
      try { await this.trafficCapture?.start(profileId, {}); }
      catch (e) { this.logger.warn({ profileId, err: e.message }, 'traffic start failed'); }
    }
    // network log is always on automatically when a CDP page session attaches
    // (via the page:cdpSession event we wired earlier)
    return { started: true, session, trafficStarted: !!traffic };
  }

  async profileStop(profileId) {
    const out = { stopped: false, trafficStopped: false };
    try {
      // Stop traffic first
      if (this.trafficCapture?.status(profileId)?.active) {
        await this.trafficCapture.stop(profileId);
        out.trafficStopped = true;
      }
    } catch (e) { this.logger.warn({ err: e.message }, 'traffic stop failed'); }
    try {
      // Clear network buffer (cheap, leaves a clean slate for next run)
      try { this.networkCapture?.clear(profileId); } catch {}
      // Stop the browser
      const result = await this.browserLauncher.stop(profileId);
      out.stopped = !!result;
    } catch (e) { this.logger.warn({ err: e.message }, 'profile stop failed'); throw e; }
    return out;
  }

  // ── Pipeline ───────────────────────────────────────────────────────

  runPipeline(spec) {
    if (!spec?.steps?.length) throw new Error('runPipeline: steps required');
    const jobId = crypto.randomBytes(8).toString('hex');
    const job = {
      id: jobId,
      status: 'queued',
      steps: spec.steps,
      currentStep: -1,
      log: [],
      results: [],
      startedAt: Date.now(),
      finishedAt: null,
      error: null,
    };
    this._jobs.set(jobId, job);
    this.logger.info({ jobId, steps: spec.steps.length }, 'pipeline queued');
    // Run async
    setImmediate(() => this._runPipeline(jobId).catch((err) => {
      job.status = 'error';
      job.error = err.message;
      job.finishedAt = Date.now();
      this.logger.error({ jobId, err: err.message }, 'pipeline failed');
      this.emit('job:error', { jobId, err });
    }));
    return { jobId };
  }

  async _runPipeline(jobId) {
    const job = this._jobs.get(jobId);
    if (!job) return;
    job.status = 'running';
    this.emit('job:start', { jobId });

    for (let i = 0; i < job.steps.length; i += 1) {
      job.currentStep = i;
      const step = job.steps[i];
      const stepLog = (msg) => {
        const line = { ts: Date.now(), step: i, msg };
        job.log.push(line);
        if (job.log.length > 2000) job.log.splice(0, job.log.length - 2000);
        this.emit('job:log', { jobId, ...line });
        this.logger.info({ jobId, step: i, msg }, 'pipeline step');
      };

      try {
        stepLog(`▶ step[${i}] type=${step.type}`);
        const result = await this._runStep(step, stepLog);
        job.results[i] = { ok: true, result };
        stepLog(`✓ step[${i}] done`);
      } catch (err) {
        stepLog(`✗ step[${i}] error: ${err.message}`);
        job.results[i] = { ok: false, error: err.message };
        if (!step.continueOnError) {
          job.status = 'error';
          job.error = `step ${i} (${step.type}) failed: ${err.message}`;
          job.finishedAt = Date.now();
          this.emit('job:error', { jobId, err: err.message });
          return;
        }
      }
    }

    job.status = 'done';
    job.finishedAt = Date.now();
    this.logger.info({ jobId, durationMs: job.finishedAt - job.startedAt }, 'pipeline done');
    this.emit('job:done', { jobId });
  }

  async _runStep(step, log) {
    switch (step.type) {
      case 'create': {
        return await this.profileCreate(step);
      }
      case 'start': {
        return await this.profileStart(step.profileId, {
          headless: step.headless,
          traffic: step.traffic,
          network: step.network,
        });
      }
      case 'stop': {
        return await this.profileStop(step.profileId);
      }
      case 'wait': {
        const ms = Number(step.ms) || 0;
        await new Promise((r) => setTimeout(r, ms));
        log(`waited ${ms}ms`);
        return { waited: ms };
      }
      case 'http': {
        if (!this.browserLauncher?.httpViaProfile) {
          throw new Error('browserLauncher.httpViaProfile not available in this MSB build');
        }
        return await this.browserLauncher.httpViaProfile(step.profileId, {
          method: step.method || 'GET',
          url: step.url,
          headers: step.headers,
          body: step.body,
        });
      }
      case 'eval': {
        if (!this.browserLauncher?.evalInProfile) {
          throw new Error('browserLauncher.evalInProfile not available');
        }
        return await this.browserLauncher.evalInProfile(step.profileId, step.code);
      }
      case 'screenshot': {
        if (!this.browserLauncher?.screenshotProfile) {
          throw new Error('browserLauncher.screenshotProfile not available');
        }
        return await this.browserLauncher.screenshotProfile(step.profileId, step.path);
      }
      case 'traffic': {
        if (step.action === 'start')  return await this.trafficCapture?.start(step.profileId, step.opts || {});
        if (step.action === 'stop')   return await this.trafficCapture?.stop(step.profileId);
        if (step.action === 'status') return this.trafficCapture?.status(step.profileId);
        throw new Error(`traffic step: unknown action ${step.action}`);
      }
      case 'network': {
        // status without profileId → list all active captures
        if (step.action === 'status' && !step.profileId) {
          return { active: this.networkCapture?.sessions?.() ?? [] };
        }
        if (!step.profileId) throw new Error('network step: profileId required for this action');
        if (step.action === 'status')   return this.networkCapture?.status(step.profileId);
        if (step.action === 'clear')    return this.networkCapture?.clear(step.profileId);
        if (step.action === 'endpoints')return this.networkCapture?.endpoints(step.profileId, step.opts || {});
        if (step.action === 'requests') return this.networkCapture?.list(step.profileId, step.opts || {});
        if (step.action === 'har')      return this.networkCapture?.toHar(step.profileId, step.opts || {});
        throw new Error(`network step: unknown action ${step.action}`);
      }
      case 'crawl':
      case 'llm': {
        if (!this.crawlerService) {
          throw new Error('crawlerService not available in this MSB build');
        }
        return await this._awaitWalker(step, log);
      }
      default:
        throw new Error(`unknown step type: ${step.type}`);
    }
  }

  // ── Job accessors ─────────────────────────────────────────────────

  jobStatus(jobId) {
    const job = this._jobs.get(jobId);
    if (!job) return null;
    return {
      id: job.id,
      status: job.status,
      currentStep: job.currentStep,
      totalSteps: job.steps.length,
      results: job.results,
      error: job.error,
      startedAt: job.startedAt,
      finishedAt: job.finishedAt,
      durationMs: (job.finishedAt || Date.now()) - job.startedAt,
    };
  }

  /**
   * Запустить walker (crawlee/llm) и заблокировать pipeline-шаг до его завершения.
   * Лог walker'а прокидывается в pipeline-лог с префиксом [walker].
   */
  async _awaitWalker(step, log) {
    const mode = step.type === 'llm' ? 'llm' : 'crawlee';
    const spec = { ...step, mode };
    delete spec.type;
    const { jobId } = this.crawlerService.startWalker(spec);
    log(`walker started: jobId=${jobId} mode=${mode}`);

    return await new Promise((resolve, reject) => {
      const onDone = (msg) => {
        cleanup();
        const status = this.crawlerService.jobStatus(jobId);
        const results = this.crawlerService.jobResults(jobId, { limit: 50 });
        resolve({ jobId, status, resultsCount: status?.resultsCount, results: results?.results });
      };
      const onError = (msg) => {
        cleanup();
        const status = this.crawlerService.jobStatus(jobId);
        reject(new Error(msg?.err || `walker ${jobId} failed`));
      };
      const onLog = (msg) => {
        // Don't dump every line into the pipeline log — only forward key events.
        // For visibility we just prefix them.
        if (msg && msg.msg) log(`[walker] ${msg.msg}`);
      };
      const cleanup = () => {
        this.crawlerService.off('job:done', onDone);
        this.crawlerService.off('job:error', onError);
        this.crawlerService.off('job:log', onLog);
      };
      this.crawlerService.on('job:done', onDone);
      this.crawlerService.on('job:error', onError);
      this.crawlerService.on('job:log', onLog);

      // If the walker already finished by the time we subscribed, resolve now.
      const cur = this.crawlerService.jobStatus(jobId);
      if (cur && (cur.status === 'done' || cur.status === 'error')) {
        if (cur.status === 'done') onDone();
        else onError({ err: cur.error || 'walker failed' });
      }
    });
  }

  jobLog(jobId, { sinceTs = 0, limit = 200 } = {}) {
    const job = this._jobs.get(jobId);
    if (!job) return null;
    const lines = job.log.filter((l) => l.ts > sinceTs);
    return {
      jobId,
      count: lines.length,
      lines: lines.slice(-limit),
      lastTs: lines.length ? lines[lines.length - 1].ts : sinceTs,
    };
  }

  listJobs() {
    return Array.from(this._jobs.values())
      .sort((a, b) => b.startedAt - a.startedAt)
      .map((j) => this.jobStatus(j.id));
  }
}
