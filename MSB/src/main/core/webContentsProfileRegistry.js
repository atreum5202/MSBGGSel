const registry = new Map();

export function registerProfileWebContents(webContentsId, profileId) {
  registry.set(webContentsId, profileId);
}

export function unregisterProfileWebContents(webContentsId) {
  registry.delete(webContentsId);
}

export function getProfileIdForWebContents(webContentsId) {
  return registry.get(webContentsId) || null;
}
