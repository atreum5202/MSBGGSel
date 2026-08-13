import { test, describe, before, after } from 'node:test';
import assert from 'node:assert';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

// Set env var before importing ProxyStore
const tempDir = path.join(os.tmpdir(), `msb-test-${Date.now()}`);
process.env.MSB_DATA_DIR = tempDir;

describe('ProxyStore tests', () => {
  let proxyStore;
  let ProxyStoreClass;

  before(async () => {
    await fs.mkdir(tempDir, { recursive: true });
    // Dynamic import to ensure the environment variable is loaded first
    const module = await import('../src/main/services/proxyStore.js');
    ProxyStoreClass = module.ProxyStore;
    proxyStore = new ProxyStoreClass();
  });

  after(async () => {
    try {
      await fs.rm(tempDir, { recursive: true, force: true });
    } catch {
      // ignore
    }
  });

  test('init creates empty array if file does not exist', async () => {
    await proxyStore.init();
    assert.deepStrictEqual(proxyStore.list(), []);
  });

  test('add a proxy', async () => {
    const item = await proxyStore.add({
      protocol: 'socks5',
      host: '1.1.1.1',
      port: 1080,
      username: 'user',
      password: 'pwd',
      label: 'test-socks'
    });

    assert.ok(item.id);
    assert.strictEqual(item.protocol, 'socks5');
    assert.strictEqual(item.host, '1.1.1.1');
    assert.strictEqual(item.port, 1080);
    assert.strictEqual(item.username, 'user');
    assert.strictEqual(item.password, 'pwd');
    assert.strictEqual(item.label, 'test-socks');

    const list = proxyStore.list();
    assert.strictEqual(list.length, 1);
    assert.strictEqual(list[0].id, item.id);
  });

  test('prevent adding duplicate proxy', async () => {
    await assert.rejects(
      async () => {
        await proxyStore.add({
          protocol: 'http',
          host: '1.1.1.1',
          port: 1080
        });
      },
      /already exists/
    );
  });

  test('addBulk parsing and deduplication', async () => {
    const lines = [
      'socks5://user:pass@1.2.3.4:1080',
      'http://5.6.7.8:3128',
      '1.1.1.1:1080', // duplicate of first test
      '9.9.9.9:9999:user9:pass9',
      'invalid-line',
      'socks5://user:pass@1.2.3.4:1080' // duplicate in the same lines input
    ].join('\n');

    const result = await proxyStore.addBulk({ lines });
    assert.strictEqual(result.added, 3); // 1.2.3.4:1080, 5.6.7.8:3128, 9.9.9.9:9999
    assert.strictEqual(result.skipped, 2); // 1.1.1.1:1080 (in store), 1.2.3.4:1080 (duplicate in queue)

    const list = proxyStore.list();
    // Total should be 1 (from previous test) + 3 = 4
    assert.strictEqual(list.length, 4);
  });

  test('remove proxy', async () => {
    const list = proxyStore.list();
    const toRemove = list[0];
    const removed = await proxyStore.remove(toRemove.id);
    assert.strictEqual(removed, true);

    const newList = proxyStore.list();
    assert.strictEqual(newList.length, 3);
    assert.ok(!newList.some(p => p.id === toRemove.id));
  });
});
