import { createServer } from 'node:net';
import { DEFAULTS, ENGINES } from '../../core/constants.js';

const LAUNCH_MODES = new Set(['visible', 'minimized', 'background', 'headless']);

export async function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.listen(0, '127.0.0.1', () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
    srv.on('error', reject);
  });
}

export async function waitForCdpJson(port, attempts = DEFAULTS.CDP_WAIT_ATTEMPTS, intervalMs = DEFAULTS.CDP_WAIT_INTERVAL_MS) {
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (res.ok) return await res.json();
    } catch { }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`CDP on port ${port} did not become ready`);
}

/**
 * Выбор движка:
 * - 'cloakbrowser' → только если явно указан в профиле (profile.engine = 'cloakbrowser')
 * - 'patchright'   → по умолчанию (AUTO или явно)
 *
 * Почему patchright по умолчанию:
 *   CloakBrowser без stealthArgs добавляет --no-sandbox, который детектируется
 *   Qrator / Cloudflare / Google как маркер автоматизации.
 *   Patchright патчит CDP-протокол без --no-sandbox и работает с chromiumSandbox: true.
 */
export function pickEngine(requested, engines) {
  if (requested === ENGINES.CLOAK) {
    if (engines.cloakbrowser) return ENGINES.CLOAK;
    throw new Error('cloakbrowser engine requested but not installed');
  }
  if (requested === ENGINES.PATCH) {
    if (engines.patchright) return ENGINES.PATCH;
    throw new Error('patchright engine requested but not installed');
  }
  // AUTO: cloakbrowser предпочтительнее — source-level fingerprint patches проходят
  // Qrator/Cloudflare/Google без дополнительных stealth-аргументов.
  if (engines.cloakbrowser) return ENGINES.CLOAK;
  if (engines.patchright) return ENGINES.PATCH;
  throw new Error('No stealth engine available');
}

export function safeUrl(page) {
  try {
    return page.url();
  } catch {
    return null;
  }
}

// Поисковая машина по умолчанию для "сырого" ввода в адресной строке.
const SEARCH_ENGINE = 'https://www.google.com/search?q=';

// Схемы, которые трактуем как полноценный URL без префиксов.
const KNOWN_SCHEMES = /^(https?|ftp|file|ws|wss|about|chrome|view-source|javascript|data|mailto|tel|sftp):\/?\/?/i;

// IPv4 / [IPv6] / localhost[:port][/path]
const HOSTLIKE = /^(localhost|(\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:]+\])(:\d+)?(\/.*)?$/i;

// Домен вида example.com[:port][/path] — минимум одна точка,
// последняя метка ≥ 2 алфавитно-цифровых символа, без пробелов.
const DOMAIN_LIKE = /^([a-z0-9-]+\.)+[a-z]{2,}(:\d+)?(\/.*)?(\?.*)?$/i;

/**
 * Приводит пользовательский ввод из адресной строки к валидному URL.
 *   - "https://google.com"          → "https://google.com"
 *   - "google.com"                  → "https://google.com"
 *   - "localhost:3000"              → "http://localhost:3000"
 *   - "192.168.0.1"                 → "http://192.168.0.1"
 *   - "привет" / "cats" / "google"  → "https://www.google.com/search?q=..."
 *   - ""                            → null
 */
export function normalizeLaunchMode(options = {}) {
  const requested = typeof options.launchMode === 'string' ? options.launchMode.trim().toLowerCase() : null;
  if (requested && LAUNCH_MODES.has(requested)) return requested;

  const legacyHeadless = options.headless === true || options.isHeadless === true;
  return legacyHeadless ? 'headless' : 'visible';
}

export function buildLaunchPolicy(launchMode) {
  switch (launchMode) {
    case 'headless':
      return {
        headless: true,
        backgroundApplied: false,
        focusSuppressed: true,
        postLaunchAction: null,
        microWarmup: false,
        windowPosition: null,
        extraArgs: [],
      };
    case 'minimized':
      return {
        headless: false,
        backgroundApplied: true,
        focusSuppressed: true,
        postLaunchAction: 'minimize',
        microWarmup: false,
        windowPosition: { x: 32, y: 32 },
        extraArgs: [],
      };
    case 'background':
      return {
        headless: false,
        backgroundApplied: true,
        focusSuppressed: true,
        postLaunchAction: 'minimize',
        microWarmup: true,
        // Windows клампит слишком большие отрицательные значения к видимой области.
        // Позиционируем правее/ниже основного монитора: типичный экран 1920x1080,
        // ставим окно сразу за правым нижним углом.
        // В сочетании с --start-minimized и ранним CDP minimize — окно не появляется.
        windowPosition: { x: 3840, y: 2160 },
        extraArgs: [
          '--start-minimized',
        ],
      };
    case 'visible':
    default:
      return {
        headless: false,
        backgroundApplied: false,
        focusSuppressed: false,
        postLaunchAction: null,
        microWarmup: false,
        windowPosition: null,
        extraArgs: [],
      };
  }
}

export function normalizeNavigationInput(input) {
  if (input == null) return null;
  const raw = String(input).trim();
  if (!raw) return null;

  // Полный URL с протоколом — используем как есть.
  if (KNOWN_SCHEMES.test(raw)) return raw;

  // host-only адреса без схемы
  if (HOSTLIKE.test(raw)) {
    return `http://${raw}`;
  }

  // Доменное имя без схемы (example.com, sub.example.co.uk/page?x=1)
  if (DOMAIN_LIKE.test(raw)) {
    return `https://${raw}`;
  }

  // Всё остальное (пробелы, кириллица, просто слово) — поисковый запрос в Google.
  return SEARCH_ENGINE + encodeURIComponent(raw);
}
