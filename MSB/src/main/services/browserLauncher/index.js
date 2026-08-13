import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';

import { toLaunchProxy, attachProxyAuth, switchProxy, normalizeProxy } from '../../lib/proxy.js';
import { installFingerprintInitScripts, refreshFingerprint, generateFingerprint, buildAcceptLanguage } from '../../lib/fingerprint.js';
import { checkIp } from '../../lib/ipCheck.js';
import {
  materialise as cryptoMaterialise,
  fingerprint as cryptoFingerprint,
  readMarker as readCryptoMarker,
  decryptSensitiveFields,
} from '../../lib/profileCrypto.js';
import { attachHumanize, humanDelay } from '../../lib/humanize.js';
import { runScenario as runScenarioTemplate } from '../../lib/scenarios/index.js';
import { DEFAULTS, ENGINES } from '../../core/constants.js';
import { createConsoleLogBuffer } from '../consoleLogBuffer.js';
import { injectLoginData } from '../loginDataManager.js';

import { COMMON_ARGS, IGNORE_DEFAULT_ARGS, CLOAK_COMMON_ARGS } from './constants.js';
import {
  getFreePort,
  waitForCdpJson,
  pickEngine,
  safeUrl,
  normalizeNavigationInput,
  normalizeLaunchMode,
  buildLaunchPolicy,
} from './utils.js';
import { createDownloadHandler } from './downloads.js';
import { loadEngines } from './engineLoader.js';

// MSB: deterministic fingerprint seed per profile
function fingerprintSeed(profileId) {
  let h = 2166136261 >>> 0;
  const s = String(profileId || 'default');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}
import { badgeExtensionPath } from '../../core/paths.js';


/**
 * Пишет msb-context.json в папку встроенного расширения badge.
 * Расширение читает этот файл в background через chrome.runtime.getURL
 * и рисует бейдж/плашку на его основе. Файл создаётся ДО запуска браузера,
 * чтобы при первой загрузке расширения контекст уже был на диске.
 *
 * @param {object} profile — профиль из profileManager
 * @returns {string|null} путь к расширению или null если отключено
 */
function writeBadgeContext(profile) {
  try {
    if (profile?.badge === false) return null; // opt-out
    const extPath = badgeExtensionPath();
    if (!fs.existsSync(extPath)) return null;

    // Номер профиля — первичный источник. ProfileManager назначает
    // number монотонно (1, 2, 3, ...) при создании. Если у профиля
    // его почему-то нет (старая база до миграции) — fallback на
    // последние 4 символа UUID, чтобы хоть что-то отображалось.
    let num;
    if (Number.isInteger(profile.number) && profile.number > 0) {
      num = String(profile.number);
    } else if (profile.id) {
      num = String(profile.id).slice(-4).toUpperCase();
    } else {
      num = '?';
    }

    const ctx = {
      id: profile.id || null,
      number: num,
      name: profile.name || profile.account?.name || '',
      email: profile.account?.email || '',
      group: profile.group || '',
      country: profile.geoip?.country || '',
      startedAt: Date.now(),
    };

    const target = path.join(extPath, 'msb-context.json');
    fs.writeFileSync(target, JSON.stringify(ctx), 'utf8');
    return extPath;
  } catch (e) {
    // Не критично — расширение просто покажет дефолт
    return null;
  }
}

/**
 * Записывает Google как поисковик + включает все флаги автозаполнения
 * в Preferences ДО запуска браузера.
 *
 * ВАЖНО: Preferences хранится в Default/Preferences, не в корне userDataDir!
 *
 * Chrome 100+ читает ключ default_search_provider (не default_search_provider_data).
 * default_search_provider_data используется только при синхронизации аккаунта Google.
 * Оба ключа пишем для совместимости со всеми версиями Chromium.
 *
 * Флаги CLI (--default-search-provider-*) работают как fallback для первого запуска,
 * но при наличии Preferences Chrome берёт настройки оттуда — поэтому файл важнее флагов.
 */
function ensurePrefs(userDataDir, account) {
  try {
    const defaultDir = userDataDir + '\\Default';
    const prefPath = defaultDir + '\\Preferences';

    let prefs = {};
    try {
      const raw = fs.readFileSync(prefPath, 'utf8');
      if (raw && raw.trim().length > 2) prefs = JSON.parse(raw);
    } catch (_) {}

    // ── Поисковик Google ───────────────────────────────────────────────────
    const tplId = '485bf7d3-0215-45af-87dc-538868000001';
    const nowMicros = Date.now() * 1000;

    const googleTemplate = {
      id: tplId,
      keyword: 'google.com',
      short_name: 'Google',
      url: 'https://www.google.com/search?q={searchTerms}',
      suggest_url: 'https://www.google.com/complete/search?output=chrome&q={searchTerms}',
      favicon_url: 'https://www.google.com/favicon.ico',
      safe_for_autoreplace: true,
      prepopulate_id: 1,
      sync_guid: tplId,
      date_created: nowMicros,
      last_modified: nowMicros,
      last_visited: nowMicros,
      input_encodings: ['utf-8'],
    };

    // Ключ который реально читает Chrome 100+ из Preferences при старте
    prefs.default_search_provider = {
      enabled: true,
      search_url: 'https://www.google.com/search?q={searchTerms}',
      suggest_url: 'https://www.google.com/complete/search?output=chrome&q={searchTerms}',
      name: 'Google',
      keyword: 'google.com',
      favicon_url: 'https://www.google.com/favicon.ico',
      prepopulate_id: 1,
    };

    // Ключ для Chrome < 100 и механизма синхронизации
    prefs.default_search_provider_data = { template_url_data: googleTemplate };

    prefs.default_search_enabled = true;
    if (!prefs.search) prefs.search = {};
    prefs.search.suggest_enabled = true;

    // ── Автозаполнение паролей ─────────────────────────────────────────────
    prefs.credentials_enable_service = true;
    prefs.credentials_enable_autosignin = true;

    // ── Автозаполнение форм (адреса, телефоны, имена) ─────────────────────
    if (!prefs.autofill) prefs.autofill = {};
    prefs.autofill.enabled = true;
    prefs.autofill.credit_card_enabled = false;

    // profile.password_manager_enabled — ключ для Chrome < 114
    if (!prefs.profile) prefs.profile = {};
    prefs.profile.password_manager_enabled = true;

    // Отключаем промпт "сохранить пароль?" — уже внедрено через Login Data
    if (!prefs.password_manager) prefs.password_manager = {};
    prefs.password_manager.os_password_blank_confirms_chrome_signin = false;

    // Создаём папку Default если её ещё нет
    fs.mkdirSync(defaultDir, { recursive: true });
    fs.writeFileSync(prefPath, JSON.stringify(prefs), 'utf8');
  } catch (e) {
    // не критично — продолжаем запуск
  }
}

/**
 * Строит init script для автоподстановки логина/пароля в формы.
 * Срабатывает на КАЖДОЙ странице при загрузке DOM.
 * Работает поверх встроенного автозаполнения Chrome как дополнительный слой.
 *
 * @param {string} email
 * @param {string} password
 * @returns {string}
 */
function buildAutofillScript(email, password) {
  const safeEmail = JSON.stringify(email);
  const safePassword = JSON.stringify(password);

  return `(function() {
  var _email = ${safeEmail};
  var _password = ${safePassword};
  if (!_email || !_password) return;

  var emailSelectors = [
    'input[type="email"]',
    'input[name="email"]',
    'input[name="username"]',
    'input[name="login"]',
    'input[name="identifier"]',
    'input[name="loginfmt"]',
    'input[id*="email" i]',
    'input[id*="user" i]',
    'input[id*="login" i]',
    'input[autocomplete="email"]',
    'input[autocomplete="username"]',
  ];

  var passwordSelectors = [
    'input[type="password"]',
    'input[name="password"]',
    'input[name="passwd"]',
    'input[name="pass"]',
    'input[autocomplete="current-password"]',
    'input[autocomplete="new-password"]',
  ];

  function nativeSet(input, value) {
    var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    if (desc && desc.set) {
      desc.set.call(input, value);
    } else {
      input.value = value;
    }
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
  }

  function fillFields() {
    for (var i = 0; i < emailSelectors.length; i++) {
      var inputs = document.querySelectorAll(emailSelectors[i]);
      for (var j = 0; j < inputs.length; j++) {
        var inp = inputs[j];
        if (inp.disabled || inp.readOnly || inp.type === 'hidden') continue;
        if (!inp.value || inp.value === '') {
          nativeSet(inp, _email);
        }
      }
    }
    for (var k = 0; k < passwordSelectors.length; k++) {
      var pinputs = document.querySelectorAll(passwordSelectors[k]);
      for (var l = 0; l < pinputs.length; l++) {
        var pinp = pinputs[l];
        if (pinp.disabled || pinp.readOnly) continue;
        if (!pinp.value || pinp.value === '') {
          nativeSet(pinp, _password);
        }
      }
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      setTimeout(fillFields, 300);
      setTimeout(fillFields, 800);
      setTimeout(fillFields, 1500);
    });
  } else {
    setTimeout(fillFields, 100);
    setTimeout(fillFields, 500);
    setTimeout(fillFields, 1200);
  }

  var observer = new MutationObserver(function(mutations) {
    var hasInputs = mutations.some(function(m) {
      return Array.from(m.addedNodes).some(function(n) {
        return n.nodeType === 1 && (
          n.matches && (n.matches('input') || n.querySelector('input'))
        );
      });
    });
    if (hasInputs) {
      setTimeout(fillFields, 200);
    }
  });

  observer.observe(document.documentElement, { childList: true, subtree: true });
})();`;
}

export class BrowserLauncher extends EventEmitter {

  constructor({ profileManager, statistics, supervisor, cookieStore, commonExtensionsManager, logger }) {
    super();
    this.profileManager = profileManager;
    this.statistics = statistics;
    this.supervisor = supervisor;
    this.cookieStore = cookieStore || null;
    this.commonExtensionsManager = commonExtensionsManager || null;
    this.running = new Map();
    this._explicitlyStopping = new Set();
    this._enginesLoaded = null;
    this.logger = logger?.child?.({ mod: 'browserLauncher' }) || logger || console;
  }

  async _loadEngines() {
    return loadEngines(this);
  }

  hasRunning() {
    return this.running.size > 0;
  }

  isRunning(id) {
    return this.running.has(id);
  }

  getRunning(id) {
    return this.running.get(id) || null;
  }

  status() {
    return Array.from(this.running.entries()).map(([id, info]) => ({
      id,
      engine: info.engine,
      startedAt: info.startedAt,
      cdpEndpoint: info.cdpEndpoint,
      cdpPort: info.cdpPort,
      pid: info.pid || null,
      running: true,
      launchMode: info.launchMode,
      headlessApplied: info.headlessApplied,
      backgroundApplied: info.backgroundApplied,
      focusSuppressed: info.focusSuppressed,
    }));
  }

  async start(profile, options = {}) {
    if (this.running.has(profile.id)) {
      return this._publicInfo(this.running.get(profile.id));
    }

    const launchMode = normalizeLaunchMode(options);
    const launchPolicy = buildLaunchPolicy(launchMode);

    const engines = await this._loadEngines();
    const engineName = pickEngine(profile.engine || ENGINES.AUTO, engines);

    const userDataDir = this.profileManager.userDataDir(profile.id);

    // ── 0a. E2E key validation (MoreLogin parity) ───────────────────────────
    // If a marker exists on disk, the profile is configured to require
    // `encryptKey` to unlock sensitive fields. We validate synchronously so
    // a wrong key returns 403 before we burn resources launching Chromium.
    const cryptoMarker = await readCryptoMarker(userDataDir);
    let derivedKey = null;
    let cryptoOk = true;
    let cryptoNote = null;
    if (cryptoMarker) {
      const mat = cryptoMaterialise(options.encryptKey, { profileId: profile.id });
      if (!mat) {
        cryptoOk = false;
        cryptoNote = 'profile is encrypted; encryptKey missing or malformed';
      } else if (cryptoFingerprint(mat.key) !== cryptoMarker.fingerprint) {
        cryptoOk = false;
        cryptoNote = 'profile is encrypted; the provided encryptKey does not match';
      } else {
        derivedKey = mat.key;
        // Decrypt sensitive fields in-place for the in-memory profile so the
        // running session sees the unencrypted values. The on-disk file is
        // untouched until the next profileManager.update() call.
        decryptSensitiveFields(profile, derivedKey);
      }
      if (!cryptoOk) {
        const err = new Error(cryptoNote);
        err.statusCode = 403;
        throw err;
      }
    }

    // ── 0. MoreLogin-style: pre-flight IP check ──────────────────────────────
    //   closeCheckIPpage=true  → check before launch; on failure honour checkIPErrorHandle
    //     1 = abort with informative error (default)
    //     2 = proceed with a warning attached to start info
    //   closeCheckIPpage=false → skip pre-check
    // The same data is also reported back to the caller for the response envelope
    // so a client written against the MoreLogin API can consume it directly.
    let ipCheckResult = null;
    if (options.closeCheckIPpage === true) {
      const proxyEnabled = profile.proxyEnabled !== false;
      const targetProxy = proxyEnabled ? (profile.proxy || null) : null;
      try {
        ipCheckResult = await checkIp(targetProxy, { logger: this.logger });
        if (ipCheckResult.status === 'error') {
          const handle = Number(options.checkIPErrorHandle) === 2 ? 2 : 1;
          if (handle === 1) {
            this.logger.warn(
              { profileId: profile.id, err: ipCheckResult.error },
              'start aborted: ip check failed (checkIPErrorHandle=1)'
            );
            const err = new Error(`IP check failed: ${ipCheckResult.error}`);
            err.statusCode = 503;
            err.ipCheck = ipCheckResult;
            throw err;
          }
          this.logger.warn(
            { profileId: profile.id, err: ipCheckResult.error },
            'ip check failed but proceeding (checkIPErrorHandle=2)'
          );
        } else {
          this.logger.info(
            { profileId: profile.id, status: ipCheckResult.status, ip: ipCheckResult.ip, country: ipCheckResult.country },
            'pre-flight ip check ok'
          );
        }
      } catch (err) {
        if (err.statusCode === 503) throw err; // re-throw our intentional abort
        this.logger.warn({ profileId: profile.id, err: err.message }, 'ip check exception — proceeding');
        ipCheckResult = { status: 'error', error: err.message, ip: null, viaProxy: !!targetProxy };
      }
    }

    // ── 1. Инжектируем логин/пароль в Login Data (DPAPI-шифрование) ──
    if (profile.account?.email && profile.account?.password) {
      await injectLoginData(userDataDir, profile.account, this.logger);
    }

    // ── 2. Пишем Google + флаги автозаполнения в Default/Preferences ──
    ensurePrefs(userDataDir, profile.account);

    // proxyEnabled=false — прокси выключен, запускаем без прокси
    const proxyEnabled = profile.proxyEnabled !== false;
    const activeProxy = proxyEnabled ? (profile.proxy || null) : null;

        // socks5 с авторизацией: Chromium не поддерживает --proxy-server=socks5://user:pass@...
    // нативно (ERR_NO_SUPPORTED_PROXIES / забивает NAT-буфер хотспота).
    // Применяем через context.route() + undici ProxyAgent после старта.
    const isSocks5WithAuth = activeProxy?.protocol === 'socks5' && activeProxy?.username && activeProxy?.password;
    const launchProxy = isSocks5WithAuth ? undefined : toLaunchProxy(activeProxy);
    const debugPort = await getFreePort();

    const fpViewport = profile.fingerprint?.viewport || { width: 1280, height: 800 };
    const windowSizeArg = `--window-size=${fpViewport.width},${fpViewport.height}`;
    const windowPositionArg = launchPolicy.windowPosition ? `--window-position=${launchPolicy.windowPosition.x},${launchPolicy.windowPosition.y}` : null;
    const userAgent = profile.fingerprint?.userAgent;
    const timezone = profile.fingerprint?.timezone;
    const locale = profile.fingerprint?.locale;
    // uiLanguage — язык UI браузера (--lang). Отдельно от locale (фингерпринт для сайтов).
    // По умолчанию ru-RU чтобы браузер был на русском независимо от locale профиля.
    const uiLanguage = profile.fingerprint?.uiLanguage ?? 'ru-RU';

    const allExtensions = [
      ...(profile.extensions || []),
      ...(this.commonExtensionsManager?.list() || []),
    ];

    // ── MSB Profile Badge: пишем контекст + добавляем в список расширений ──
    // Делаем это ДО запуска движка, чтобы к моменту первой загрузки
    // расширения файл msb-context.json уже был на диске.
    const badgeExt = writeBadgeContext(profile);
    if (badgeExt) {
      allExtensions.push(badgeExt);
    }

    let context;

    if (engineName === ENGINES.CLOAK) {
      // CloakBrowser — используется только при явном profile.engine = 'cloakbrowser'.
      // stealthArgs: false — отключаем дефолтный блок CloakBrowser который добавляет
      // --no-sandbox. Этот флаг детектируется Qrator/Cloudflare/Google как маркер
      // автоматизации и приводит к 403. Без него binary-patches CloakBrowser работают.
      //
      // CLOAK_COMMON_ARGS — runtime-флаги (WebRTC, default-search, privacy),
      // которые Cloak НЕ покрывает на уровне бинаря. Антидетект-CDP/JS флаги
      // Cloak проставляет сам через stealthArgs — их сюда НЕ добавляем.
      const cloakExtraArgs = [
        ...CLOAK_COMMON_ARGS,
        `--remote-debugging-port=${debugPort}`,
        `--remote-debugging-address=127.0.0.1`,
        windowSizeArg,
        '--enable-features=PasswordManager',
        `--msb-profile-number=${profile.number != null ? profile.number : ''}`,
        `--msb-profile-email=${profile.account?.email || ''}`,
        `--msb-fingerprint-seed=${fingerprintSeed(profile.id)}`,
        ...(windowPositionArg ? [windowPositionArg] : []),
        ...(launchPolicy.extraArgs || []),
        ...(options.extraArgs || []),
      ];

      if (allExtensions.length > 0) {
        cloakExtraArgs.push(`--load-extension=${allExtensions.join(',')}`);
        cloakExtraArgs.push(`--allowlisted-extension-id=${allExtensions.map(() => '*').join(',')}`);
      }

      const cloakOptions = {
        userDataDir,
        headless: launchPolicy.headless,
        proxy: launchProxy,
        humanize: !!profile.humanize,
        geoip: !!profile.proxy,
        viewport: null,
        stealthArgs: false,
        args: cloakExtraArgs,
        acceptDownloads: true,
        contextOptions: {
          ignoreHTTPSErrors: true,
          acceptDownloads: true,
        },
      };

      if (userAgent) cloakOptions.userAgent = userAgent;
      if (timezone && !profile.proxy) cloakOptions.timezone = timezone;
      if (!profile.proxy) cloakOptions.locale = uiLanguage;

      this.logger.info({
        profileId: profile.id,
        engine: engineName,
        launchMode,
        headlessApplied: launchPolicy.headless,
        backgroundApplied: launchPolicy.backgroundApplied,
        focusSuppressed: launchPolicy.focusSuppressed,
        extensions: allExtensions.length,
        humanize: cloakOptions.humanize,
        debugPort,
      }, 'launching CloakBrowser');
      context = await engines.cloakbrowser.launchPersistentContext(cloakOptions);

    } else {
      // Patchright — движок по умолчанию (AUTO).
      // Патчит CDP-протокол без --no-sandbox, chromiumSandbox: true.
      // Поиск в адресной строке работает через COMMON_ARGS + ensurePrefs выше.
      const args = [
        ...COMMON_ARGS,
        `--remote-debugging-port=${debugPort}`,
        windowSizeArg,
        '--enable-features=PasswordManager',
        `--msb-profile-number=${profile.number != null ? profile.number : ''}`,
        `--msb-profile-email=${profile.account?.email || ''}`,
        `--msb-fingerprint-seed=${fingerprintSeed(profile.id)}`,
        ...(windowPositionArg ? [windowPositionArg] : []),
        ...(launchPolicy.extraArgs || []),
        ...(options.extraArgs || []),
      ];

      if (allExtensions.length > 0) {
        args.push(`--load-extension=${allExtensions.join(',')}`);
        args.push(`--allowlisted-extension-id=${allExtensions.join(',')}`);
      }

      this.logger.info({
        profileId: profile.id,
        engine: engineName,
        launchMode,
        headlessApplied: launchPolicy.headless,
        backgroundApplied: launchPolicy.backgroundApplied,
        focusSuppressed: launchPolicy.focusSuppressed,
        extensions: allExtensions.length,
        humanize: !!profile.humanize,
        debugPort,
      }, 'launching Patchright');

      context = await engines.patchright.chromium.launchPersistentContext(userDataDir, {
        headless: launchPolicy.headless,
        proxy: launchProxy,
        viewport: null,
        userAgent,
        locale: uiLanguage,
        timezoneId: timezone,
        args,
        ignoreDefaultArgs: IGNORE_DEFAULT_ARGS,
        ignoreHTTPSErrors: true,
        chromiumSandbox: true,
        acceptDownloads: true,
      });
    }
    // ── EARLY WINDOW HIDE: свернуть окно немедленно после запуска браузера ──────
    // Это происходит ДО grantPermissions/setGeolocation/newPage (те занимают ~200-600мс).
    // Именно в эти 200-600мс окно было видно пользователю.
    // Второй вызов minimize остаётся ниже как страховка.
    if (launchPolicy.postLaunchAction === 'minimize') {
      const earlyPage = context.pages()[0];
      if (earlyPage) {
        try {
          const earlySession = await context.newCDPSession(earlyPage);
          const { windowId } = await earlySession.send('Browser.getWindowForTarget');
          await earlySession.send('Browser.setWindowBounds', {
            windowId,
            bounds: { windowState: 'minimized' },
          });
          await earlySession.detach().catch(() => {});
          this.logger.debug({ profileId: profile.id, launchMode }, 'early window minimize OK');
        } catch (err) {
          this.logger.warn({ profileId: profile.id, launchMode, err: err.message }, 'early window minimize failed');
        }
      }
    }

    try {
      await context.grantPermissions([
        'geolocation',
        'notifications',
        'camera',
        'microphone',
        'clipboard-read',
        'clipboard-write',
      ]);
    } catch (err) {
      this.logger.warn({ profileId: profile.id, err: err.message }, 'grantPermissions partial failure');
    }

    // ── Geolocation override (from fingerprint.geolocation) ──────────────────
    const geo = profile.fingerprint?.geolocation;
    if (geo && typeof geo.latitude === 'number' && typeof geo.longitude === 'number') {
      try {
        await context.setGeolocation({ latitude: geo.latitude, longitude: geo.longitude, accuracy: geo.accuracy ?? 50 });
        this.logger.debug({ profileId: profile.id, geo }, 'geolocation set from fingerprint');
      } catch (err) {
        this.logger.warn({ profileId: profile.id, err: err.message }, 'setGeolocation failed');
      }
    }

    context.on('page', (p) => {
      p.on('requestfailed', () => {});
    });

    const page = context.pages()[0] || (await context.newPage());

    if (launchPolicy.postLaunchAction === 'minimize') {
      try {
        const session = await context.newCDPSession(page);
        const { windowId } = await session.send('Browser.getWindowForTarget');
        await session.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'minimized' } });
        await session.detach().catch(() => {});
      } catch (err) {
        this.logger.warn({ profileId: profile.id, launchMode, err: err.message }, 'window minimize best-effort failed');
      }
    }

    // ── Init script: убираем маркеры автоматизации + блокируем горячие клавиши ──
    // cdpEvasion=true (MoreLogin parity) — расширенный набор анти-детект маркеров.
    // Применяется когда к профилю подключаются извне (Playwright / Puppeteer / Selenium)
    // через CDP WebSocket и сайты пытаются детектировать headless-режим.
    const cdpEvasion = options.cdpEvasion === true;
    const initScript = `(function () {
      try {
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
      } catch(e) {}

      try { delete window.__playwright_coverage__; } catch(e) {}
      try { delete window.__pw_manual; } catch(e) {}
      try {
        Object.getOwnPropertyNames(window).forEach(function(k) {
          if (/^(__cdc|cdc_)/i.test(k)) { try { delete window[k]; } catch(e) {} }
        });
      } catch(e) {}

      ${cdpEvasion ? `
      // ── cdpEvasion: more aggressive Playwright/automation marker removal ──
      try {
        // Puppeteer / Playwright global install markers
        if (navigator.webdriver === true) Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        // Remove Permission/query descriptors used by some detection scripts
        const _origPermQuery = window.Permissions?.prototype?.query;
        if (_origPermQuery) {
          try {
            window.Permissions.prototype.query = function(...a) {
              const name = a[0] && a[0].name;
              if (name === 'notifications') return Promise.resolve({ state: Notification.permission });
              return _origPermQuery.apply(this, a);
            };
          } catch(e) {}
        }
        // chrome.runtime only exists in real Chrome, not in headless Cloud browsers
        if (!window.chrome?.runtime) {
          try {
            window.chrome = window.chrome || {};
            window.chrome.runtime = window.chrome.runtime || {
              PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
              PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
              RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' },
              OnInstalledReason: { CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
              OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
              connect: () => {},
              sendMessage: () => {},
            };
            window.chrome.loadTimes = function(){};
            window.chrome.csi = function(){};
            window.chrome.app = { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };
          } catch(e) {}
        }
        // Hardware concurrency rounding (avoid the 2 / 4 / 8 / 16 set)
        try {
          if (navigator.hardwareConcurrency && navigator.hardwareConcurrency < 4) {
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8, configurable: true });
          }
        } catch(e) {}
        // Languages — make sure it's a real array
        try {
          Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'], configurable: true });
        } catch(e) {}
      } catch(e) {}
      ` : ``}

      window.addEventListener('contextmenu', function(e) { e.stopPropagation(); }, true);
      window.addEventListener('wheel', function(e) {
        if (e.ctrlKey) e.stopPropagation();
      }, { passive: true, capture: true });
      window.addEventListener('keydown', function(e) {
        const ctrl = e.ctrlKey || e.metaKey;
        if (ctrl && ['a','c','v','x','z','y'].includes(e.key.toLowerCase())) e.stopPropagation();
        if (e.key === 'F5' || (ctrl && e.key.toLowerCase() === 'r')) e.stopPropagation();
        if (ctrl && e.key.toLowerCase() === 'l') e.stopPropagation();
        if (ctrl && e.key.toLowerCase() === 't') e.stopPropagation();
        if (ctrl && e.key.toLowerCase() === 'w') e.stopPropagation();
        if (ctrl && e.key.toLowerCase() === 's') e.stopPropagation();
        if (ctrl && e.key.toLowerCase() === 'p') e.stopPropagation();
        if (ctrl && e.key.toLowerCase() === 'f') e.stopPropagation();
        if (ctrl && e.key.toLowerCase() === 'u') e.stopPropagation();
        if (e.key === 'F11') e.stopPropagation();
        if (e.key === 'F12') e.stopPropagation();
        if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) e.stopPropagation();
        if (e.key === 'Backspace' && !['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)) {
          e.stopPropagation();
        }
      }, true);
    })()`;
    await context.addInitScript(initScript);

    // ── 3. JS autofill: подставляем email+пароль в формы на каждой странице ──
    if (profile.account?.email && profile.account?.password) {
      try {
        await context.addInitScript(buildAutofillScript(profile.account.email, profile.account.password));
        this.logger.debug({ profileId: profile.id, email: profile.account.email }, 'autofill init script installed');
      } catch (err) {
        this.logger.warn({ profileId: profile.id, err: err.message }, 'autofill init script failed');
      }
    }

    const { attachDownloadHandler } = createDownloadHandler(profile, this.logger);
    attachDownloadHandler(page);
    page.on('popup', attachDownloadHandler);
    context.on('page', (newPage) => {
      attachDownloadHandler(newPage);
      newPage.on('popup', attachDownloadHandler);
      // Subscribe network capture to popups / new tabs opened after launch
      try {
        newPage.context().newCDPSession(newPage).then((cdp) => {
          newPage.on('close', () => { try { cdp.detach(); } catch {} });
          this.emit('page:cdpSession', { profileId: profile.id, cdpPage: cdp, page: newPage });
        }).catch((e) => this.logger.debug({ err: e.message }, 'popup cdp session failed'));
      } catch (e) {
        this.logger.debug({ err: e.message }, 'popup attach skipped');
      }
    });

    const consoleLog = createConsoleLogBuffer();
    page.on('console', (msg) => {
      consoleLog.push({ type: 'console', level: msg.type(), text: msg.text(), location: msg.location(), at: Date.now() });
    });
    page.on('pageerror', (err) => {
      consoleLog.push({ type: 'pageerror', level: 'error', text: err?.message || String(err), stack: err?.stack || null, at: Date.now() });
    });

    if (activeProxy) {
      if (isSocks5WithAuth) {
        // socks5 с авторизацией — роутим весь трафик через undici ProxyAgent
        try {
          await switchProxy(context, activeProxy);
          this.logger.info({ profileId: profile.id, proxy: `socks5://${activeProxy.host}:${activeProxy.port}` }, 'socks5 proxy applied via route()');
        } catch (err) {
          this.logger.warn({ profileId: profile.id, err: err.message }, 'switchProxy (socks5) failed');
        }
      } else {
        try {
          await attachProxyAuth(context, activeProxy);
        } catch (err) {
          this.logger.warn({ profileId: profile.id, err: err.message }, 'attachProxyAuth failed');
        }
      }
    }

    if (profile.aggressiveFingerprint) {
      await installFingerprintInitScripts(context, profile.fingerprint);
    }

    // ── Per-page CDP overrides: locale, Accept-Language, timezone (на каждой странице) ──
    // Это гарантирует что fingerprint применяется независимо от --lang браузера.
    const _applyPageFingerprint = async (pg) => {
      if (!profile.fingerprint) return;
      const fp = profile.fingerprint;
      try {
        const cli = await context.newCDPSession(pg);
        // Accept-Language из массива languages (правильный q-weighted header)
        if (fp.userAgent) {
          const acceptLang = buildAcceptLanguage(fp.languages || (fp.locale ? [fp.locale] : []));
          await cli.send('Emulation.setUserAgentOverride', {
            userAgent: fp.userAgent,
            acceptLanguage: acceptLang,
            platform: fp.platform || 'Win32',
          }).catch(() => {});
        }
        // Locale для Intl API (navigator.language через CDP)
        if (fp.locale) {
          await cli.send('Emulation.setLocaleOverride', { locale: fp.locale }).catch(() => {});
        }
        // Timezone (redundant but safe)
        if (fp.timezone) {
          await cli.send('Emulation.setTimezoneOverride', { timezoneId: fp.timezone }).catch(() => {});
        }
        await cli.detach().catch(() => {});
      } catch (_) {}
    };

    // Применяем к уже открытым страницам
    for (const pg of context.pages()) { _applyPageFingerprint(pg).catch(() => {}); }
    // И ко всем новым
    context.on('page', (pg) => { _applyPageFingerprint(pg).catch(() => {}); });

    let wayfern = null;
    if (profile.humanize && engineName === ENGINES.PATCH) {
      wayfern = await attachHumanize(page);
    }

    if (launchPolicy.microWarmup) {
      try {
        await humanDelay(250, 700);
        await page.evaluate(() => {
          const delta = 24 + Math.floor(Math.random() * 60);
          window.scrollBy({ top: delta, behavior: 'smooth' });
          window.scrollBy({ top: -delta, behavior: 'smooth' });
        }).catch(() => {});
      } catch (err) {
        this.logger.debug({ profileId: profile.id, launchMode, err: err.message }, 'background micro-warmup skipped');
      }
    }

    // CloakBrowser: humanize через его собственный API (эквивалент Wayfern).
    // humanizeBrowser() применяет behavioral-layer к Browser (не к Page/Context),
    // вытаскиваем browser через context.browser(). Делаем best-effort — если
    // API недоступен (старая версия cloakbrowser) или упал, логируем и идём дальше.
    if (profile.humanize && engineName === ENGINES.CLOAK) {
      try {
        const browser = context.browser?.();
        const humanizeFn = engines.cloakbrowser?.humanizeBrowser;
        if (browser && typeof humanizeFn === 'function') {
          await humanizeFn.call(engines.cloakbrowser, browser);
          this.logger.info({ profileId: profile.id }, 'cloak humanize applied');
        } else {
          this.logger.warn({ profileId: profile.id }, 'cloak humanize skipped: API unavailable');
        }
      } catch (err) {
        this.logger.warn({ profileId: profile.id, err: err.message }, 'cloak humanize failed');
      }
    }

    if (this.cookieStore) {
      try {
        const snapshot = await this.cookieStore.loadSnapshot(profile.id);
        if (Array.isArray(snapshot) && snapshot.length) {
          await context.addCookies(snapshot);
          this.logger.info({ profileId: profile.id, count: snapshot.length }, 'cookie snapshot auto-restored on start');
        }
      } catch (err) {
        this.logger.warn({ profileId: profile.id, err: err.message }, 'auto-restore cookie snapshot failed');
      }
    }

    let cdpEndpoint = null;
    let cdpPort = null;
    try {
      const json = await waitForCdpJson(debugPort);
      cdpEndpoint = json.webSocketDebuggerUrl;
      cdpPort = debugPort;
    } catch (err) {
      this.logger.warn({ profileId: profile.id, debugPort, err: err.message }, 'CDP endpoint unavailable');
    }

    try {
      const cdpPage = await context.newCDPSession(page);
      page.on('close', () => { try { cdpPage.detach(); } catch {} });
      await cdpPage.send('Emulation.setPageScaleFactor', { pageScaleFactor: 1 });
      // Notify subscribers (e.g. NetworkCaptureService) that a per-page CDP session is ready
      try { this.emit('page:cdpSession', { profileId: profile.id, cdpPage, page }); }
      catch (emitErr) { this.logger.debug({ err: emitErr.message }, 'page:cdpSession emit failed'); }
    } catch (err) {
      this.logger.warn({ profileId: profile.id, err: err.message }, 'zoom CDP setup skipped');
    }

    let browserPid = null;
    try {
      browserPid = context.browser()?.process()?.pid ?? null;
    } catch {}

    const info = {
      id: profile.id,
      engine: engineName,
      context,
      page,
      wayfern,
      cdpEndpoint,
      cdpPort,
      pid: browserPid,
      startedAt: Date.now(),
      profile,
      consoleLog,
      launchMode,
      headlessApplied: launchPolicy.headless,
      backgroundApplied: launchPolicy.backgroundApplied,
      focusSuppressed: launchPolicy.focusSuppressed,
    };

    context.on('close', () => {
      const wasExplicit = this._explicitlyStopping?.has(profile.id);
      this._explicitlyStopping?.delete(profile.id);
      this.running.delete(profile.id);
      this.supervisor?.markStopping?.(profile.id);
      if (!wasExplicit) {
        this.emit('stopped', { id: profile.id, crashed: true });
      }
    });

    this.running.set(profile.id, info);

    if (this.supervisor) this.supervisor.track(profile, options, context);
    if (this.statistics) await this.statistics.recordStart(profile.id);

    if (profile.startUrl) {
      try {
        await page.goto(profile.startUrl, { waitUntil: 'domcontentloaded', timeout: DEFAULTS.NAV_TIMEOUT_MS });
      } catch (err) {
        this.logger.warn({ profileId: profile.id, startUrl: profile.startUrl, err: err.message }, 'initial navigation failed');
      }
    }

    this.logger.info({
      profileId: profile.id,
      engine: engineName,
      launchMode,
      headlessApplied: launchPolicy.headless,
      backgroundApplied: launchPolicy.backgroundApplied,
      focusSuppressed: launchPolicy.focusSuppressed,
      cdpPort,
      pid: browserPid,
      crypto: !!cryptoMarker,
    }, 'browser started');
    this.emit('started', { id: profile.id, launchMode, headlessApplied: launchPolicy.headless });
    // Persist ip-check result on the running record so status() can show it.
    if (ipCheckResult) info.ipCheck = ipCheckResult;
    // Persist crypto status so /api/env/status can report it.
    info.crypto = cryptoMarker ? { enabled: true, kind: cryptoMarker.kind, verified: true } : { enabled: false };
    return this._publicInfo(info);
  }

  async stop(id, { crashed = false } = {}) {
    const info = this.running.get(id);
    if (!info) return false;
    this.supervisor?.markStopping?.(id);
    this._explicitlyStopping.add(id);

    if (this.cookieStore && !crashed) {
      try {
        const cookies = await info.context.cookies();
        await this.cookieStore.saveSnapshot(id, cookies);
        this.logger.info({ profileId: id, count: cookies.length }, 'cookie snapshot auto-saved on stop');
      } catch (err) {
        this.logger.warn({ profileId: id, err: err.message }, 'auto-save cookie snapshot failed');
      }
    }

    try {
      await info.context.close();
    } catch (err) {
      this.logger.warn({ profileId: id, err: err.message }, 'close context failed');
    }
    this._explicitlyStopping.delete(id);
    this.running.delete(id);
    if (this.statistics) await this.statistics.recordStop(id, { crashed });
    this.logger.info({ profileId: id, crashed }, 'browser stopped');
    this.emit('stopped', { id, crashed });
    return true;
  }

  async closeAll() {
    const ids = Array.from(this.running.keys());
    await Promise.allSettled(ids.map((id) => this.stop(id)));
  }

  async goto(id, url) {
    const info = this._requireRunning(id);
    const target = normalizeNavigationInput(url);
    if (!target) throw new Error('url required');
    await info.page.goto(target, { waitUntil: 'domcontentloaded', timeout: DEFAULTS.NAV_TIMEOUT_MS });
    this.logger.info({ profileId: id, input: url, target }, 'goto');
    return { url: info.page.url(), target };
  }

  async evaluate(id, script) {
    const info = this._requireRunning(id);
    const fn = new Function(`return (async () => { ${script} })()`);
    return info.page.evaluate(fn);
  }

  async screenshot(id, options = {}) {
    const info = this._requireRunning(id);
    const type = options.type === 'jpeg' ? 'jpeg' : 'png';
    const buffer = await info.page.screenshot({
      type,
      fullPage: !!options.fullPage,
      quality: type === 'jpeg' ? (options.quality ?? DEFAULTS.SCREENCAST_QUALITY) : undefined,
    });
    return { buffer, mimeType: type === 'jpeg' ? 'image/jpeg' : 'image/png' };
  }

  getConsoleLog(id, { limit } = {}) {
    const info = this._requireRunning(id);
    return info.consoleLog ? info.consoleLog.list(limit) : [];
  }

  async runScenario(id, scenarioName, params) {
    const info = this._requireRunning(id);
    try {
      const res = await runScenarioTemplate(scenarioName, {
        page: info.page,
        context: info.context,
        wayfern: info.wayfern,
        profile: info.profile,
        cookieStore: this.cookieStore || null,
        params,
      });
      await this.statistics?.recordScenario?.(id, scenarioName, { success: true });
      this.logger.info({ profileId: id, scenario: scenarioName }, 'scenario succeeded');
      return res;
    } catch (err) {
      await this.statistics?.recordScenario?.(id, scenarioName, { success: false });
      this.logger.warn({ profileId: id, scenario: scenarioName, err: err.message }, 'scenario failed');
      throw err;
    }
  }

  async executeCommands(id, commands = [], waitFor = null) {
    const info = this._requireRunning(id);
    const page = info.page;
    const results = [];

    for (const cmd of commands) {
      switch (cmd.type) {
        case 'goto': {
          const target = normalizeNavigationInput(cmd.url);
          if (!target) throw new Error('goto: url required');
          await page.goto(target, { waitUntil: 'domcontentloaded', timeout: cmd.timeout ?? DEFAULTS.NAV_TIMEOUT_MS });
          results.push({ type: 'goto', url: page.url(), target });
          break;
        }
        case 'click': {
          if (!cmd.selector) throw new Error('click: selector required');
          await page.click(cmd.selector, { timeout: cmd.timeout ?? 5000 });
          results.push({ type: 'click', selector: cmd.selector });
          break;
        }
        case 'fill': {
          if (!cmd.selector) throw new Error('fill: selector required');
          await page.fill(cmd.selector, cmd.value ?? '', { timeout: cmd.timeout ?? 5000 });
          results.push({ type: 'fill', selector: cmd.selector });
          break;
        }
        case 'screenshot': {
          const buffer = await page.screenshot({ type: 'jpeg', quality: DEFAULTS.SCREENCAST_QUALITY ?? 40 });
          results.push({ type: 'screenshot', data: buffer.toString('base64') });
          break;
        }
        case 'waitFor': {
          if (!cmd.selector) throw new Error('waitFor: selector required');
          await page.waitForSelector(cmd.selector, { timeout: cmd.timeout ?? 5000 });
          results.push({ type: 'waitFor', selector: cmd.selector });
          break;
        }
        case 'assert': {
          if (!cmd.expression) throw new Error('assert: expression required');
          const value = await page.evaluate(cmd.expression);
          results.push({ type: 'assert', passed: value === true, value });
          break;
        }
        case 'scroll': {
          const x = cmd.x ?? 760;
          const y = cmd.y ?? 400;
          await page.mouse.move(x, y);
          await page.mouse.wheel(cmd.deltaX ?? 0, cmd.deltaY ?? 300);
          results.push({ type: 'scroll', x, y, deltaX: cmd.deltaX ?? 0, deltaY: cmd.deltaY ?? 300 });
          break;
        }
        case 'wait': {
          const ms = Math.min(Math.max(cmd.ms ?? 1000, 0), 30_000);
          await page.waitForTimeout(ms);
          results.push({ type: 'wait', ms });
          break;
        }
        default:
          this.logger.warn({ cmd }, 'executeCommands: unknown command type');
          results.push({ type: cmd.type, error: 'unknown_command' });
      }
    }

    if (waitFor?.selector) {
      await page.waitForSelector(waitFor.selector, { timeout: waitFor.timeout ?? 10000 });
    }

    this.logger.debug({ profileId: id, commandCount: commands.length }, 'executeCommands completed');
    return results;
  }

  async refreshFingerprint(id, fingerprint) {
    const info = this._requireRunning(id);
    const newFp = fingerprint || generateFingerprint({ platform: info.profile.fingerprint?.platform });
    await refreshFingerprint(info.context, info.page, newFp, { reload: false });
    const updated = await this.profileManager.update(id, { fingerprint: newFp });
    info.profile = updated;
    this.logger.info({ profileId: id }, 'fingerprint refreshed');
    return { applied: true, fingerprint: newFp };
  }

  async switchProxy(id, proxy) {
    const info = this._requireRunning(id);
    const normalized = proxy === null ? null : normalizeProxy(proxy);
    const result = await switchProxy(info.context, normalized);
    this.logger.info({ profileId: id, proxy: normalized ? `${normalized.protocol}://${normalized.host}:${normalized.port}` : null }, 'proxy switched');
    const updated = await this.profileManager.update(id, { proxy: normalized });
    info.profile = updated;
    return result;
  }

  async selfTest(id) {
    const info = this._requireRunning(id);
    const page = info.page;
    const probes = [];

    async function probe(name, fn) {
      try {
        const detail = await fn();
        probes.push({ name, ok: true, detail });
      } catch (err) {
        probes.push({ name, ok: false, detail: err.message });
      }
    }

    await probe('navigator.webdriver', async () => {
      const val = await page.evaluate(() => navigator.webdriver);
      if (val !== undefined && val !== false) throw new Error(`webdriver=${JSON.stringify(val)}`);
      return `webdriver=${JSON.stringify(val)}`;
    });
    await probe('userAgent', () => page.evaluate(() => navigator.userAgent));
    await probe('plugins.length', async () => {
      const n = await page.evaluate(() => navigator.plugins?.length ?? 0);
      return `count=${n}`;
    });
    await probe('permissions.query(notifications)', async () => {
      return page.evaluate(async () => {
        const p = await navigator.permissions.query({ name: 'notifications' });
        return `state=${p.state}`;
      });
    });
    await probe('chrome.runtime', async () => {
      const val = await page.evaluate(() => typeof window.chrome?.runtime);
      return `chrome.runtime=${val}`;
    });
    await probe('navigator.vendor', async () => {
      return page.evaluate(() => navigator.vendor);
    });

    return { engine: info.engine, cdpEndpoint: info.cdpEndpoint, probes };
  }

  _requireRunning(id) {
    const info = this.running.get(id);
    if (!info) throw new Error(`Profile ${id} is not running`);
    return info;
  }

  _publicInfo(info) {
    return {
      id: info.id,
      engine: info.engine,
      startedAt: info.startedAt,
      cdpEndpoint: info.cdpEndpoint,
      cdpPort: info.cdpPort,
      pid: info.pid || null,
      running: true,
      launchMode: info.launchMode || 'visible',
      headlessApplied: !!info.headlessApplied,
      backgroundApplied: !!info.backgroundApplied,
      focusSuppressed: !!info.focusSuppressed,
      // MoreLogin-compatible aliases (so a client written against /api/env/start works)
      envId: info.id,
      debugPort: info.cdpPort != null ? String(info.cdpPort) : null,
      wsEndpoint: info.cdpEndpoint,
      type: info.engine,
      webdriver: info.cdpEndpoint,
      url: safeUrl(info.page),
      ipCheck: info.ipCheck || null,
      crypto: info.crypto || { enabled: false },
    };
  }
}
