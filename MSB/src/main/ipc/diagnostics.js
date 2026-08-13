import { IPC } from '../core/constants.js';

export function registerDiagnosticsHandlers(ipcMain, { browserLauncher, logger }) {
  const log = logger?.child?.({ mod: 'ipc:diagnostics' }) || logger || console;

  ipcMain.handle(IPC.DIAG.SELF_TEST, async (_e, id) => {
    const result = await browserLauncher.selfTest(id);
    const failed = result.probes?.filter((p) => !p.ok).length || 0;
    log.info?.({ profileId: id, failed, total: result.probes?.length || 0, via: 'ipc' }, 'self-test requested via IPC');
    return result;
  });
}
