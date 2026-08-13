/**
 * run_all_profiles.js
 * Запускает ggsel_register.js последовательно по всем профилям из группы MSB.
 *
 * Использование:
 *   node run_all_profiles.js [action] [--group <group_name>] [--delay <seconds>]
 *
 * Примеры:
 *   node run_all_profiles.js                        — full по всем профилям GGSeller
 *   node run_all_profiles.js login                  — только шаг login
 *   node run_all_profiles.js --delay 30             — пауза 30 сек между профилями
 *   node run_all_profiles.js --group GGSeller full
 *
 * Остановка:
 *   Ctrl+C  — мягкая: дожидается конца текущего профиля, потом выходит
 *   Ctrl+C x2 — жёсткая: убивает текущий процесс немедленно
 */

import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { getProfilesByGroup, getProfileEmail, listGroups } from './lib/msb_client.js';
import { isAlreadyRegistered, saveResult, getResultsFilePath } from './lib/results_store.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REGISTER_SCRIPT = join(__dirname, 'ggsel_register.js');

// ── Разбор аргументов ─────────────────────────────────────────────────────────

const args = process.argv.slice(2);
let action = 'full';
let groupName = 'GGSeller';
let delaySeconds = 10;

for (let i = 0; i < args.length; i++) {
  if (args[i] === '--group' && args[i + 1]) { groupName = args[++i]; continue; }
  if (args[i] === '--delay' && args[i + 1]) { delaySeconds = Number(args[++i]); continue; }
  if (!args[i].startsWith('--')) { action = args[i]; }
}

// ── Состояние остановки ───────────────────────────────────────────────────────

let stopping = false;
let currentChild = null;
let sigintCount = 0;

process.on('SIGINT', () => {
  sigintCount++;
  if (sigintCount === 1) {
    console.log('\n');
    log('⏸  Ctrl+C — доделываю текущий профиль и останавливаюсь...');
    log('   Нажми Ctrl+C ещё раз чтобы убить немедленно.');
    stopping = true;
  } else {
    log('⛔ Принудительная остановка!');
    if (currentChild) {
      try { currentChild.kill('SIGTERM'); } catch (_) {}
    }
    process.exit(130);
  }
});

// ── Утилиты ───────────────────────────────────────────────────────────────────

function sleep(ms) {
  // Прерываемый sleep — проверяет stopping каждые 500ms
  return new Promise((resolve) => {
    const step = 500;
    let elapsed = 0;
    const iv = setInterval(() => {
      elapsed += step;
      if (stopping || elapsed >= ms) {
        clearInterval(iv);
        resolve();
      }
    }, step);
  });
}

function timestamp() {
  return new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function log(msg) {
  console.log(`[${timestamp()}] ${msg}`);
}

// ── Запуск одного профиля ─────────────────────────────────────────────────────

function runProfile(profileId, email, action) {
  return new Promise((resolve) => {
    log(`▶ Запуск: ${email} (${profileId}) — action=${action}`);

    const child = spawn(
      'node',
      [REGISTER_SCRIPT, profileId, action],
      {
        cwd: __dirname,
        stdio: ['ignore', 'pipe', 'pipe'],
        shell: false,
      }
    );

    currentChild = child;
    const prefix = `  [${email}]`;

    child.stdout.on('data', (data) => {
      data.toString().split('\n').forEach(line => {
        if (line.trim()) console.log(`${prefix} ${line}`);
      });
    });

    child.stderr.on('data', (data) => {
      data.toString().split('\n').forEach(line => {
        if (line.trim()) console.error(`${prefix} ⚠ ${line}`);
      });
    });

    child.on('close', (code) => {
      currentChild = null;
      const success = code === 0;
      log(`${success ? '✓' : '✗'} ${email} завершён (exit ${code ?? 'killed'})`);
      resolve({ exitCode: code ?? -1, success });
    });

    child.on('error', (err) => {
      currentChild = null;
      log(`✗ ${email} — ошибка запуска процесса: ${err.message}`);
      resolve({ exitCode: -1, success: false });
    });
  });
}

// ── Главная логика ────────────────────────────────────────────────────────────

async function main() {
  // Загружаем профили из MSB
  let profiles;
  try {
    profiles = await getProfilesByGroup(groupName);
  } catch (err) {
    console.error(`Не удалось получить профили из MSB: ${err.message}`);
    console.error('Убедись что MSB запущен на http://127.0.0.1:17248');
    process.exit(1);
  }

  if (!profiles.length) {
    console.error(`Группа "${groupName}" не найдена или пустая в MSB.`);
    try {
      const groups = await listGroups();
      console.error(`Доступные группы: ${groups.join(', ')}`);
    } catch (_) {}
    process.exit(1);
  }

  // Фильтруем только Outlook-профили (Gmail не поддерживается)
  const outlookProfiles = profiles.filter(p => p.provider === 'outlook' || p.account?.type === 'outlook');
  const skippedGmail = profiles.length - outlookProfiles.length;
  if (skippedGmail > 0) {
    log(`ℹ Пропущено ${skippedGmail} Gmail-профилей (не поддерживаются)`);
  }

  log(`═══════════════════════════════════════════════`);
  log(`Источник:  MSB API (group="${groupName}")`);
  log(`Профилей:  ${outlookProfiles.length} (Outlook)`);
  log(`Action:    ${action}`);
  log(`Пауза:     ${delaySeconds}s между профилями`);
  log(`Результаты: ${getResultsFilePath()}`);
  log(`Стоп:      Ctrl+C — мягко | Ctrl+C x2 — жёстко`);
  log(`═══════════════════════════════════════════════`);

  const sessionResults = [];
  let skippedCount = 0;

  for (let i = 0; i < outlookProfiles.length; i++) {
    // Мягкая остановка — выходим между профилями
    if (stopping) {
      log(`⏸  Остановка после ${i} профилей (осталось ${outlookProfiles.length - i})`);
      break;
    }

    const profile = outlookProfiles[i];
    const email = getProfileEmail(profile);

    log(`\n[${i + 1}/${outlookProfiles.length}] ${email}`);

    // Проверка на уже зарегистрированный
    const alreadyDone = await isAlreadyRegistered(profile.id);
    if (alreadyDone) {
      log(`[SKIP] ${email} — уже зарегистрирован (results.jsonl)`);
      skippedCount++;
      continue;
    }

    // Запуск регистрации
    const result = await runProfile(profile.id, email, action);
    sessionResults.push({ email, profileId: profile.id, ...result });

    // Сохраняем результат в JSONL
    await saveResult({
      profileId: profile.id,
      email,
      status: result.success ? 'success' : 'error',
      error: result.success ? null : `exit code ${result.exitCode}`,
    });

    if (i < outlookProfiles.length - 1 && !stopping) {
      log(`Пауза ${delaySeconds}s... (Ctrl+C чтобы пропустить паузу и остановиться)`);
      await sleep(delaySeconds * 1000);
    }
  }

  // ── Итоговый отчёт ────────────────────────────────────────────────────────

  const ok  = sessionResults.filter(r => r.success);
  const err = sessionResults.filter(r => !r.success);
  const notRun = outlookProfiles.length - sessionResults.length - skippedCount;

  console.log('\n');
  log(`═══════════════════ ИТОГ ═══════════════════════`);
  log(`Обработано: ${sessionResults.length} из ${outlookProfiles.length}`);
  log(`Успешно:    ${ok.length}`);
  log(`Ошибки:     ${err.length}`);
  if (skippedCount > 0) log(`Пропущено:  ${skippedCount} (уже зарегистрированы)`);
  if (notRun > 0)       log(`Не запущено: ${notRun} (остановлено)`);

  if (ok.length) {
    log(`\n✓ Успешно:`);
    ok.forEach(r => console.log(`    ${r.email}`));
  }

  if (err.length) {
    log(`\n✗ Ошибки:`);
    err.forEach(r => console.log(`    ${r.email} (exit ${r.exitCode})`));
  }

  if (notRun > 0) {
    log(`\n— Не запускались:`);
    outlookProfiles.slice(sessionResults.length + skippedCount).forEach(p =>
      console.log(`    ${getProfileEmail(p)}`)
    );
  }

  log(`═══════════════════════════════════════════════`);
  process.exit(err.length > 0 ? 1 : 0);
}

main().catch(err => {
  console.error('FATAL:', err.message);
  process.exit(1);
});
