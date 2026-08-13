import { app, BrowserWindow, ipcMain, dialog, Tray, Menu, nativeImage } from 'electron';
import path from 'node:path';
import fs from 'node:fs';
import { paths } from './core/paths.js';
import { LogBroker, createRootLogger } from './core/logger.js';
import { ProfileManager } from './services/profileManager.js';import { ProxyStore } from './services/proxyStore.js';
import { BrowserLauncher } from './services/browserLauncher/index.js';
import { Supervisor } from './services/supervisor.js';
import { CookieStore } from './services/cookieStore.js';
import { Statistics } from './services/statistics.js';
import { GeoIP } from './services/geoip.js';
import { startApiServer } from './api/server.js';
import { createShutdownController } from './core/shutdown.js';
import { CommonExtensionsManager } from './services/commonExtensionsManager.js';
import { registerIpcHandlers } from './ipc/registry.js';
import { createMainWindow } from './ui/window.js';
import { TrafficCaptureService } from './services/trafficCapture.js';
import { NetworkCaptureService } from './services/networkCapture.js';
import { AutomationService } from './services/automationService.js';
import { CrawlerService } from './services/crawlerService.js';

const isDev = !app.isPackaged && process.env.NODE_ENV !== 'production';

// MSB_SILENT=1 — запуск без консоли (через VBS), окно Electron показывается,
// но закрытие крестиком прячет в трей, а не убивает процесс.
const isSilent = process.env.MSB_SILENT === '1' || process.env.MSB_SILENT === 'true';

let mainWindow = null;
let tray = null;
let profileManager = null;
let browserLauncher = null;
let supervisor = null;
let logBroker = null;
let appLogger = null;
let statistics = null;
let cookieStore = null;
let proxyStore = null;
let geoip = null;
let apiServer = null;
let shutdownController = null;
let commonExtensionsManager = null;

// Принудительно один экземпляр. Если второй запуск — показываем окно первого.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
}

app.on('second-instance', () => {
  showWindow();
});

function showWindow() {
  if (!mainWindow) {
    mainWindow = createMainWindow({ isDev });
    setupWindowEvents();
  } else if (mainWindow.isDestroyed()) {
    mainWindow = createMainWindow({ isDev });
    setupWindowEvents();
  } else {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }
}

function setupWindowEvents() {
  if (!mainWindow) return;

  mainWindow.on('close', (event) => {
    // В любом режиме крестик только скрывает окно, не убивает процесс
    if (!shutdownController?.isShuttingDown()) {
      event.preventDefault();
      mainWindow.hide();
      if (tray) {
        tray.displayBalloon?.({
          title: 'MSB',
          content: 'MSB работает в фоне. Нажми на иконку в трее чтобы открыть.',
          noSound: true,
        });
      }
    }
  });
}

function createTray() {
  // Try to load build/icon.ico; fall back to generated RGBA icon
  let trayIcon;
  try {
    const icoPath = path.join(paths.root, 'build', 'icon.ico');
    if (fs.existsSync(icoPath)) {
      trayIcon = nativeImage.createFromPath(icoPath);
    }
  } catch {
    // ignore
  }

  if (!trayIcon || trayIcon.isEmpty()) {
    try {
      const size = 16;
      const buf = Buffer.alloc(size * size * 4);
      for (let y = 0; y < size; y++) {
        for (let x = 0; x < size; x++) {
          const i = (y * size + x) * 4;
          // Blue background (#2F6FED) — BGRA order for Windows
          buf[i]     = 237;  // B
          buf[i + 1] = 111;  // G
          buf[i + 2] = 47;   // R
          buf[i + 3] = 255;  // A
          // Letter 'M' in white — vertical stripes + diagonals
          const inM =
            (x === 2 && y >= 3 && y <= 12) ||
            (x === 13 && y >= 3 && y <= 12) ||
            (x === 7 && y >= 3 && y <= 7) ||
            (x === 8 && y >= 3 && y <= 7) ||
            (y === x - 1 && x >= 2 && x <= 7) ||
            (y === 15 - x && x >= 8 && x <= 13);
          if (inM) {
            buf[i]     = 255;
            buf[i + 1] = 255;
            buf[i + 2] = 255;
          }
        }
      }
      trayIcon = nativeImage.createFromBuffer(buf, { width: size, height: size });
    } catch {
      trayIcon = nativeImage.createEmpty();
    }
  }

  tray = new Tray(trayIcon);
  tray.setToolTip('MSB - Stealth Browser');

  const contextMenu = Menu.buildFromTemplate([
    {
      label: 'Open MSB',
      click: () => showWindow(),
    },
    { type: 'separator' },
    {
      label: 'Stop MSB',
      click: () => {
        shutdownController?.performShutdown({ exit: true }).catch(() => app.exit(0));
      },
    },
  ]);

  tray.setContextMenu(contextMenu);
  tray.on('click', () => showWindow());
  tray.on('double-click', () => showWindow());
}

async function bootstrap() {
  // Write PID file so external tools can track / stop the process
  try {
    const pidFile = path.join(paths.logs, 'msb.pid');
    fs.writeFileSync(pidFile, String(process.pid), 'utf8');
  } catch {
    // non-fatal — log not yet available
  }

  logBroker = new LogBroker();
  appLogger = createRootLogger({ dir: paths.logs, broker: logBroker });

  profileManager = new ProfileManager({ profilesDir: paths.profiles, logger: appLogger });
  await profileManager.init();

  proxyStore = new ProxyStore({ logger: appLogger });
  await proxyStore.init();

  statistics = new Statistics({ profilesDir: paths.profiles, logger: appLogger });

  commonExtensionsManager = new CommonExtensionsManager({ profilesDir: paths.profiles, logger: appLogger });
  await commonExtensionsManager.init();
  cookieStore = new CookieStore({ profilesDir: paths.profiles, logger: appLogger });
  geoip = new GeoIP({ cacheFile: path.join(paths.logs, 'geoip-cache.json'), logger: appLogger });

  supervisor = new Supervisor({ browserLauncher: null, logger: appLogger });
  browserLauncher = new BrowserLauncher({ profileManager, statistics, supervisor, cookieStore, commonExtensionsManager, logger: appLogger });
  supervisor.browserLauncher = browserLauncher;

  // Built-in traffic + network capture services
  const trafficCapture  = new TrafficCaptureService({ profileManager, browserLauncher, logger: appLogger });
  const networkCapture  = new NetworkCaptureService({ profileManager, browserLauncher, logger: appLogger });
  const crawler         = new CrawlerService({ profileManager, browserLauncher, logger: appLogger });
  const automation      = new AutomationService({ profileManager, browserLauncher, trafficCapture, networkCapture, crawlerService: crawler, logger: appLogger });

  shutdownController = createShutdownController({
    getBrowserLauncher: () => browserLauncher,
    getApiServer: () => apiServer,
    logger: appLogger,
  });

  try {
    apiServer = await startApiServer({
      profileManager,
      browserLauncher,
      logBroker,
      statistics,
      cookieStore,
      profilesDir: paths.profiles,
      logger: appLogger,
      shutdownController,
      commonExtensionsManager,
      getMainWindow: () => mainWindow,
      showWindow,
      proxyStore,
      trafficCapture,
      networkCapture,
      automation,
      crawler,
    });
  } catch (err) {
    if (err.code === 'EADDRINUSE') {
      // Уже запущен — покажем окно через second-instance и выйдем
      appLogger.warn({ err: err.message }, 'MSB уже запущен (порт занят), показываем существующее окно');
      app.exit(0);
      return;
    }
    appLogger.error({ err: err.message, stack: err.stack }, 'failed to start REST API');
  }

  registerIpcHandlers(ipcMain, {
    profileManager,
    browserLauncher,
    commonExtensionsManager,
    getMainWindow: () => mainWindow,
    logger: appLogger,
    dialog,
  });

  // Всегда создаём трей, чтобы приложение оставалось доступным в фоне
  createTray();

  // В silent режиме окно создаётся скрытым (show: false) — открывается только через трей
  mainWindow = createMainWindow({ isDev, show: !isSilent });
  setupWindowEvents();

  app.on('activate', () => {
    showWindow();
  });
}

app.whenReady().then(bootstrap).catch((err) => {
  (appLogger || console).error({ err: err.message, stack: err.stack }, 'bootstrap failed');
  app.exit(1);
});

// Никогда не убиваем приложение при закрытии всех окон —
// оно продолжает жить (API + трей)
app.on('window-all-closed', () => {
  // intentionally empty — process stays alive
});

app.on('before-quit', async (event) => {
  if (shutdownController?.isShuttingDown()) return;
  if (browserLauncher?.hasRunning?.()) {
    event.preventDefault();
    await shutdownController.performShutdown({ exit: true });
  }
});
