import { BrowserWindow, shell, dialog, app } from 'electron';
import path from 'node:path';
import fs from 'node:fs';
import { paths } from '../core/paths.js';

const STATE_FILE = process.env.CONTROLLER_STATE_FILE
  || path.join(app.getPath('appData'), 'controller', 'state.json');

function readLastLaunchedProfileId() {
  try {
    if (fs.existsSync(STATE_FILE)) {
      const state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
      return state.lastLaunchedProfileId || null;
    }
  } catch {}
  return null;
}

export function createMainWindow({ isDev = false, show = true } = {}) {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#FDFDFC',
    autoHideMenuBar: true,
    show,
    webPreferences: {
      preload: paths.preload,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  win.webContents.session.on('will-download', (_event, item) => {
    const defaultPath = path.join(app.getPath('downloads'), item.getFilename());
    const savePath = dialog.showSaveDialogSync(win, {
      title: 'Сохранить файл',
      defaultPath,
      buttonLabel: 'Сохранить',
    });
    if (savePath) {
      item.setSavePath(savePath);
    } else {
      item.cancel();
    }
  });

  const port = process.env.MSB_API_PORT || 17248;
  const lastProfileId = readLastLaunchedProfileId();

  let loadUrl;
  if (isDev && process.env.MSB_VITE_URL) {
    loadUrl = process.env.MSB_VITE_URL;
    win.webContents.openDevTools({ mode: 'detach' });
  } else {
    loadUrl = `http://127.0.0.1:${port}/ui/`;
  }

  if (lastProfileId) {
    loadUrl += `#lastProfile=${encodeURIComponent(lastProfileId)}`;
  }

  win.loadURL(loadUrl);

  return win;
}
