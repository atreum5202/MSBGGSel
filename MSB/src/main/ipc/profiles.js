import { IPC } from '../core/constants.js';
import { importLegacyBulkExport } from '../services/legacyProfileImport.js';

export function registerProfileHandlers(ipcMain, { profileManager, browserLauncher, cookieStore, logger }) {
  const log = logger?.child?.({ mod: 'ipc:profiles' }) || logger || console;

  ipcMain.handle(IPC.PROFILES.LIST, () => {
    const result = profileManager.list();
    log.info?.({ count: result.count, via: 'ipc' }, 'profiles list requested via IPC');
    return result;
  });

  ipcMain.handle(IPC.PROFILES.GET, (_e, id) => profileManager.get(id));

  ipcMain.handle(IPC.PROFILES.CREATE, async (_e, data) => {
    const created = await profileManager.create(data);
    log.info?.({ profileId: created.id, via: 'ipc' }, 'profile created via IPC');
    return created;
  });

  ipcMain.handle(IPC.PROFILES.UPDATE, async (_e, id, patch) => {
    const updated = await profileManager.update(id, patch);
    log.debug?.({ profileId: id, fields: Object.keys(patch || {}), via: 'ipc' }, 'profile updated via IPC');
    return updated;
  });

  ipcMain.handle(IPC.PROFILES.DELETE, async (_e, id) => {
    if (browserLauncher.isRunning(id)) await browserLauncher.stop(id);
    const ok = await profileManager.remove(id);
    if (ok) log.info?.({ profileId: id, via: 'ipc' }, 'profile deleted via IPC');
    return ok;
  });

  ipcMain.handle(IPC.PROFILES.EXPORT, (_e, id) => profileManager.exportJson(id));

  ipcMain.handle(IPC.PROFILES.IMPORT, async (_e, json) => {
    const imported = await profileManager.importJson(json);
    log.info?.({ profileId: imported.id, via: 'ipc' }, 'profile imported via IPC');
    return imported;
  });

  ipcMain.handle(IPC.PROFILES.IMPORT_LEGACY_BULK, async (_e, text) => {
    const result = await importLegacyBulkExport(text, { profileManager, cookieStore, logger: log });
    log.info?.(
      { imported: result.imported.length, errors: result.errors.length, via: 'ipc' },
      'legacy bulk profile import via IPC'
    );
    return result;
  });
}
