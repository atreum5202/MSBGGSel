import { IPC } from '../core/constants.js';
import { showWidget, hideWidget, attachStealthScreencast, stopStealthScreencast } from '../ui/widget.js';

export function registerWidgetHandlers(ipcMain, { browserLauncher, getMainWindow, logger }) {
  const log = logger?.child?.({ mod: 'ipc:widget' }) || logger || console;

  ipcMain.handle(IPC.WIDGET.SHOW, async (_e, id) => {
    const win = getMainWindow();
    if (!win) throw new Error('Main window not ready');
    const info = browserLauncher.getRunning(id);
    if (!info) throw new Error(`Profile ${id} is not running`);
    await attachStealthScreencast(win, info.page);
    log.debug?.({ profileId: id, via: 'ipc' }, 'widget shown via IPC');
    return { ok: true };
  });

  ipcMain.handle(IPC.WIDGET.HIDE, async () => {
    const win = getMainWindow();
    if (!win) return;
    await stopStealthScreencast(win);
    hideWidget(win);
    log.debug?.({ via: 'ipc' }, 'widget hidden via IPC');
    return { ok: true };
  });

  ipcMain.handle(IPC.WIDGET.NAV, async (_e, url) => {
    const win = getMainWindow();
    if (!win) throw new Error('Main window not ready');
    showWidget(win, url);
    log.debug?.({ url, via: 'ipc' }, 'widget navigated via IPC');
    return { ok: true };
  });
}
