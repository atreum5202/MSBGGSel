import { app } from 'electron';

export function createShutdownController({ getBrowserLauncher, getApiServer, logger }) {
  const log = logger || console;
  let shuttingDown = false;
  let shutdownPromise = null;

  async function runShutdownSequence() {
    const browserLauncher = getBrowserLauncher();
    const apiServer = getApiServer();

    try {
      await browserLauncher?.closeAll();
    } catch (err) {
      log.error({ err: err.message }, 'closeAll failed');
    }
    try {
      await apiServer?.close();
    } catch (err) {
      log.error({ err: err.message }, 'apiServer close failed');
    }
  }

  async function performShutdown({ exit = true } = {}) {
    if (!shutdownPromise) {
      shuttingDown = true;
      shutdownPromise = runShutdownSequence();
    }
    await shutdownPromise;
    if (exit) app.exit(0);
  }

  return {
    performShutdown,
    isShuttingDown: () => shuttingDown,
  };
}
