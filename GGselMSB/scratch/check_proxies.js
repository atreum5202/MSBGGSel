/**
 * check_proxies.js
 * Проверяет каждый профиль группы GGSeller:
 * делает HTTP GET https://ggsel.net через его socks5 прокси.
 * Выводит: email | прокси | статус | время | заголовок
 */

import { SocksClient } from 'socks';
import * as https from 'node:https';
import { getProfilesByGroup, getProfileEmail } from './lib/msb_client.js';

const GROUP   = process.argv[2] || 'GGSeller';
const TARGET  = { host: 'ggsel.net', port: 443, path: '/' };
const TIMEOUT = 12_000; // ms

// ── HTTP через SOCKS5 ─────────────────────────────────────────────────────────

async function checkViaProxy(proxy) {
  const t0 = Date.now();

  const { socket } = await SocksClient.createConnection({
    proxy: {
      host:     proxy.host,
      port:     proxy.port,
      type:     5,
      userId:   proxy.username,
      password: proxy.password,
    },
    command:     'connect',
    destination: { host: TARGET.host, port: TARGET.port },
    timeout:     TIMEOUT,
  });

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      socket.destroy();
      reject(new Error('timeout'));
    }, TIMEOUT);

    const req = https.request(
      {
        host:     TARGET.host,
        port:     TARGET.port,
        path:     TARGET.path,
        method:   'GET',
        socket,
        createConnection: () => socket,
        headers: {
          'Host':            TARGET.host,
          'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
          'Accept':          'text/html',
          'Accept-Language': 'ru-RU,ru;q=0.9',
          'Connection':      'close',
        },
        timeout: TIMEOUT,
      },
      (res) => {
        clearTimeout(timer);
        res.resume(); // читаем и выбрасываем тело
        res.on('end', () => {
          socket.destroy();
          resolve({
            status:  res.statusCode,
            ms:      Date.now() - t0,
            title:   res.headers['x-frame-options'] || res.headers['server'] || '-',
            location: res.headers['location'] || null,
          });
        });
      }
    );

    req.on('error', (err) => {
      clearTimeout(timer);
      socket.destroy();
      reject(err);
    });

    req.end();
  });
}

// ── Главная логика ─────────────────────────────────────────────────────────────

async function main() {
  console.log(`\nПроверка прокси профилей группы "${GROUP}" → https://${TARGET.host}\n`);

  let profiles;
  try {
    profiles = await getProfilesByGroup(GROUP);
  } catch (e) {
    console.error('MSB недоступен:', e.message);
    process.exit(1);
  }

  const withProxy = profiles.filter(p => p.proxy);
  const noProxy   = profiles.filter(p => !p.proxy);

  if (noProxy.length) {
    console.log(`⚠  Без прокси (пропускаем): ${noProxy.map(p => getProfileEmail(p)).join(', ')}\n`);
  }

  if (!withProxy.length) {
    console.log('Нет профилей с прокси.');
    return;
  }

  const col = { email: 32, proxy: 30, status: 7, ms: 7, note: 20 };
  const hr = '─'.repeat(Object.values(col).reduce((a, b) => a + b + 3, 0));

  const fmt = (email, proxy, status, ms, note) =>
    `${email.padEnd(col.email)} │ ${proxy.padEnd(col.proxy)} │ ${String(status).padEnd(col.status)} │ ${String(ms).padStart(col.ms)} │ ${note}`;

  console.log(fmt('Email', 'Прокси', 'Статус', 'ms', 'Примечание'));
  console.log(hr);

  const results = [];

  for (const profile of withProxy) {
    const email = getProfileEmail(profile);
    const { proxy } = profile;
    const proxyStr = `${proxy.host}:${proxy.port}`;

    process.stdout.write(fmt(email, proxyStr, '...', '...', '') + '\r');

    try {
      const r = await checkViaProxy(proxy);
      const icon  = r.status < 400 ? '✓' : r.status < 500 ? '⚠' : '✗';
      const note  = r.location ? `→ ${r.location.slice(0, 18)}` : r.title;
      const line  = fmt(email, proxyStr, `${icon} ${r.status}`, r.ms, note);
      console.log(line);
      results.push({ email, proxy: proxyStr, ...r, ok: true });
    } catch (err) {
      const line = fmt(email, proxyStr, '✗ ERR', '-', err.message.slice(0, 20));
      console.log(line);
      results.push({ email, proxy: proxyStr, status: null, ms: null, ok: false, error: err.message });
    }
  }

  // Итог
  const ok  = results.filter(r => r.ok && r.status < 400);
  const rdr = results.filter(r => r.ok && r.status >= 300 && r.status < 400);
  const bad = results.filter(r => !r.ok || r.status >= 400);

  console.log('\n' + hr);
  console.log(`Итог: ${results.length} проверено | ✓ OK: ${ok.length} | ⚠ Редирект: ${rdr.length} | ✗ Ошибки: ${bad.length}`);
  if (bad.length) {
    console.log('Проблемные:');
    bad.forEach(r => console.log(`  ${r.email}  ${r.error || `HTTP ${r.status}`}`));
  }
  console.log();
}

main().catch(err => {
  console.error('FATAL:', err.message);
  process.exit(1);
});
