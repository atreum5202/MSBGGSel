import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(__dirname, '..', '..', '..');


export const MSB_DATA_DIR = process.env.MSB_DATA_DIR || 
  (process.env.APPDATA 
    ? path.join(process.env.APPDATA, 'MSB') 
    : path.join(os.homedir(), 'AppData', 'Roaming', 'MSB'));

export const paths = {
  root: ROOT,
  preload: path.join(ROOT, 'src', 'preload', 'bridge.cjs'),
  rendererIndex: path.join(ROOT, 'dist', 'renderer', 'index.html'),
  profiles: process.env.MSB_PROFILES_DIR || path.join(ROOT, 'profiles'),
  logs: process.env.MSB_LOG_DIR || path.join(ROOT, 'logs'),
  msbDataDir: MSB_DATA_DIR,
};

export function profileDir(profilesDir, id) {
  return path.join(profilesDir, id);
}

export function userDataDir(profilesDir, id) {
  return path.join(profileDir(profilesDir, id), 'userData');
}

export function profileMetaFile(profilesDir, id) {
  return path.join(profileDir(profilesDir, id), 'meta.json');
}

export function profileIndexFile(profilesDir) {
  return path.join(profilesDir, 'index.json');
}

export function commonExtensionsFile(profilesDir) {
  return path.join(profilesDir, 'common-extensions.json');
}

// MSB Profile Badge — встроенное Chrome-расширение, которое показывает
// плашку "<номер> | <email>" в тулбаре и в углу страницы.
// Грузится автоматически во все профили при старте (если не отключено
// через profile.badge === false).
export function badgeExtensionPath() {
  return path.join(ROOT, 'extensions', 'msb-profile-badge');
}
