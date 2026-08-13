import path from 'node:path';
import fs from 'node:fs/promises';
import os from 'node:os';

const MSB_APPDATA = process.env.MSB_APPDATA
  || path.join(os.homedir(), 'AppData', 'Roaming', 'MSB');
const BUNDLED_EXT_DIR = path.join(MSB_APPDATA, 'extensions');

export function registerExtensionRoutes({ app, commonExtensionsManager }) {
  app.get('/extensions', { schema: { summary: 'List common extensions' } }, async () => {
    return { extensions: commonExtensionsManager.list() };
  });

  app.post('/extensions', {
    schema: {
      summary: 'Add common extension',
      body: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
    },
  }, async (req, reply) => {
    const result = await commonExtensionsManager.add(req.body.path);
    reply.code(result.added ? 201 : 200).send(result);
  });

  app.delete('/extensions', {
    schema: {
      summary: 'Remove common extension',
      body: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] },
    },
  }, async (req) => {
    return commonExtensionsManager.remove(req.body.path);
  });

  app.delete('/extensions/all', { schema: { summary: 'Clear all common extensions' } }, async () => {
    return commonExtensionsManager.clear();
  });

  app.post('/extensions/install-crx', {
    schema: {
      summary: 'Install extension from CRX base64',
      body: {
        type: 'object',
        properties: {
          name: { type: 'string' },
          data: { type: 'string', description: 'base64-encoded .crx file' },
        },
        required: ['name', 'data'],
      },
    },
  }, async (req, reply) => {
    const { name, data } = req.body;
    try {
      const AdmZip = (await import('adm-zip')).default;
      const buf = Buffer.from(data, 'base64');
      let zipBuf = buf;
      if (buf.slice(0, 4).toString() === 'Cr24') {
        const headerSize = buf.readUInt32LE(8);
        zipBuf = buf.slice(12 + headerSize);
      }
      const extName = name.replace(/\.crx$/i, '').replace(/[^a-zA-Z0-9_-]/g, '_');
      const destDir = path.join(BUNDLED_EXT_DIR, extName);
      await fs.mkdir(destDir, { recursive: true });
      const zip = new AdmZip(zipBuf);
      zip.extractAllTo(destDir, true);
      await fs.access(path.join(destDir, 'manifest.json'));
      const addResult = await commonExtensionsManager.add(destDir);
      reply.code(200).send({ installed: true, path: destDir, ...addResult });
    } catch (err) {
      reply.code(400).send({ installed: false, error: err.message });
    }
  });

  app.post('/extensions/add-tampermonkey', { schema: { summary: 'Add bundled Tampermonkey' } }, async (_req, reply) => {
    const tmDir = path.join(BUNDLED_EXT_DIR, 'tampermonkey');
    try {
      await fs.access(path.join(tmDir, 'manifest.json'));
    } catch {
      reply.code(404).send({ added: false, error: `Tampermonkey не найден в ${tmDir}. Сначала загрузи .crx через кнопку 📦.` });
      return;
    }
    const result = await commonExtensionsManager.add(tmDir);
    reply.code(result.added ? 201 : 200).send(result);
  });
}
