// Хранилище результатов регистраций в формате JSONL (один JSON на строку, append).
// Файл: scratch/results.jsonl

import { appendFile, readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const RESULTS_FILE = join(__dirname, '..', 'results.jsonl');

/**
 * Записать результат одного профиля.
 * @param {{ profileId: string, email: string, status: 'success'|'error'|'skipped', error?: string|null, ggselUserId?: string|null }} opts
 */
export async function saveResult({ profileId, email, status, error = null, ggselUserId = null }) {
  const record = {
    profileId,
    email,
    status,          // 'success' | 'error' | 'skipped'
    error,
    ggselUserId,
    registeredAt: new Date().toISOString(),
  };
  await appendFile(RESULTS_FILE, JSON.stringify(record) + '\n', 'utf-8');
}

/**
 * Загрузить все результаты из JSONL.
 * @returns {Promise<Array>}
 */
export async function loadResults() {
  if (!existsSync(RESULTS_FILE)) return [];
  const raw = await readFile(RESULTS_FILE, 'utf-8');
  return raw
    .trim()
    .split('\n')
    .filter(Boolean)
    .map(line => JSON.parse(line));
}

/**
 * Проверить — был ли профиль уже успешно зарегистрирован.
 * @param {string} profileId
 * @returns {Promise<boolean>}
 */
export async function isAlreadyRegistered(profileId) {
  const results = await loadResults();
  return results.some(r => r.profileId === profileId && r.status === 'success');
}

/**
 * Получить путь к файлу результатов (для логов).
 * @returns {string}
 */
export function getResultsFilePath() {
  return RESULTS_FILE;
}
