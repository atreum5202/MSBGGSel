// MSB API client для scratch-скриптов
// Реальная структура ответа: { ok: true, data: [...] }
// Поля профиля: id, name, group (string|null), account.email, proxy, tags

const MSB_BASE = process.env.MSB_URL || 'http://127.0.0.1:17248';

/**
 * Возвращает все профили из MSB.
 * @returns {Promise<Array>}
 */
export async function getAllProfiles() {
  const r = await fetch(`${MSB_BASE}/profiles`);
  if (!r.ok) throw new Error(`MSB /profiles error: ${r.status} ${r.statusText}`);
  const data = await r.json();
  // MSB возвращает { ok: true, data: [...] }
  return Array.isArray(data) ? data : (data.data ?? []);
}

/**
 * Возвращает профили из конкретной группы MSB.
 * Фильтрует по полю profile.group (строгое равенство).
 * @param {string} groupName
 * @returns {Promise<Array>}
 */
export async function getProfilesByGroup(groupName) {
  const profiles = await getAllProfiles();
  return profiles.filter(p => p.group === groupName);
}

/**
 * Возвращает один профиль по ID.
 * @param {string} id
 * @returns {Promise<Object>}
 */
export async function getProfile(id) {
  const r = await fetch(`${MSB_BASE}/profiles/${id}`);
  if (!r.ok) throw new Error(`MSB /profiles/${id} error: ${r.status} ${r.statusText}`);
  const data = await r.json();
  return data.data ?? data;
}

/**
 * Извлекает email из профиля MSB.
 * Надёжное поле — profile.account.email.
 * Fallback — profile.name (там тоже часто email).
 * @param {Object} profile
 * @returns {string}
 */
export function getProfileEmail(profile) {
  return profile?.account?.email ?? profile?.name ?? `profile-${profile?.id}`;
}

/**
 * Возвращает список всех уникальных групп (без null).
 * Полезно для диагностики.
 * @returns {Promise<string[]>}
 */
export async function listGroups() {
  const profiles = await getAllProfiles();
  const groups = [...new Set(profiles.map(p => p.group).filter(Boolean))];
  return groups.sort();
}
