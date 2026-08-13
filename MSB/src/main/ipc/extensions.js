import path from 'node:path';
import fs from 'node:fs/promises';
import os from 'node:os';
import { IPC_EXTENSIONS } from '../core/constants.js';

const MSB_APPDATA = process.env.MSB_APPDATA
  || path.join(os.homedir(), 'AppData', 'Roaming', 'MSB');
const BUNDLED_EXT_DIR = path.join(MSB_APPDATA, 'extensions');

export function registerExtensionHandlers(ipcMain, { commonExtensionsManager, logger, dialog }) {
  const log = logger?.child?.({ mod: 'ipc:extensions' }) || logger || console;

  ipcMain.handle(IPC_EXTENSIONS.LIST, () => {
    return commonExtensionsManager.list();
  });

  ipcMain.handle(IPC_EXTENSIONS.ADD, async (_e, extPath) => {
    if (!extPath || typeof extPath !== 'string') return { added: false, error: 'invalid path' };
    const result = await commonExtensionsManager.add(extPath);
    log.info?.({ extPath, added: result.added }, 'extension add via IPC');
    return result;
  });

  ipcMain.handle(IPC_EXTENSIONS.REMOVE, async (_e, extPath) => {
    if (!extPath || typeof extPath !== 'string') return { removed: false, error: 'invalid path' };
    const result = await commonExtensionsManager.remove(extPath);
    log.info?.({ extPath, removed: result.removed }, 'extension remove via IPC');
    return result;
  });

  ipcMain.handle(IPC_EXTENSIONS.CLEAR, async () => {
    const result = await commonExtensionsManager.clear();
    log.info?.('extensions cleared via IPC');
    return result;
  });

  ipcMain.handle(IPC_EXTENSIONS.PICK_FOLDER, async (_e) => {
    if (!dialog) return { canceled: true };
    const result = await dialog.showOpenDialog({
      title: 'Выберите папку расширения (должна содержать manifest.json)',
      properties: ['openDirectory'],
      buttonLabel: 'Выбрать расширение',
    });
    if (result.canceled || !result.filePaths?.length) return { canceled: true };
    const folderPath = result.filePaths[0];

    try {
      await fs.access(path.join(folderPath, 'manifest.json'));
    } catch {
      return { canceled: false, error: 'Папка не содержит manifest.json — это не расширение' };
    }
    const addResult = await commonExtensionsManager.add(folderPath);
    log.info?.({ folderPath, added: addResult.added }, 'extension added via folder picker');
    return { canceled: false, path: folderPath, ...addResult };
  });

  ipcMain.handle(IPC_EXTENSIONS.INSTALL_CRX, async (_e, { name, data }) => {
    if (!name || !data) return { installed: false, error: 'missing name or data' };

    try {
      const AdmZip = (await import('adm-zip')).default;
      const buf = Buffer.from(data, 'base64');

      let zipBuf = buf;
      if (buf.slice(0, 4).toString() === 'Cr24') {

        const headerSize = buf.readUInt32LE(8);
        zipBuf = buf.slice(12 + headerSize);
      }
      const extName = name.replace(/\.crx$/i, '').replace(/[^a-zA-Z0-9_-]/g, '_');
      const destDir = path.join(BUNDLED_EXT_DIR, extName);
      await fs.mkdir(destDir, { recursive: true });
      const zip = new AdmZip(zipBuf);
      zip.extractAllTo(destDir, true);

      await fs.access(path.join(destDir, 'manifest.json'));
      const addResult = await commonExtensionsManager.add(destDir);
      log.info?.({ extName, destDir, added: addResult.added }, 'extension installed from CRX');
      return { installed: true, path: destDir, ...addResult };
    } catch (err) {
      log.warn?.({ err: err.message }, 'CRX install failed');
      return { installed: false, error: err.message };
    }
  });

  ipcMain.handle(IPC_EXTENSIONS.ADD_TM, async () => {
    const tmDir = path.join(BUNDLED_EXT_DIR, 'tampermonkey');
    try {
      await fs.access(path.join(tmDir, 'manifest.json'));
    } catch {
      return { added: false, error: `Tampermonkey не найден в ${tmDir}. Установите его вручную.` };
    }
    const result = await commonExtensionsManager.add(tmDir);
    log.info?.({ tmDir, added: result.added }, 'Tampermonkey added via IPC');
    return result;
  });
}
