import { registerProfileHandlers } from './profiles.js';
import { registerBrowserHandlers } from './browser.js';
import { registerDiagnosticsHandlers } from './diagnostics.js';
import { registerWidgetHandlers } from './widget.js';
import { registerExtensionHandlers } from './extensions.js';
import { registerScraperHandlers } from './scrapers.js';
import { registerTrafficHandlers } from './traffic.js';
import { ALLOWED_CHANNELS, ALLOWED_EVENT_CHANNELS } from './allowedChannels.js';

function guardIpcMain(realIpcMain) {
  return {
    handle(channel, fn) {
      if (!ALLOWED_CHANNELS.includes(channel)) {
        throw new Error(
          `IPC channel "${channel}" is not in IPC constants (src/main/core/constants.js). ` +
          `Add it there first if this is intentional.`
        );
      }
      return realIpcMain.handle(channel, fn);
    },
    on(channel, fn) {
      if (!ALLOWED_EVENT_CHANNELS.includes(channel)) {
        throw new Error(
          `IPC event channel "${channel}" is not in ALLOWED_EVENT_CHANNELS (src/main/ipc/allowedChannels.js).`
        );
      }
      return realIpcMain.on(channel, fn);
    },
    removeHandler: (...args) => realIpcMain.removeHandler(...args),
  };
}

export function registerIpcHandlers(ipcMain, ctx) {
  const guarded = guardIpcMain(ipcMain);
  registerProfileHandlers(guarded, ctx);
  registerBrowserHandlers(guarded, ctx);
  registerDiagnosticsHandlers(guarded, ctx);
  registerWidgetHandlers(guarded, ctx);
  registerExtensionHandlers(guarded, ctx);
  registerScraperHandlers(guarded, ctx);
  registerTrafficHandlers(guarded, ctx);
}
