// Integration test: ProfileStorage + TrashService + atomicFs + profileSchema (Tier 2)
// Run: node --test tests/profileStorage.test.js
import { test, describe, before, after } from 'node:test';
import assert from 'node:assert/strict';
import fsp from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { ProfileStorage } from '../src/main/services/profile/ProfileStorage.js';
import { TrashService } from '../src/main/services/profile/TrashService.js';
import { atomicWriteJson, readJsonSafe } from '../src/main/lib/atomicFs.js';
import { migrateProfile, PROFILE_SCHEMA_VERSION } from '../src/main/lib/profileSchema.js';

let tmpDir, storage, trashSvc;

before(async () => {
  tmpDir = await fsp.mkdtemp(path.join(os.tmpdir(), 'msb-test-'));
  storage = new ProfileStorage({ profilesDir: tmpDir });
  trashSvc = new TrashService({ storage });
  await storage.ensureDir();
});

after(async () => {
  await fsp.rm(tmpDir, { recursive: true, force: true });
});

describe('ProfileStorage', () => {
  test('readIndex returns null when missing', async () => {
    assert.equal(await storage.readIndex(), null);
  });

  test('writeIndex + readIndex round-trip', async () => {
    await storage.writeIndex({ nextNumber: 5, profiles: [{ id: 'abc' }] });
    const g = await storage.readIndex();
    assert.equal(g.nextNumber, 5);
    assert.equal(g.schemaVersion, PROFILE_SCHEMA_VERSION);
    assert.equal(g.profiles.length, 1);
  });

  test('writeMeta + readMeta round-trip', async () => {
    const p = { id: 'tid1', schemaVersion: PROFILE_SCHEMA_VERSION, name: 'T', number: 1, createdAt: 0, updatedAt: 0 };
    await storage.writeMeta(p);
    const g = await storage.readMeta('tid1');
    assert.equal(g.name, 'T');
  });

  test('readMeta returns null for missing id', async () => {
    assert.equal(await storage.readMeta('nope'), null);
  });

  test('scanOrphans finds profiles not in index', async () => {
    const o = await storage.scanOrphans();
    assert.ok(o.some(p => p.id === 'tid1'));
  });

  test('remove deletes profile directory', async () => {
    await storage.writeMeta({ id: 'rm', name: 'X', schemaVersion: PROFILE_SCHEMA_VERSION, number: 2, createdAt: 0, updatedAt: 0 });
    await storage.remove('rm');
    let e = false;
    try { await fsp.access(storage.dir('rm')); e = true; } catch {}
    assert.ok(!e);
  });
});

describe('TrashService', () => {
  const TID = 'tr-prof';

  before(async () => {
    await storage.writeMeta({ id: TID, name: 'TrashMe', number: 42, schemaVersion: PROFILE_SCHEMA_VERSION, createdAt: Date.now(), updatedAt: Date.now() });
  });

  test('trash() moves profile to .trash dir', async () => {
    const p = await storage.readMeta(TID);
    const r = await trashSvc.trash(p);
    assert.ok(r && r.trashed);
    assert.equal(r.retentionDays, 7);
    let live = false;
    try { await fsp.access(storage.dir(TID)); live = true; } catch {}
    assert.ok(!live, 'live dir should be gone');
    let td = false;
    try { await fsp.access(storage.trashPath(TID)); td = true; } catch {}
    assert.ok(td, 'trash dir should exist');
  });

  test('list() returns trashed profiles', async () => {
    const items = await trashSvc.list();
    const found = items.find(i => i.id === TID);
    assert.ok(found);
    assert.equal(found.name, 'TrashMe');
    assert.ok(found.expiresAt > Date.now());
  });

  test('restore() brings profile back', async () => {
    const ok = await trashSvc.restore(TID);
    assert.ok(ok);
    let live = false;
    try { await fsp.access(storage.dir(TID)); live = true; } catch {}
    assert.ok(live, 'live dir should exist after restore');
    let td = false;
    try { await fsp.access(storage.trashPath(TID)); td = true; } catch {}
    assert.ok(!td, 'trash dir should be gone after restore');
  });

  test('purge() permanently removes from trash', async () => {
    const p = await storage.readMeta(TID);
    await trashSvc.trash(p);
    const ok = await trashSvc.purge(TID);
    assert.ok(ok);
    let td = false;
    try { await fsp.access(storage.trashPath(TID)); td = true; } catch {}
    assert.ok(!td);
  });

  test('purgeExpired() only removes expired items', async () => {
    const id = 'exp';
    await storage.writeMeta({ id, name: 'E', number: 99, schemaVersion: PROFILE_SCHEMA_VERSION, createdAt: 0, updatedAt: 0 });
    const p = await storage.readMeta(id);
    await trashSvc.trash(p);
    // Backdate the manifest by 8 days
    const mp = path.join(storage.trashPath(id), 'trash.json');
    const m = await readJsonSafe(mp);
    m.deletedAt = Date.now() - 8 * 86400_000;
    await atomicWriteJson(mp, m);
    const { purged } = await trashSvc.purgeExpired();
    assert.ok(purged >= 1);
  });
});

describe('atomicFs safety', () => {
  test('readJsonSafe returns null for missing file', async () => {
    assert.equal(await readJsonSafe(path.join(tmpDir, 'nope.json')), null);
  });

  test('atomicWriteJson + readJsonSafe round-trip', async () => {
    const p = path.join(tmpDir, 'rw.json');
    await atomicWriteJson(p, { a: 1, b: 'two' });
    assert.deepEqual(await readJsonSafe(p), { a: 1, b: 'two' });
  });
});

describe('profileSchema migration', () => {
  test('v0 profile gets schemaVersion 1', () => {
    assert.equal(migrateProfile({ id: 'x', name: 'Old', number: 1, createdAt: 0, updatedAt: 0 }).schemaVersion, PROFILE_SCHEMA_VERSION);
  });

  test('migration is idempotent', () => {
    assert.equal(migrateProfile({ id: 'x', name: 'N', number: 1, schemaVersion: 1, createdAt: 0, updatedAt: 0 }).schemaVersion, 1);
  });

  test('group initialised to null if missing', () => {
    assert.equal(migrateProfile({ id: 'x', name: 'O', number: 1, createdAt: 0, updatedAt: 0 }).group, null);
  });

  test('tags initialised to [] if missing', () => {
    assert.deepEqual(migrateProfile({ id: 'x', name: 'O', number: 1, createdAt: 0, updatedAt: 0 }).tags, []);
  });
});
