export const DEFAULTS = {
  API_PORT: 17248,
  API_HOST: '127.0.0.1',
  PROFILES_DIR: './profiles',
  LOG_LEVEL: 'info',
  LOG_DIR: './logs',
  START_URL: 'https://www.google.com',
  WIDGET_SIDEBAR: 340,
  WIDGET_HEADER: 56,
  RING_SIZE: 500,
  WS_MAX_PAYLOAD: 1_048_576,

  API_BODY_LIMIT: 50 * 1_048_576,
  CDP_WAIT_ATTEMPTS: 50,
  CDP_WAIT_INTERVAL_MS: 300,
  NAV_TIMEOUT_MS: 60_000,
  GEOIP_TTL_MS: 24 * 60 * 60 * 1000,
  SUPERVISOR_MAX_RETRIES: 3,
  SUPERVISOR_WINDOW_MS: 5 * 60_000,
  SCREENCAST_QUALITY: 60,
  SCREENCAST_FORMAT: 'jpeg',

  // API rate limiting (MoreLogin parity: 60 req/min per IP+token key).
  // Override with MSB_RATE_LIMIT env var to disable/change.
  RATE_LIMIT_WINDOW_MS: 60_000,
  RATE_LIMIT_MAX_REQUESTS: 0, // Отключено по умолчанию

  // ─── AI Workspace ───────────────────────────────────────────────────────
  // Один клик в MSB: создаёт/берёт специальный профиль, поднимает Flask
  // (GGselMSB) и открывает его в браузере профиля. Нужен, чтобы
  // ИИ-агент мог ходить в локальную панель через антидетект-профиль.
  //
  // Override через env (полезно, если проект перенесён):
  //   MSB_GGSELLER_PROJECT_DIR — путь к GGselMSB (по умолчанию
  //     C:\Users\Atreum\Desktop\MySoft\GGselMSB)
  //   MSB_GGSELLER_URL — стартовая страница проекта (по умолчанию
  //     http://127.0.0.1:5000)
  GGSELLER_PROJECT_DIR: 'C:\\Users\\Atreum\\Desktop\\MSBWorkshop\\GGselMSB',
  GGSELLER_URL: 'http://127.0.0.1:5000',
  GGSELLER_PYTHON: 'python',           // executable name в PATH
  GGSELLER_START_CMD: 'app.py',         // какой файл запускать
  GGSELLER_HEALTH_TIMEOUT_MS: 25_000,  // ждать поднятия Flask
  GGSELLER_HEALTH_INTERVAL_MS: 400,
  GGSELLER_PROFILE_NAME: 'AI Workspace',
  GGSELLER_PROFILE_GROUP: 'AI Workspace',
  GGSELLER_PROFILE_TAGS: ['ai-workspace'],
};

export const ENGINES = {
  AUTO: 'auto',
  CLOAK: 'cloakbrowser',
  PATCH: 'patchright',
};

export const PROXY_PROTOCOLS = ['http', 'https', 'socks4', 'socks5'];

export const IPC = {
  PROFILES: {
    LIST: 'msb:profiles:list',
    GET: 'msb:profiles:get',
    CREATE: 'msb:profiles:create',
    UPDATE: 'msb:profiles:update',
    DELETE: 'msb:profiles:delete',
    EXPORT: 'msb:profiles:export',
    IMPORT: 'msb:profiles:import',
    IMPORT_LEGACY_BULK: 'msb:profiles:import-legacy-bulk',
  },
  BROWSER: {
    START: 'msb:browser:start',
    STOP: 'msb:browser:stop',
    STATUS: 'msb:browser:status',
    GOTO: 'msb:browser:goto',
    SCENARIO: 'msb:browser:runScenario',
    EVAL: 'msb:browser:eval',
  },
  DIAG: {
    SELF_TEST: 'msb:diagnostics:selfTest',
  },
  WIDGET: {
    SHOW: 'msb:widget:show',
    HIDE: 'msb:widget:hide',
    NAV: 'msb:widget:navigate',
  },
  SCRAPERS: {
    LIST:       'msb:scrapers:list',
    GET:        'msb:scrapers:get',
    READ_TEXT:  'msb:scrapers:read-text',
    READ_JSONL: 'msb:scrapers:read-jsonl',
    OPEN_PATH:  'msb:scrapers:open-path',
    RUN:        'msb:scrapers:run',
    READ_OUTPUT:'msb:scrapers:read-output',
    KILL:       'msb:scrapers:kill',
  },
  TRAFFIC: {
    OPEN_TERMINAL: 'msb:traffic:open-terminal',
  },
};

export const COMMON_EXTENSIONS_FILE = 'common-extensions.json';

export const IPC_EXTENSIONS = {
  LIST:        'msb:extensions:list',
  ADD:         'msb:extensions:add',
  REMOVE:      'msb:extensions:remove',
  CLEAR:       'msb:extensions:clear',
  PICK_FOLDER: 'msb:extensions:pick-folder',
  INSTALL_CRX: 'msb:extensions:install-crx',
  ADD_TM:      'msb:extensions:add-tampermonkey',
};
