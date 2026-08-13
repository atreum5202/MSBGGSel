const { contextBridge, ipcRenderer } = require('electron');

const invoke = (channel, ...args) => ipcRenderer.invoke(channel, ...args);

contextBridge.exposeInMainWorld('msb', {
  profiles: {
    list: () => invoke('msb:profiles:list'),
    get: (id) => invoke('msb:profiles:get', id),
    create: (data) => invoke('msb:profiles:create', data),
    update: (id, patch) => invoke('msb:profiles:update', id, patch),
    remove: (id) => invoke('msb:profiles:delete', id),
    exportJson: (id) => invoke('msb:profiles:export', id),
    importJson: (json) => invoke('msb:profiles:import', json),
    importLegacyBulk: (text) => invoke('msb:profiles:import-legacy-bulk', text),
  },
  browser: {
    start: (id, options) => invoke('msb:browser:start', id, options || {}),
    stop: (id) => invoke('msb:browser:stop', id),
    status: () => invoke('msb:browser:status'),
    goto: (id, url) => invoke('msb:browser:goto', id, url),
    runScenario: (id, name, params) => invoke('msb:browser:runScenario', id, name, params || {}),
    evaluate: (id, script) => invoke('msb:browser:eval', id, script),
  },
  diagnostics: {
    selfTest: (id) => invoke('msb:diagnostics:selfTest', id),
  },
  widget: {
    show: (id) => invoke('msb:widget:show', id),
    hide: () => invoke('msb:widget:hide'),
    navigate: (url) => invoke('msb:widget:navigate', url),
  },
  extensions: {
    list:       ()                  => invoke('msb:extensions:list'),
    add:        (path)              => invoke('msb:extensions:add', path),
    remove:     (path)              => invoke('msb:extensions:remove', path),
    clear:      ()                  => invoke('msb:extensions:clear'),
    pickFolder: ()                  => invoke('msb:extensions:pick-folder'),
    installCrx: (name, data)        => invoke('msb:extensions:install-crx', { name, data }),
    addTampermonkey: ()             => invoke('msb:extensions:add-tampermonkey'),
  },
  scrapers: {
    list:       ()                  => invoke('msb:scrapers:list'),
    get:        (id)                => invoke('msb:scrapers:get', id),
    readText:   (relPath)           => invoke('msb:scrapers:read-text', relPath),
    readJsonl:  (scraperId, file, limit) => invoke('msb:scrapers:read-jsonl', { scraperId, file, limit }),
    openPath:   (relPath)           => invoke('msb:scrapers:open-path', relPath),
    run:        (scraperId, command)=> invoke('msb:scrapers:run', { scraperId, command }),
    readOutput: (logFile, offset)   => invoke('msb:scrapers:read-output', { logFile, offset }),
    kill:       (scraperId)         => invoke('msb:scrapers:kill', { scraperId }),
  },
  traffic: {
    openTerminal: (opts) => invoke('msb:traffic:open-terminal', opts || {}),
  },
});
