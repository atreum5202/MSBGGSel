import { trySend } from '../../lib/ws.js';

export function registerStatusRoutes({ app, browserLauncher }, wsAuth) {
  app.get('/ws/status', { websocket: true }, (socket, req) => {
    if (!wsAuth(req)) {
      socket.close(4401, 'unauthorized');
      return;
    }

    trySend(socket, { type: 'snapshot', running: browserLauncher.status() });

    const onStarted = ({ id }) => trySend(socket, { type: 'started', id });
    const onStopped = ({ id, crashed }) => trySend(socket, { type: 'stopped', id, crashed: !!crashed });

    browserLauncher.on('started', onStarted);
    browserLauncher.on('stopped', onStopped);

    socket.on('close', () => {
      browserLauncher.off('started', onStarted);
      browserLauncher.off('stopped', onStopped);
    });
  });
}
