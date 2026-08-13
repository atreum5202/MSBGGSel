import path from 'node:path';
import { app } from 'electron';

export const DOWNLOADS_DIR = process.env.MSB_DOWNLOADS_DIR || app.getPath('downloads');

export const COMMON_ARGS = [
  // Антидетект (критично для Google)
  '--disable-blink-features=AutomationControlled',
  '--exclude-switches=enable-automation',
  '--disable-automation',

  // Изоляция / sandbox
  '--disable-features=IsolateOrigins',

  // Первый запуск
  '--no-first-run',
  '--no-default-browser-check',
  '--no-service-autorun',

  // Хранилище паролей
  '--password-store=basic',
  '--use-mock-keychain',

  // Автозаполнение
  '--enable-features=PasswordImport,NetworkService,NetworkServiceInProcess,PasswordSaving,AutofillEnableAccountStorageForScreenReader',
  '--disable-features=PasswordLeakDetection',

  // WebRTC
  '--webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--force-webrtc-ip-handling-policy',

  // Приватность / телеметрия
  '--no-pings',
  '--disable-client-side-phishing-detection',
  '--disable-sync',
  '--disable-breakpad',
  '--disable-domain-reliability',
  '--disable-component-update',

  // Производительность
  '--disable-backgrounding-occluded-windows',
  '--disable-background-mode',
  '--disable-background-networking',
  '--disable-dev-shm-usage',

  // Сертификаты
  '--ignore-certificate-errors',

  // CDP / отладка
  '--remote-debugging-address=127.0.0.1',

  // Скролл
  '--enable-smooth-scrolling',
  '--enable-features=TouchpadAndWheelScrollLatching,AsyncWheelEvents',

  // Адресная строка: поиск через Google при вводе текста без схемы.
  // Флаги работают в связке с ensurePrefs() которая пишет правильную структуру
  // в Default/Preferences до запуска браузера. Флаги — fallback на случай если
  // Preferences ещё не существует (первый запуск профиля).
  '--default-search-provider-enabled',
  '--default-search-provider-name=Google',
  '--default-search-provider-search-url=https://www.google.com/search?q={searchTerms}',
  '--default-search-provider-suggest-url=https://www.google.com/complete/search?output=chrome&q={searchTerms}',
];

// Аргументы которые Playwright/Patchright добавляет по-умолчанию и которые
// нужно исключить чтобы Google не детектировал автоматизацию
export const IGNORE_DEFAULT_ARGS = [
  '--enable-automation',
  '--disable-extensions',
  '--disable-default-apps',
  '--disable-component-extensions-with-background-pages',
  '--disable-hang-monitor',
  '--disable-prompt-on-repost',
  '--disable-popup-blocking',
  '--metrics-recording-only',
  '--safebrowsing-disable-auto-update',
];

// Аргументы, которые прокидываются в CloakBrowser (в дополнение к его
// бинарным fingerprint-патчам). CloakBrowser управляет антидетект-CDP/JS
// флагами сам (через stealthArgs), поэтому сюда НЕ включаем:
//   --disable-blink-features=AutomationControlled
//   --exclude-switches=enable-automation
//   --disable-automation
//   --disable-features=IsolateOrigins|PasswordLeakDetection
//   --enable-features=PasswordImport|...
//
// Сюда входит только то, что Cloak НЕ покрывает на уровне бинаря и что
// критично для runtime-поведения: WebRTC-leak, default search provider,
// шум от первого запуска, телеметрия, сертификаты.
export const CLOAK_COMMON_ARGS = [
  // WebRTC: блокируем утечку реального IP при работе через SOCKS5/HTTP-прокси
  '--webrtc-ip-handling-policy=disable_non_proxied_udp',
  '--force-webrtc-ip-handling-policy',

  // Адресная строка: при вводе текста без схемы отправляем в Google.
  // В коде есть и ensurePrefs() который пишет в Default/Preferences, но
  // флаги нужны как fallback для первого запуска когда Preferences ещё нет.
  '--default-search-provider-enabled',
  '--default-search-provider-name=Google',
  '--default-search-provider-search-url=https://www.google.com/search?q={searchTerms}',
  '--default-search-provider-suggest-url=https://www.google.com/complete/search?output=chrome&q={searchTerms}',

  // Первый запуск / дефолтный браузер: убираем промпты и welcome screen
  '--no-first-run',
  '--no-default-browser-check',
  '--no-service-autorun',

  // Хранилище паролей: не дёргаем keychain (Mac) и используем basic-сторе
  '--password-store=basic',
  '--use-mock-keychain',

  // Приватность / телеметрия
  '--no-pings',
  '--disable-client-side-phishing-detection',
  '--disable-sync',
  '--disable-breakpad',
  '--disable-domain-reliability',
  '--disable-component-update',

  // Производительность / окружение
  '--disable-backgrounding-occluded-windows',
  '--disable-background-mode',
  '--disable-background-networking',
  '--disable-dev-shm-usage',
  '--ignore-certificate-errors',
  '--enable-smooth-scrolling',
  '--enable-features=TouchpadAndWheelScrollLatching,AsyncWheelEvents',
];
