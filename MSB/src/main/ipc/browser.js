import { IPC } from '../core/constants.js';
import fs from 'node:fs';
import path from 'node:path';
import { app } from 'electron';

const STATE_FILE = process.env.CONTROLLER_STATE_FILE
  || path.join(app.getPath('appData'), 'controller', 'state.json');

function saveLastLaunched(profileId) {
  try {
    let state = {};
    if (fs.existsSync(STATE_FILE)) {
      try { state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch {}
    }
    state.lastLaunchedProfileId = profileId;
    state.lastLaunchedAt = Date.now();
    fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), 'utf8');
  } catch (e) {

  }
}

export function registerBrowserHandlers(ipcMain, { profileManager, browserLauncher, logger }) {
  const log = logger?.child?.({ mod: 'ipc:browser' }) || logger || console;

  ipcMain.handle(IPC.BROWSER.START, async (_e, id, options) => {
    const profile = profileManager.get(id);
    if (!profile) throw new Error(`Profile ${id} not found`);
    const result = await browserLauncher.start(profile, options || {});
    log.info?.({ profileId: id, via: 'ipc' }, 'browser start requested via IPC');
    saveLastLaunched(id);
    return result;
  });

  ipcMain.handle(IPC.BROWSER.STOP, async (_e, id) => {
    const ok = await browserLauncher.stop(id);
    log.info?.({ profileId: id, ok, via: 'ipc' }, 'browser stop requested via IPC');
    return ok;
  });

  ipcMain.handle(IPC.BROWSER.STATUS, () => browserLauncher.status());
  ipcMain.handle(IPC.BROWSER.GOTO, (_e, id, url) => browserLauncher.goto(id, url));

  ipcMain.handle(IPC.BROWSER.SCENARIO, (_e, id, name, params) => {
    log.info?.({ profileId: id, scenario: name, via: 'ipc' }, 'scenario requested via IPC');
    return browserLauncher.runScenario(id, name, params || {});
  });

  ipcMain.handle(IPC.BROWSER.EVAL, (_e, id, script) => {
    log.warn?.({ profileId: id, via: 'ipc' }, 'arbitrary eval requested via IPC');
    return browserLauncher.evaluate(id, script);
  });
}
