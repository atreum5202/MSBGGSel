// tests/profileNumber.test.js
// Тесты для поля number в ProfileManager:
//  - auto-increment при create
//  - миграция существующих профилей без number
//  - уникальность при ручном PATCH
//  - поведение при удалении (номера не переиспользуются)
//  - восстановление nextNumber из index.json

import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { ProfileManager } from '../src/main/services/profileManager.js';

async function makeTempDir() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'msb-pm-'));
  return dir;
}

async function writeProfileMeta(profilesDir, profile) {
  const dir = path.join(profilesDir, profile.id);
  await fs.mkdir(path.join(dir, 'userData'), { recursive: true });
  await fs.writeFile(path.join(dir, 'meta.json'), JSON.stringify(profile, null, 2));
}

async function writeIndex(profilesDir, index) {
  await fs.writeFile(path.join(profilesDir, 'index.json'), JSON.stringify(index, null, 2));
}

test('create auto-assigns sequential numbers starting at 1', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  const p1 = await pm.create({ name: 'first', account: { email: 'a@b.com' } });
  const p2 = await pm.create({ name: 'second', account: { email: 'c@d.com' } });
  const p3 = await pm.create({ name: 'third', account: { email: 'e@f.com' } });

  assert.equal(p1.number, 1);
  assert.equal(p2.number, 2);
  assert.equal(p3.number, 3);
  assert.equal(pm.nextNumber, 4);
});

test('migration assigns numbers to existing profiles by createdAt', async () => {
  const dir = await makeTempDir();
  // Симулируем "старый" index.json без nextNumber и профили без number
  await writeIndex(dir, { profiles: [] });

  const old1 = { id: 'old-1', name: 'older', account: { email: 'a@b.com', type: 'other' }, createdAt: 1000, updatedAt: 1000, provider: 'other', tags: [] };
  const old2 = { id: 'old-2', name: 'newer', account: { email: 'c@d.com', type: 'other' }, createdAt: 2000, updatedAt: 2000, provider: 'other', tags: [] };
  await writeProfileMeta(dir, old1);
  await writeProfileMeta(dir, old2);
  // index.json указывает на оба профиля
  await writeIndex(dir, { profiles: [{ id: 'old-1', name: 'older', updatedAt: 1000 }, { id: 'old-2', name: 'newer', updatedAt: 2000 }] });

  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  // old-1 создан раньше → получит 1, old-2 → 2
  const m1 = pm.get('old-1');
  const m2 = pm.get('old-2');
  assert.equal(m1.number, 1, 'old-1 should get number 1 (earlier createdAt)');
  assert.equal(m2.number, 2, 'old-2 should get number 2');
  assert.equal(pm.nextNumber, 3);
});

test('migration preserves existing numbers and continues after max', async () => {
  const dir = await makeTempDir();
  // У одного профиля уже есть number=5, у второго нет
  const withNum = { id: 'has-num', name: 'with', account: { email: 'a@b.com', type: 'other' }, createdAt: 1000, updatedAt: 1000, number: 5, provider: 'other', tags: [] };
  const noNum = { id: 'no-num', name: 'without', account: { email: 'c@d.com', type: 'other' }, createdAt: 2000, updatedAt: 2000, provider: 'other', tags: [] };
  await writeProfileMeta(dir, withNum);
  await writeProfileMeta(dir, noNum);
  await writeIndex(dir, { profiles: [{ id: 'has-num', number: 5, name: 'with', updatedAt: 1000 }, { id: 'no-num', name: 'without', updatedAt: 2000 }] });

  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  assert.equal(pm.get('has-num').number, 5, 'existing number preserved');
  assert.ok(pm.get('no-num').number > 5, 'new number should be > 5 (got ' + pm.get('no-num').number + ')');
  assert.equal(pm.get('no-num').number, 6, 'should be exactly 6');
  assert.equal(pm.nextNumber, 7);
});

test('PATCH updates number with validation', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  const p1 = await pm.create({ name: 'p1' });
  const p2 = await pm.create({ name: 'p2' });

  // p1.number = 1, p2.number = 2
  // Перекидываем p1 на number=10
  const updated = await pm.update(p1.id, { number: 10 });
  assert.equal(updated.number, 10);
  assert.equal(pm.nextNumber, 11, 'nextNumber should advance past manually set number');

  // Новый профиль получит 11, не 3
  const p3 = await pm.create({ name: 'p3' });
  assert.equal(p3.number, 11);
});

test('PATCH rejects duplicate number', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  const p1 = await pm.create({ name: 'p1' });
  const p2 = await pm.create({ name: 'p2' });

  await assert.rejects(
    () => pm.update(p2.id, { number: p1.number }),
    /already taken/
  );
});

test('PATCH rejects non-integer or zero/negative number', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();
  const p = await pm.create({ name: 'p' });

  await assert.rejects(() => pm.update(p.id, { number: 0 }), /> 0/);
  await assert.rejects(() => pm.update(p.id, { number: -5 }), /> 0/);
  await assert.rejects(() => pm.update(p.id, { number: 1.5 }), /integer or null/);
  await assert.rejects(() => pm.update(p.id, { number: 'abc' }), /integer or null/);
});

test('PATCH allows null to clear number', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();
  const p = await pm.create({ name: 'p' });
  assert.equal(p.number, 1);

  const updated = await pm.update(p.id, { number: null });
  assert.equal(updated.number, null);
});

test('delete does not reuse number', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  const p1 = await pm.create({ name: 'first' });
  const p2 = await pm.create({ name: 'second' });
  // p1.number=1, p2.number=2, nextNumber=3

  await pm.remove(p1.id);
  // Удаляем p1 (number=1). Счётчик не должен сбрасываться.

  const p3 = await pm.create({ name: 'third' });
  assert.equal(p3.number, 3, 'new profile should get 3, not reuse 1');
  assert.equal(pm.nextNumber, 4);
});

test('nextNumber is restored from index.json on init', async () => {
  const dir = await makeTempDir();
  // Создадим index.json с nextNumber=42
  await writeIndex(dir, { profiles: [], nextNumber: 42 });
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  assert.equal(pm.nextNumber, 42);
  const p = await pm.create({ name: 'after-restart' });
  assert.equal(p.number, 42);
  assert.equal(pm.nextNumber, 43);
});

test('_toListItem includes number', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();
  const p = await pm.create({ name: 'p', account: { email: 'x@y.com' } });

  const list = pm.list();
  assert.equal(list.length, 1);
  assert.equal(list[0].number, 1);
  assert.equal(list[0].id, p.id);
});

test('create with explicit number parameter', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  const p1 = await pm.create({ name: 'auto-1' });
  const p2 = await pm.create({ name: 'explicit', number: 100 });
  const p3 = await pm.create({ name: 'auto-2' });

  assert.equal(p1.number, 1);
  assert.equal(p2.number, 100);
  // p3 должен получить 101 (а не 3), т.к. счётчик уехал за 100
  assert.equal(p3.number, 101);
  assert.equal(pm.nextNumber, 102);
});

test('create rejects explicit number that is already taken', async () => {
  const dir = await makeTempDir();
  const pm = new ProfileManager({ profilesDir: dir, logger: null });
  await pm.init();

  await pm.create({ name: 'p1' });
  await assert.rejects(
    () => pm.create({ name: 'p2', number: 1 }),
    /already taken/
  );
});
