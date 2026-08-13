import { trySend } from '../../lib/ws.js';

export function registerLogsRoutes({ app, logBroker }, wsAuth) {
  app.get('/ws/logs', { websocket: true }, (socket, req) => {
    if (!wsAuth(req)) {
      socket.close(4401, 'unauthorized');
      return;
    }
    const scope = req.query?.scope || 'app';
    for (const entry of logBroker.history(scope)) {
      trySend(socket, { historical: true, entry });
    }
    const listener = (entry) => trySend(socket, { entry });
    logBroker.on(`log:${scope}`, listener);
    socket.on('close', () => logBroker.off(`log:${scope}`, listener));
  });
}
