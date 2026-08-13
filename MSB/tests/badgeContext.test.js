// tests/badgeContext.test.js
// Smoke test: проверяет, что writeBadgeContext() корректно пишет
// msb-context.json в папку расширения. Реплицирует логику из
// browserLauncher/index.js, чтобы можно было тестировать без Electron.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const EXT_PATH = path.resolve(__dirname, '..', 'extensions', 'msb-profile-badge');
const TARGET = path.join(EXT_PATH, 'msb-context.json');

function writeBadgeContext(profile) {
  if (profile?.badge === false) return null;
  if (!fs.existsSync(EXT_PATH)) return null;
  const num = profile.number != null
    ? String(profile.number)
    : (profile.id ? String(profile.id).slice(-4).toUpperCase() : '?');
  const ctx = {
    id: profile.id || null,
    number: num,
    name: profile.name || profile.account?.name || '',
    email: profile.account?.email || '',
    group: profile.group || '',
    country: profile.geoip?.country || '',
    startedAt: Date.now(),
  };
  fs.writeFileSync(TARGET, JSON.stringify(ctx), 'utf8');
  return EXT_PATH;
}

test('writes context for normal profile', () => {
  const r = writeBadgeContext({
    id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    number: 127,
    account: { email: 'boris.liron@gmail.com' },
    group: 'GGSeller',
    geoip: { country: 'AM' },
  });
  assert.equal(r, EXT_PATH);
  const ctx = JSON.parse(fs.readFileSync(TARGET, 'utf8'));
  assert.equal(ctx.email, 'boris.liron@gmail.com');
  assert.equal(ctx.number, '127');
  assert.equal(ctx.group, 'GGSeller');
  assert.equal(ctx.country, 'AM');
  assert.ok(ctx.startedAt > 0);
});

test('uses profile.number when present', () => {
  writeBadgeContext({
    id: 'uuid-xyz',
    number: 42,
    account: { email: 'foo@bar.com' },
  });
  const ctx = JSON.parse(fs.readFileSync(TARGET, 'utf8'));
  assert.equal(ctx.number, '42');
});

test('falls back to last-4 of UUID when number is missing', () => {
  writeBadgeContext({
    id: 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
    account: { email: 'x@y.com' },
  });
  const ctx = JSON.parse(fs.readFileSync(TARGET, 'utf8'));
  assert.equal(ctx.number, '7890');
});

test('respects profile.badge === false (opt-out)', () => {
  writeBadgeContext({ id: 'uuid-1', number: 1, account: { email: 'a@b.com' } });
  assert.ok(fs.existsSync(TARGET));
  const r = writeBadgeContext({
    id: 'uuid-2',
    number: 2,
    account: { email: 'c@d.com' },
    badge: false,
  });
  assert.equal(r, null);
});

test('handles empty profile gracefully', () => {
  const r = writeBadgeContext({});
  assert.equal(r, EXT_PATH);
  const ctx = JSON.parse(fs.readFileSync(TARGET, 'utf8'));
  assert.equal(ctx.email, '');
  assert.equal(ctx.number, '?');
});
