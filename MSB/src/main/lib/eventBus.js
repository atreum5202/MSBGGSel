import { EventEmitter } from 'node:events';

/**
 * eventBus.js — глобальная шина событий MSB
 *
 * Используется WS-роутом /ws/events для push-уведомлений клиентам.
 *
 * События:
 *   profile:created  { profile }
 *   profile:updated  { id, patch }
 *   profile:deleted  { id }
 *   profile:trashed  { id }
 *   profile:restored { id }
 *   cookies:imported { profileId, imported }
 *   cookies:cleared  { profileId }
 */

class EventBus extends EventEmitter {}

export const eventBus = new EventBus();
eventBus.setMaxListeners(100);
