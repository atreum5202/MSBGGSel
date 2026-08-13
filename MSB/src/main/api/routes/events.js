import { trySend } from '../../lib/ws.js';
import { eventBus } from '../../lib/eventBus.js';

const ALL_EVENTS = [
  'profile:created',
  'profile:updated',
  'profile:deleted',
  'profile:trashed',
  'profile:restored',
  'cookies:imported',
  'cookies:cleared',
];

export function registerEventsRoute({ app }, wsAuth) {
  app.get('/ws/events', { websocket: true }, (socket, req) => {
    if (!wsAuth(req)) {
      socket.close(4401, 'unauthorized');
      return;
    }

    // Приветственное сообщение — клиент знает что соединение живо
    trySend(socket, { type: 'connected', ts: Date.now() });

    // Keepalive ping каждые 25 сек чтобы прокси не рвали idle-соединение
    const pingInterval = setInterval(() => {
      trySend(socket, { type: 'ping', ts: Date.now() });
    }, 25000);

    const listeners = {};

    for (const event of ALL_EVENTS) {
      const listener = (payload) => trySend(socket, { type: event, ...payload, ts: Date.now() });
      listeners[event] = listener;
      eventBus.on(event, listener);
    }

    socket.on('close', () => {
      clearInterval(pingInterval);
      for (const event of ALL_EVENTS) {
        eventBus.off(event, listeners[event]);
      }
    });
  });
}
