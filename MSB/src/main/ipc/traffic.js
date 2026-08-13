/**
 * traffic.js — IPC handlers для раздела "Перехват трафика"
 *
 * Открывает терминал с mitmproxy/mitmweb в отдельном видимом окне.
 */

import { IPC } from '../core/constants.js';
import { spawn } from 'node:child_process';

/**
 * Открыть терминал и запустить mitmproxy (или mitmweb) в нём.
 * На Windows — cmd.exe /K mitmproxy (или mitmweb --web-open-browser)
 * На macOS  — osascript + Terminal.app
 * На Linux  — x-terminal-emulator / gnome-terminal / xterm
 *
 * @param {string} [bin]  — 'mitmproxy' | 'mitmweb' | 'mitmdump'
 * @param {string[]} [args] — дополнительные аргументы
 */
function openTerminalWithMitmproxy(bin = 'mitmproxy', extraArgs = []) {
  const args = [...extraArgs];
  const cmd  = [bin, ...args].join(' ');

  if (process.platform === 'win32') {
    // Открываем cmd.exe, /K — не закрывать окно после завершения
    spawn('cmd.exe', ['/C', 'start', 'cmd.exe', '/K', cmd], {
      detached: true,
      stdio: 'ignore',
      shell: false,
    }).unref();
  } else if (process.platform === 'darwin') {
    spawn('osascript', [
      '-e',
      `tell application "Terminal" to do script "${cmd.replace(/"/g, '\\"')}"`,
    ], {
      detached: true,
      stdio: 'ignore',
    }).unref();
  } else {
    // Linux: пробуем несколько эмуляторов по очереди
    const terminals = [
      ['x-terminal-emulator', ['-e', `bash -c '${cmd}; exec bash'`]],
      ['gnome-terminal',      ['--', 'bash', '-c', `${cmd}; exec bash`]],
      ['xterm',               ['-e', `bash -c '${cmd}; exec bash'`]],
      ['konsole',             ['-e', `bash -c '${cmd}; exec bash'`]],
    ];
    let launched = false;
    for (const [term, termArgs] of terminals) {
      try {
        spawn(term, termArgs, { detached: true, stdio: 'ignore' }).unref();
        launched = true;
        break;
      } catch { /* попробуем следующий */ }
    }
    if (!launched) throw new Error('No suitable terminal emulator found');
  }
}

export function registerTrafficHandlers(ipcMain, { logger } = {}) {
  const log = logger?.child?.({ mod: 'ipc:traffic' }) || logger || console;

  ipcMain.handle(IPC.TRAFFIC.OPEN_TERMINAL, async (_e, opts = {}) => {
    const bin       = opts.bin       || process.env.MITMPROXY_BIN || 'mitmproxy';
    const extraArgs = opts.extraArgs || [];

    log.info?.({ bin, extraArgs }, 'opening terminal with mitmproxy');

    try {
      openTerminalWithMitmproxy(bin, extraArgs);
      return { ok: true, bin };
    } catch (err) {
      log.error?.({ err: err.message }, 'failed to open terminal');
      throw new Error(`Не удалось открыть терминал: ${err.message}`);
    }
  });
}
