/**
 * loginDataManager.js
 *
 * Записывает логин/пароль профиля в Login Data (SQLite) Chromium
 * чтобы браузер предлагал автозаполнение на страницах входа.
 *
 * Chrome на Windows шифрует пароли через DPAPI — plaintext BLOB не работает.
 * Решение: вызываем PowerShell для шифрования через DPAPI CurrentUser.
 * Вызывать только когда браузер профиля НЕ запущен.
 */

import path from 'node:path';
import fs from 'node:fs/promises';
import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

// URL-ы для автозаполнения по типу почты
const ORIGIN_URLS = {
  gmail: [
    'https://accounts.google.com',
    'https://accounts.google.com/signin/v2/identifier',
    'https://mail.google.com',
  ],
  outlook: [
    'https://login.microsoftonline.com',
    'https://login.live.com',
    'https://outlook.live.com',
    'https://outlook.office.com',
  ],
};

const CREATE_TABLE_SQL = `
CREATE TABLE IF NOT EXISTS logins (
  id                        INTEGER PRIMARY KEY,
  origin_url                TEXT NOT NULL,
  action_url                TEXT,
  username_element          TEXT,
  username_value            TEXT,
  password_element          TEXT,
  password_value            BLOB,
  submit_element            TEXT,
  signon_realm              TEXT NOT NULL,
  preferred                 INTEGER NOT NULL DEFAULT 0,
  date_created              INTEGER NOT NULL DEFAULT 0,
  blacklisted_by_user       INTEGER NOT NULL DEFAULT 0,
  scheme                    INTEGER NOT NULL DEFAULT 0,
  password_type             INTEGER,
  times_used                INTEGER NOT NULL DEFAULT 0,
  form_data                 BLOB,
  date_synced               INTEGER NOT NULL DEFAULT 0,
  display_name              TEXT,
  icon_url                  TEXT,
  federation_url            TEXT,
  skip_zero_click           INTEGER NOT NULL DEFAULT 0,
  generation_upload_status  INTEGER NOT NULL DEFAULT 0,
  possible_username_pairs   BLOB,
  id_date_password_modified INTEGER NOT NULL DEFAULT 0,
  moving_blocked_for        BLOB,
  date_last_used            INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
  key LONGVARCHAR NOT NULL UNIQUE PRIMARY KEY,
  value LONGVARCHAR
);
`;

/**
 * Шифрует строку через Windows DPAPI (CurrentUser scope).
 * Возвращает Buffer с зашифрованными байтами — именно такой формат хранит Chrome.
 * @param {string} plaintext
 * @returns {Buffer}
 */
function encryptWithDPAPI(plaintext) {
  // Экранируем одинарные кавычки для PowerShell
  const escaped = plaintext.replace(/'/g, "''");

  // Единственная команда: DPAPI Protect → Base64
  const psCmd = `[Convert]::ToBase64String([System.Security.Cryptography.ProtectedData]::Protect([System.Text.Encoding]::UTF8.GetBytes('${escaped}'),$null,[System.Security.Cryptography.DataProtectionScope]::CurrentUser))`;

  const b64 = execSync(
    `powershell -NonInteractive -NoProfile -Command "${psCmd}"`,
    { encoding: 'utf8', timeout: 15000 }
  ).trim();

  return Buffer.from(b64, 'base64');
}

/**
 * Записывает учётные данные в Login Data профиля.
 * @param {string} userDataDir  — путь к папке профиля (содержит Default/)
 * @param {object} account      — { email, password, type }
 * @param {object} logger
 */
export async function injectLoginData(userDataDir, account, logger) {
  if (!account?.email || !account?.password) return;

  let initSqlJs;
  try {
    initSqlJs = require('sql.js');
  } catch {
    logger?.warn?.({ mod: 'loginDataManager' }, 'sql.js not available — skipping Login Data injection');
    return;
  }

  const defaultDir = path.join(userDataDir, 'Default');
  await fs.mkdir(defaultDir, { recursive: true });

  const loginDataPath = path.join(defaultDir, 'Login Data');

  // Загружаем существующий файл или создаём новый
  let fileBuffer = null;
  try {
    fileBuffer = await fs.readFile(loginDataPath);
  } catch {
    // файла нет — создадим новый
  }

  let SQL;
  try {
    SQL = await initSqlJs();
  } catch (err) {
    logger?.warn?.({ mod: 'loginDataManager', err: err.message }, 'sql.js init failed');
    return;
  }

  let db;
  try {
    db = fileBuffer ? new SQL.Database(fileBuffer) : new SQL.Database();
  } catch (err) {
    logger?.warn?.({ mod: 'loginDataManager', err: err.message }, 'cannot open Login Data');
    return;
  }

  try {
    db.run(CREATE_TABLE_SQL);

    // Версия схемы (Chrome проверяет)
    db.run(`INSERT OR REPLACE INTO meta (key, value) VALUES ('version', '35')`);
    db.run(`INSERT OR REPLACE INTO meta (key, value) VALUES ('last_compatible_version', '1')`);

    const emailLower = account.email.toLowerCase().trim();

    // Шифруем пароль через DPAPI — Chrome на Windows требует именно это
    let passwordBytes;
    try {
      passwordBytes = encryptWithDPAPI(account.password);
      logger?.debug?.({ mod: 'loginDataManager' }, 'DPAPI encryption succeeded');
    } catch (dpErr) {
      // Fallback: plaintext с префиксом v10 (для не-Windows окружений)
      logger?.warn?.({ mod: 'loginDataManager', err: dpErr.message }, 'DPAPI failed, using plaintext fallback');
      passwordBytes = Buffer.concat([Buffer.from('v10'), Buffer.from(account.password, 'utf8')]);
    }

    const origins = ORIGIN_URLS[account.type] || [...ORIGIN_URLS.gmail, ...ORIGIN_URLS.outlook];

    // Chrome time = микросекунды с 1601-01-01
    const chromeNow = (BigInt(Date.now()) + 11644473600000n) * 1000n;

    for (const originUrl of origins) {
      const signonRealm = new URL(originUrl).origin + '/';

      // Удаляем старую запись чтобы не было дублей
      db.run(
        `DELETE FROM logins WHERE origin_url=? AND username_value=?`,
        [originUrl, emailLower]
      );

      db.run(
        `INSERT INTO logins
          (origin_url, action_url, username_element, username_value,
           password_element, password_value, signon_realm,
           date_created, blacklisted_by_user, scheme, times_used, date_last_used)
         VALUES (?,?,?,?,?,?,?,?,0,0,3,?)`,
        [
          originUrl,
          originUrl,
          'identifier',
          emailLower,
          'password',
          passwordBytes,
          signonRealm,
          Number(chromeNow),
          Number(chromeNow),
        ]
      );
    }

    // Сохраняем файл
    const data = db.export();
    await fs.writeFile(loginDataPath, Buffer.from(data));

    logger?.info?.({
      mod: 'loginDataManager',
      email: emailLower,
      type: account.type,
      origins: origins.length,
    }, 'Login Data injected successfully');
  } catch (err) {
    logger?.warn?.({ mod: 'loginDataManager', err: err.message }, 'Login Data injection failed');
  } finally {
    try { db.close(); } catch { /* ok */ }
  }
}
