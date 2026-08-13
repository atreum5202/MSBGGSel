/**
 * crawlerService.js — автономный walker поверх существующего CDP-профиля.
 *
 * Два режима:
 *   - crawlee:  полноценный crawler (Crawlee + Playwright) — обходит сайт по
 *               списку URL, линк-экстракция, queue, fingerprint-friendly.
 *               Подключается к уже запущенному профилю через connectOverCDP,
 *               не открывает второй Chromium.
 *   - llm:      LLM-driven walker (browser-use, Python sidecar) — получает
 *               текстовое задание, сам кликает / заполняет / собирает данные.
 *               Требует `browser-use` в Python (`pip install browser-use`).
 *
 * Архитектура:
 *   Crawlee подключается через playwright-core (уже в dependencies).
 *   Crawlee сам — optional dependency (npm install crawlee). Если его нет,
 *   сервис вернёт понятную ошибку при попытке запустить, но не уронит MSB.
 *
 *   browser-use — внешний Python-процесс. Детектим python + browser-use
 *   при инициализации, subprocess + JSON-line IPC.
 *
 * Каждый walker — асинхронная задача с jobId, статусом, логом, результатом.
 * Совместимо с AutomationService pipeline: pipeline step { type: 'crawl' | 'llm' }
 * маршрутизируется в этот сервис.
 */

import { EventEmitter } from 'node:events';
import fs from 'node:fs/promises';
import fssync from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';

const MSB_APPDATA = process.env.MSB_APPDATA
  || path.join(os.homedir(), 'AppData', 'Roaming', 'MSB');
const WALKER_DIR = path.join(MSB_APPDATA, 'walker', 'jobs');
const WALKER_SCRIPTS = path.join(MSB_APPDATA, 'walker', 'scripts');

// Cached module references (lazy import)
let _crawlee = null;
let _crawleeChecked = false;
let _playwright = null;
let _playwrightChecked = false;

async function tryImportCrawlee() {
  if (_crawleeChecked) return _crawlee;
  _crawleeChecked = true;
  try {
    const mod = await import('crawlee');
    _crawlee = mod;
    return _crawlee;
  } catch (err) {
    _crawlee = null;
    return null;
  }
}

async function tryImportPlaywright() {
  if (_playwrightChecked) return _playwright;
  _playwrightChecked = true;
  try {
    const mod = await import('playwright-core');
    _playwright = mod;
    return _playwright;
  } catch (err) {
    _playwright = null;
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// CrawlerService
// ─────────────────────────────────────────────────────────────────────────────

export class CrawlerService extends EventEmitter {
  constructor({ profileManager, browserLauncher, logger }) {
    super();
    this.profileManager = profileManager;
    this.browserLauncher = browserLauncher;
    this.logger = logger?.child?.({ mod: 'crawler' }) || logger || console;

    /** @type {Map<string, WalkerJob>} */
    this._jobs = new Map();
    fssync.mkdirSync(WALKER_DIR, { recursive: true });
    fssync.mkdirSync(WALKER_SCRIPTS, { recursive: true });

    // Cache Python / browser-use detection
    this._pythonCheck = null;
    this._browserUseCheck = null;
  }

  // ── Capability probes (cheap, run on demand) ──────────────────────────

  /**
   * Что у нас есть прямо сейчас: версии, доступность crawlee, python, browser-use.
   * UI / API использует это чтобы показать статус «готово / надо поставить».
   */
  async capabilities() {
    const crawlee = await tryImportCrawlee();
    const playwright = await tryImportPlaywright();
    const python = await this._detectPython();
    const browserUse = python.available ? await this._detectBrowserUse() : { available: false, reason: 'python missing' };

    return {
      crawlee: {
        available: !!crawlee,
        version: crawlee?.version || null,
        hint: crawlee ? null : 'npm install crawlee (или npm i в корне MSB — он в optionalDependencies)',
      },
      playwright: {
        available: !!playwright,
        version: playwright?.version || null,
      },
      python: {
        available: python.available,
        path: python.path || null,
        version: python.version || null,
        hint: python.available ? null : this._pythonHint(),
      },
      browserUse: {
        available: browserUse.available,
        version: browserUse.version || null,
        hint: browserUse.available ? null : 'pip install browser-use (требует Python 3.11+)',
      },
    };
  }

  _pythonHint() {
    return 'Установи Python 3.11+ (python.org / Microsoft Store)';
  }

  async _detectPython() {
    if (this._pythonCheck) return this._pythonCheck;
    const candidates = process.platform === 'win32'
      ? ['python', 'python3', 'py']
      : ['python3', 'python'];
    for (const cmd of candidates) {
      try {
        const out = await this._runOnce(cmd, ['--version'], { timeoutMs: 5000 });
        if (out.exitCode === 0) {
          this._pythonCheck = { available: true, path: cmd, version: out.stdout.trim() || out.stderr.trim() };
          return this._pythonCheck;
        }
      } catch { /* try next */ }
    }
    this._pythonCheck = { available: false, path: null, version: null };
    return this._pythonCheck;
  }

  async _detectBrowserUse() {
    if (this._browserUseCheck) return this._browserUseCheck;
    const py = await this._detectPython();
    if (!py.available) {
      this._browserUseCheck = { available: false, version: null, reason: 'python missing' };
      return this._browserUseCheck;
    }
    try {
      const out = await this._runOnce(py.path, ['-c', 'import browser_use; print(getattr(browser_use, "__version__", "unknown"))'], { timeoutMs: 10000 });
      if (out.exitCode === 0) {
        this._browserUseCheck = { available: true, version: out.stdout.trim() };
      } else {
        this._browserUseCheck = { available: false, version: null, reason: 'pip install browser-use' };
      }
    } catch (err) {
      this._browserUseCheck = { available: false, version: null, reason: err.message };
    }
    return this._browserUseCheck;
  }

  _runOnce(cmd, args, { timeoutMs = 5000 } = {}) {
    return new Promise((resolve, reject) => {
      let proc;
      try {
        proc = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
      } catch (err) {
        return reject(err);
      }
      let stdout = '';
      let stderr = '';
      const timer = setTimeout(() => {
        try { proc.kill('SIGKILL'); } catch {}
        reject(new Error(`timeout after ${timeoutMs}ms`));
      }, timeoutMs);
      proc.stdout.on('data', (d) => { stdout += d.toString(); });
      proc.stderr.on('data', (d) => { stderr += d.toString(); });
      proc.on('error', (err) => { clearTimeout(timer); reject(err); });
      proc.on('close', (code) => {
        clearTimeout(timer);
        resolve({ exitCode: code, stdout, stderr });
      });
    });
  }

  // ── Job lifecycle ──────────────────────────────────────────────────────

  /**
   * Запустить walker. mode='crawlee' или 'llm'.
   *
   * spec:
   *   profileId          — обязателен. Должен быть уже запущен.
   *   urls               — string[] (для crawlee). Берутся как startUrls.
   *   task               — string (для llm). Текстовое задание.
   *   goal               — string (для llm, optional). Что собрать.
   *   maxPages           — number (crawlee, default 25)
   *   maxDepth           — number (crawlee, default 2)
   *   linkPattern        — string (crawlee, optional. RegExp)
   *   extract            — object (crawlee, optional. {selector, attr, type})
   *   outputFile         — string (optional. Куда писать JSONL результат)
   *   continueOnError    — bool
   */
  startWalker(spec) {
    if (!spec?.profileId) throw new Error('startWalker: profileId required');
    const mode = spec.mode || 'crawlee';
    if (mode !== 'crawlee' && mode !== 'llm') {
      throw new Error(`startWalker: unknown mode "${mode}" (use "crawlee" or "llm")`);
    }

    const profile = this.profileManager?.get?.(spec.profileId);
    if (!profile) throw new Error(`startWalker: profile ${spec.profileId} not found`);

    const running = this.browserLauncher?.getRunning?.(spec.profileId);
    if (!running?.cdpEndpoint) {
      throw new Error(`startWalker: profile ${spec.profileId} is not running. Start it first via POST /profiles/${spec.profileId}/start`);
    }

    const jobId = crypto.randomBytes(8).toString('hex');
    const job = {
      id: jobId,
      mode,
      status: 'queued',
      profileId: spec.profileId,
      cdpEndpoint: running.cdpEndpoint,
      spec,
      log: [],
      results: [],
      startedAt: Date.now(),
      finishedAt: null,
      error: null,
      childPid: null,
      aborted: false,
    };
    this._jobs.set(jobId, job);

    this.logger.info({ jobId, mode, profileId: spec.profileId }, 'walker queued');
    setImmediate(() => this._runWalker(jobId).catch((err) => {
      job.status = 'error';
      job.error = err.message;
      job.finishedAt = Date.now();
      this.logger.error({ jobId, err: err.message }, 'walker failed');
      this.emit('job:error', { jobId, err: err.message });
    }));

    return { jobId };
  }

  async _runWalker(jobId) {
    const job = this._jobs.get(jobId);
    if (!job) return;
    job.status = 'running';
    this.emit('job:start', { jobId });

    const log = (msg) => {
      const line = { ts: Date.now(), msg };
      job.log.push(line);
      if (job.log.length > 5000) job.log.splice(0, job.log.length - 5000);
      this.emit('job:log', { jobId, ...line });
      this.logger.info({ jobId, msg }, 'walker');
    };

    try {
      if (job.mode === 'crawlee') {
        await this._runCrawlee(job, log);
      } else if (job.mode === 'llm') {
        await this._runLlm(job, log);
      } else {
        throw new Error(`unknown mode: ${job.mode}`);
      }
      job.status = 'done';
      job.finishedAt = Date.now();
      this.logger.info({ jobId, durationMs: job.finishedAt - job.startedAt, results: job.results.length }, 'walker done');
      this.emit('job:done', { jobId });
    } catch (err) {
      job.status = 'error';
      job.error = err.message;
      job.finishedAt = Date.now();
      this.logger.error({ jobId, err: err.message }, 'walker failed');
      this.emit('job:error', { jobId, err: err.message });
    }
  }

  async _runCrawlee(job, log) {
    const crawlee = await tryImportCrawlee();
    const playwright = await tryImportPlaywright();
    if (!crawlee) {
      throw new Error('crawlee is not installed. Run: npm install crawlee  (или npm i — он optional dependency)');
    }
    if (!playwright) {
      throw new Error('playwright-core is not available (should be in dependencies)');
    }

    const { profileId, spec, cdpEndpoint } = job;
    const startUrls = Array.isArray(spec.urls) && spec.urls.length
      ? spec.urls
      : (spec.url ? [spec.url] : []);
    if (!startUrls.length) throw new Error('crawlee walker: urls[] required');
    const maxPages = Number(spec.maxPages) || 25;
    const maxDepth = Number(spec.maxDepth) || 2;
    const linkPattern = spec.linkPattern ? new RegExp(spec.linkPattern) : null;

    log(`connecting to CDP ${cdpEndpoint}`);
    const browser = await playwright.chromium.connectOverCDP(cdpEndpoint);
    log(`connected to profile ${profileId} via CDP`);

    // Storage dir for this job
    const jobDir = path.join(WALKER_DIR, job.id);
    await fs.mkdir(jobDir, { recursive: true });

    // Copy storage state (cookies + localStorage) from the existing default
    // context into the new crawlee context so the walker starts authenticated
    // the same way the profile does. Without this, crawlee spawns a clean
    // context and would have to log in again.
    let storageState = null;
    try {
      const contexts = browser.contexts();
      const defaultCtx = contexts[0];
      if (defaultCtx) {
        storageState = await defaultCtx.storageState();
        log(`imported storage state: ${(storageState.cookies || []).length} cookies, ${(storageState.origins || []).length} origins`);
      }
    } catch (err) {
      log(`storage state import failed (non-fatal): ${err.message}`);
    }

    let visited = 0;
    const seen = new Set(startUrls);

    try {
      // Crawlee 3.x: share the CDP browser via browserPoolOptions, disable
      // fingerprint generation (profile already has one), seed with storage
      // state from the existing context.
      const crawler = new crawlee.PlaywrightCrawler({
        browserPoolOptions: {
          browser,
          // No fingerprint injection — profile already carries one.
          useFingerprints: false,
          // Seed new crawlee contexts with cookies + localStorage from profile.
          contextOptions: storageState ? { storageState } : undefined,
        },
        maxRequests: maxPages,
        maxRequestRetries: 0,
        navigationTimeoutSecs: 60,
        requestHandlerTimeoutSecs: 120,
        // Don't auto-save to dataset, we collect manually.
        failedRequestHandler: ({ request, error }) => {
          log(`✗ ${request.url} — ${error?.message || 'failed'}`);
        },
        async requestHandler({ page, request, enqueueLinks }) {
          visited += 1;
          log(`→ ${request.url} (depth ${request.userData?.depth ?? 0})`);

          // Wait for content
          try { await page.waitForLoadState('domcontentloaded', { timeout: 15000 }); } catch {}

          // Optional extraction
          if (spec.extract) {
            const ex = spec.extract;
            let data;
            try {
              if (ex.type === 'text') {
                data = await page.locator(ex.selector).first().innerText({ timeout: 5000 }).catch(() => null);
              } else if (ex.type === 'html') {
                data = await page.locator(ex.selector).first().innerHTML({ timeout: 5000 }).catch(() => null);
              } else if (ex.type === 'attr') {
                data = await page.locator(ex.selector).first().getAttribute(ex.attr || 'href', { timeout: 5000 }).catch(() => null);
              } else {
                // Default: snapshot meta + title + url
                const meta = await page.evaluate(() => ({
                  title: document.title,
                  description: document.querySelector('meta[name="description"]')?.content || null,
                  h1: Array.from(document.querySelectorAll('h1')).map((h) => h.innerText).slice(0, 5),
                }));
                data = meta;
              }
              if (data != null) {
                const rec = { url: request.url, capturedAt: Date.now(), extract: ex.name || 'data', value: data };
                job.results.push(rec);
                log(`  ✓ extracted ${ex.name || 'data'}: ${typeof data === 'string' ? data.slice(0, 80) : JSON.stringify(data).slice(0, 80)}`);
              }
            } catch (err) {
              log(`  ✗ extract failed: ${err.message}`);
            }
          } else {
            // Default: record title + canonical
            try {
              const meta = await page.evaluate(() => ({
                title: document.title,
                canonical: document.querySelector('link[rel="canonical"]')?.href || null,
              }));
              job.results.push({ url: request.url, capturedAt: Date.now(), ...meta });
            } catch {}
          }

          // Link following
          if ((request.userData?.depth ?? 0) < maxDepth) {
            await enqueueLinks({
              globs: spec.globs || undefined,
              selector: spec.linkSelector || undefined,
              transformRequest: (req) => {
                if (linkPattern && !linkPattern.test(req.url)) return null;
                if (seen.has(req.url)) return null;
                seen.add(req.url);
                req.userData = { ...(req.userData || {}), depth: (request.userData?.depth ?? 0) + 1 };
                return req;
              },
            });
          }

          if (job.aborted) throw new Error('walker aborted by user');
        },
      });

      await crawler.run(startUrls.map((u) => ({ url: u, userData: { depth: 0 } })));

      // Optional: write results JSONL
      if (spec.outputFile) {
        const out = path.isAbsolute(spec.outputFile)
          ? spec.outputFile
          : path.join(jobDir, spec.outputFile);
        await fs.writeFile(out, job.results.map((r) => JSON.stringify(r)).join('\n') + '\n', 'utf8');
        log(`saved ${job.results.length} records to ${out}`);
      }

      log(`done: visited=${visited} results=${job.results.length}`);
    } finally {
      try { await browser.close(); } catch (e) { /* CDP browser — closing just disconnects */ }
    }
  }

  async _runLlm(job, log) {
    const py = await this._detectPython();
    if (!py.available) throw new Error(`python not found: ${this._pythonHint()}`);
    const bu = await this._detectBrowserUse();
    if (!bu.available) throw new Error(bu.reason || 'browser-use not installed');

    const { profileId, cdpEndpoint, spec } = job;
    if (!spec.task) throw new Error('llm walker: task required (natural-language goal)');

    // Write a small Python harness that:
    //   1. Connects Playwright (sync API) to cdpEndpoint
    //   2. Wraps it in browser-use's BrowserSession
    //   3. Runs the Agent with the task
    //   4. Streams events to stdout as JSONL: { type, ts, msg, data }
    //
    // The harness reads job spec from a JSON file we write next to it.
    const jobDir = path.join(WALKER_DIR, job.id);
    await fs.mkdir(jobDir, { recursive: true });
    const specFile = path.join(jobDir, 'spec.json');
    const harnessFile = path.join(jobDir, 'harness.py');
    const llmSpec = {
      cdpEndpoint,
      task: spec.task,
      goal: spec.goal || null,
      maxSteps: Number(spec.maxSteps) || 30,
      model: spec.model || null, // browser-use picks default if null
      outputFile: spec.outputFile ? path.join(jobDir, spec.outputFile) : null,
    };
    await fs.writeFile(specFile, JSON.stringify(llmSpec, null, 2), 'utf8');
    await fs.writeFile(harnessFile, LL_HARNESS_PY, 'utf8');

    log(`spawning python harness (${py.path} ${harnessFile})`);
    const child = spawn(py.path, [harnessFile, specFile], {
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    job.childPid = child.pid;

    let bufOut = '';
    let bufErr = '';
    const onLine = (line) => {
      if (!line) return;
      // Try JSONL first
      try {
        const obj = JSON.parse(line);
        if (obj && typeof obj === 'object' && obj.type) {
          if (obj.type === 'log') {
            log(`  ${obj.msg}`);
            if (obj.data) job.results.push({ at: obj.ts || Date.now(), ...obj.data });
          } else if (obj.type === 'error') {
            log(`  ✗ ${obj.msg}`);
          } else if (obj.type === 'done') {
            log(`✓ agent done: ${obj.summary || ''}`);
            if (obj.result) job.results.push({ type: 'final', at: Date.now(), ...obj.result });
          } else {
            log(`  ${JSON.stringify(obj).slice(0, 200)}`);
          }
          return;
        }
      } catch { /* not JSONL, treat as plain text */ }
      log(`  ${line}`);
    };
    child.stdout.on('data', (d) => {
      bufOut += d.toString();
      const lines = bufOut.split(/\r?\n/);
      bufOut = lines.pop();
      for (const l of lines) onLine(l);
    });
    child.stderr.on('data', (d) => {
      bufErr += d.toString();
      const lines = bufErr.split(/\r?\n/);
      bufErr = lines.pop();
      for (const l of lines) onLine(`[stderr] ${l}`);
    });
    const exitCode = await new Promise((resolve) => {
      child.on('close', (code) => resolve(code ?? 0));
      child.on('error', (err) => {
        log(`spawn error: ${err.message}`);
        resolve(1);
      });
    });
    if (bufOut) onLine(bufOut);
    if (bufErr) onLine(`[stderr] ${bufErr}`);

    if (job.aborted) {
      log('walker aborted');
    } else if (exitCode !== 0) {
      throw new Error(`browser-use exited with code ${exitCode}`);
    } else {
      log('walker finished successfully');
    }
  }

  abort(jobId) {
    const job = this._jobs.get(jobId);
    if (!job) return { ok: false, error: 'job not found' };
    if (job.status !== 'running' && job.status !== 'queued') {
      return { ok: false, error: `cannot abort job in status "${job.status}"` };
    }
    job.aborted = true;
    if (job.childPid) {
      try { process.kill(job.childPid, 'SIGTERM'); } catch (e) { /* ignore */ }
    }
    return { ok: true };
  }

  jobStatus(jobId) {
    const job = this._jobs.get(jobId);
    if (!job) return null;
    return {
      id: job.id,
      mode: job.mode,
      status: job.status,
      profileId: job.profileId,
      resultsCount: job.results.length,
      error: job.error,
      startedAt: job.startedAt,
      finishedAt: job.finishedAt,
      durationMs: (job.finishedAt || Date.now()) - job.startedAt,
    };
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

  jobResults(jobId, { limit = 200 } = {}) {
    const job = this._jobs.get(jobId);
    if (!job) return null;
    return {
      jobId,
      count: job.results.length,
      results: job.results.slice(-limit),
    };
  }

  listJobs() {
    return Array.from(this._jobs.values())
      .sort((a, b) => b.startedAt - a.startedAt)
      .map((j) => this.jobStatus(j.id));
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// browser-use Python harness
//
// We embed the Python script as a string and write it next to the job spec.
// It expects argv[1] = path to spec.json. It must stream JSONL events on stdout
// and a non-zero exit on failure.
// ─────────────────────────────────────────────────────────────────────────────

const LL_HARNESS_PY = `# MSB LLM walker harness — auto-generated by crawlerService.js
# Reads spec.json, runs browser-use Agent, streams JSONL events to stdout.
import json
import sys
import os
import traceback
from datetime import datetime

def emit(obj):
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\\n")
        sys.stdout.flush()
    except Exception:
        pass

def main():
    spec_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not spec_path or not os.path.exists(spec_path):
        emit({"type": "error", "msg": f"spec.json not found: {spec_path}"})
        sys.exit(2)
    with open(spec_path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    cdp = spec.get("cdpEndpoint")
    task = spec.get("task")
    if not cdp or not task:
        emit({"type": "error", "msg": "cdpEndpoint and task required in spec"})
        sys.exit(2)

    try:
        from playwright.sync_api import sync_playwright
        emit({"type": "log", "msg": f"connecting playwright to {cdp}"})
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.pages[0] if context.pages else context.new_page()
            emit({"type": "log", "msg": "playwright connected"})

        # browser-use imports — must be installed
        from browser_use import Agent
        from browser_use.browser.session import BrowserSession
        from browser_use.browser import BrowserProfile
        emit({"type": "log", "msg": "browser-use imported"})

        # Re-use the existing playwright page via BrowserSession.
        # browser-use 0.5+ supports custom playwright session.
        profile = BrowserProfile(headless=False)
        session = BrowserSession(browser=context.browser, profile=profile)
        agent = Agent(
            task=task,
            llm_model=spec.get("model"),
            browser_session=session,
        )
        emit({"type": "log", "msg": f"running agent (max {spec.get('maxSteps', 30)} steps)"})
        result = agent.run(max_steps=int(spec.get("maxSteps", 30)))
        emit({"type": "log", "msg": "agent run complete"})
        out = spec.get("outputFile")
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"task": task, "result": str(result)}, f, ensure_ascii=False, indent=2)
            emit({"type": "log", "msg": f"saved result to {out}"})
        emit({"type": "done", "summary": str(result)[:200], "result": {"raw": str(result)[:4000]}})
        sys.exit(0)
    except Exception as e:
        emit({"type": "error", "msg": str(e), "trace": traceback.format_exc()})
        sys.exit(1)

if __name__ == "__main__":
    main()
`;
