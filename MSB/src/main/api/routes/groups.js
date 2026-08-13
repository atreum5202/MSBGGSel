// Persistent group metadata (description, color override).
// Groups themselves are virtual — they exist because some `profile.group === "X"`.
// Meta is stored in `<profilesDir>/group-meta.json` (atomic write) and survives restarts.
import fs from 'node:fs/promises';
import path from 'node:path';
import { atomicWriteJson, readJsonSafe } from '../../lib/atomicFs.js';

// Зарезервированные виртуальные группы — не создаются из profile.group
const VIRTUAL_GROUP_NAMES = new Set(['Без групп', 'Minimax', 'Claude', 'GitHub']);

// Domains that indicate a session per virtual group
const MINIMAX_DOMAINS = ['minimax.com', 'minimaxi.com', 'hailuo.video', 'hailuoai.com'];
const CLAUDE_DOMAINS  = ['claude.ai', 'anthropic.com'];
const GITHUB_DOMAINS  = ['github.com', 'githubusercontent.com', 'githubassets.com'];

function cookieHasDomain(cookies, domainList) {
  if (!Array.isArray(cookies)) return false;
  return cookies.some(c => {
    const d = (c.domain || '').replace(/^\./, '').toLowerCase();
    return domainList.some(target => d === target || d.endsWith('.' + target));
  });
}

// In-memory mirror of the on-disk file, for fast read access.
const groupMeta = new Map(); // name -> { description, colorOverride }

function _metaFile(profilesDir) {
  return path.join(profilesDir, 'group-meta.json');
}

async function _loadMeta(profilesDir, logger) {
  try {
    const obj = await readJsonSafe(_metaFile(profilesDir), { fallback: {} });
    if (obj && typeof obj === 'object') {
      for (const [name, meta] of Object.entries(obj)) {
        if (!meta || typeof meta !== 'object') continue;
        groupMeta.set(name, {
          description: typeof meta.description === 'string' ? meta.description : '',
          colorOverride: typeof meta.colorOverride === 'string' ? meta.colorOverride : null,
        });
      }
      logger?.info({ count: groupMeta.size }, 'group meta loaded from disk');
    }
  } catch (e) {
    logger?.warn({ err: e.message }, 'group meta load failed (starting empty)');
  }
}

async function _saveMeta(profilesDir, logger) {
  const obj = {};
  for (const [name, meta] of groupMeta.entries()) {
    obj[name] = meta;
  }
  await atomicWriteJson(_metaFile(profilesDir), obj);
  logger?.debug({ count: Object.keys(obj).length }, 'group meta persisted');
}

export function registerGroupRoutes({ app, profileManager, cookieStore, logger, profilesDir }) {
  // Load on boot (fire-and-forget; safe because writes go through the same map).
  _loadMeta(profilesDir, logger).catch((e) => logger?.error({ err: e.message }, 'group meta load error'));

  // ── GET /groups ────────────────────────────────────────────────────────────
  // Возвращает:
  //   1. Пользовательские группы (profile.group, кроме зарезервированных имён)
  //   2. Виртуальные группы (Без групп / Minimax / Claude / GitHub) — всегда,
  //      даже если count = 0, чтобы UI мог отобразить пустой чип.
  //
  // Профили без group идут в виртуальную «Без групп».
  // Профили с group = 'Minimax'/'Claude'/'GitHub' игнорируются в пользовательских
  // группах — они не должны создавать дублей; их принадлежность к виртуальным
  // группам определяется только через cookies-snapshot.
  app.get('/groups', async (req, reply) => {
    const profiles = profileManager.list();

    // ── 1. Пользовательские группы (реальные) ─────────────────────────────
    const realGroups = new Map(); // name -> { name, count, profileIds, color, description, virtual: false }

    for (const profile of profiles) {
      const group = profile.group;
      // Пропускаем профили без группы и с зарезервированными именами
      if (!group || VIRTUAL_GROUP_NAMES.has(group)) continue;

      if (!realGroups.has(group)) {
        realGroups.set(group, {
          name: group,
          count: 0,
          profileIds: [],
          color: groupMeta.get(group)?.colorOverride || getGroupColor(group),
          description: groupMeta.get(group)?.description || '',
          virtual: false,
        });
      }
      realGroups.get(group).count++;
      realGroups.get(group).profileIds.push(profile.id);
    }

    // ── 2. Виртуальная группа «Без групп» ─────────────────────────────────
    const ungroupedIds = profiles.filter(p => !p.group).map(p => p.id);
    const ungroupedEntry = {
      name: 'Без групп',
      count: ungroupedIds.length,
      profileIds: ungroupedIds,
      color: groupMeta.get('Без групп')?.colorOverride || 'var(--text-dim)',
      description: groupMeta.get('Без групп')?.description || '',
      virtual: true,
      icon: '📭',
    };

    // ── 3. Виртуальные сессионные группы (Minimax / Claude / GitHub) ──────
    const minimaxEntry = { name: 'Minimax', count: 0, profileIds: [], color: groupMeta.get('Minimax')?.colorOverride || '#06b6d4', description: groupMeta.get('Minimax')?.description || '', virtual: true, icon: '🤖' };
    const claudeEntry  = { name: 'Claude',  count: 0, profileIds: [], color: groupMeta.get('Claude')?.colorOverride  || '#8b5cf6', description: groupMeta.get('Claude')?.description  || '', virtual: true, icon: '🧠' };
    const githubEntry  = { name: 'GitHub',  count: 0, profileIds: [], color: groupMeta.get('GitHub')?.colorOverride  || '#1f2328', description: groupMeta.get('GitHub')?.description  || '', virtual: true, icon: '🐙' };

    try {
      const cookiesPerProfile = await Promise.all(
        profiles.map(async (p) => {
          try { return [p.id, await cookieStore.loadSnapshot(p.id)]; }
          catch { return [p.id, null]; }
        })
      );
      for (const [id, cookies] of cookiesPerProfile) {
        if (!cookies) continue;
        if (cookieHasDomain(cookies, MINIMAX_DOMAINS)) { minimaxEntry.profileIds.push(id); minimaxEntry.count++; }
        if (cookieHasDomain(cookies, CLAUDE_DOMAINS))  { claudeEntry.profileIds.push(id);  claudeEntry.count++;  }
        if (cookieHasDomain(cookies, GITHUB_DOMAINS))  { githubEntry.profileIds.push(id);  githubEntry.count++;  }
      }
    } catch (e) {
      logger?.warn({ err: e.message }, 'virtual group scan failed (non-fatal)');
    }

    // ── 4. Собираем итоговый массив: реальные → Без групп → сессионные ────
    const result = [
      ...Array.from(realGroups.values()),
      ungroupedEntry,
      minimaxEntry,
      claudeEntry,
      githubEntry,
    ];

    return { ok: true, data: result };
  });

  // ── GET /groups/sessions-summary ──────────────────────────────────────────
  app.get('/groups/sessions-summary', async (req, reply) => {
    const profiles = profileManager.list();
    const minimaxIds = [];
    const claudeIds  = [];
    const githubIds  = [];

    await Promise.all(profiles.map(async (p) => {
      try {
        const cookies = await cookieStore.loadSnapshot(p.id);
        if (!cookies) return;
        if (cookieHasDomain(cookies, MINIMAX_DOMAINS)) minimaxIds.push(p.id);
        if (cookieHasDomain(cookies, CLAUDE_DOMAINS))  claudeIds.push(p.id);
        if (cookieHasDomain(cookies, GITHUB_DOMAINS))  githubIds.push(p.id);
      } catch { /* soft-fail per profile */ }
    }));

    return {
      ok: true,
      data: {
        minimax: { ids: minimaxIds, count: minimaxIds.length },
        claude:  { ids: claudeIds,  count: claudeIds.length  },
        github:  { ids: githubIds,  count: githubIds.length  },
      },
    };
  });

  // ── POST /groups ───────────────────────────────────────────────────────────
  app.post('/groups', async (req, reply) => {
    const { name, color, description } = req.body || {};
    if (!name) {
      return reply.code(400).send({ ok: false, error: 'Group name required' });
    }
    if (VIRTUAL_GROUP_NAMES.has(name)) {
      return reply.code(400).send({ ok: false, error: `"${name}" is a reserved virtual group name` });
    }
    if (description !== undefined || color !== undefined) {
      const existing = groupMeta.get(name) || {};
      groupMeta.set(name, {
        ...existing,
        ...(description !== undefined ? { description } : {}),
        ...(color !== undefined ? { colorOverride: color } : {}),
      });
      await _saveMeta(profilesDir, logger);
    }
    logger?.info({ groupName: name, via: 'rest' }, 'group created via API');
    reply.code(201).send({ ok: true, data: { name, color: color || getGroupColor(name), description: description || '' } });
  });

  // ── PATCH /groups/:name ────────────────────────────────────────────────────
  app.patch('/groups/:name', async (req, reply) => {
    const oldName = req.params.name;
    const { name, color } = req.body || {};

    const profiles = profileManager.list();
    const profilesInGroup = profiles.filter(p => p.group === oldName);

    for (const profile of profilesInGroup) {
      await profileManager.update(profile.id, { group: name || oldName });
    }

    if (name && name !== oldName && groupMeta.has(oldName)) {
      groupMeta.set(name, groupMeta.get(oldName));
      groupMeta.delete(oldName);
    }
    if (color) {
      const existing = groupMeta.get(name || oldName) || {};
      groupMeta.set(name || oldName, { ...existing, colorOverride: color });
    }
    await _saveMeta(profilesDir, logger);

    logger?.info({ oldName, newName: name || oldName, via: 'rest' }, 'group updated via API');
    return { ok: true, data: { name: name || oldName, count: profilesInGroup.length } };
  });

  // ── PATCH /groups/:name/meta ───────────────────────────────────────────────
  app.patch('/groups/:name/meta', async (req, reply) => {
    const groupName = req.params.name;
    const { description, color } = req.body || {};

    const existing = groupMeta.get(groupName) || {};
    const updated = {
      ...existing,
      ...(description !== undefined ? { description } : {}),
      ...(color !== undefined ? { colorOverride: color } : {}),
    };
    groupMeta.set(groupName, updated);
    await _saveMeta(profilesDir, logger);

    logger?.info({ groupName, via: 'rest' }, 'group meta updated via API');
    return { ok: true, data: { name: groupName, ...updated } };
  });

  // ── DELETE /groups/:name ───────────────────────────────────────────────────
  app.delete('/groups/:name', async (req, reply) => {
    const groupName = req.params.name;

    if (VIRTUAL_GROUP_NAMES.has(groupName)) {
      return reply.code(400).send({
        ok: false,
        error: `Group "${groupName}" is virtual and cannot be deleted. Clear the description via PATCH /groups/${encodeURIComponent(groupName)}/meta instead.`,
      });
    }

    const profiles = profileManager.list();
    const profilesInGroup = profiles.filter(p => p.group === groupName);

    for (const profile of profilesInGroup) {
      await profileManager.update(profile.id, { group: null });
    }

    groupMeta.delete(groupName);
    await _saveMeta(profilesDir, logger);

    logger?.info({ groupName, count: profilesInGroup.length, via: 'rest' }, 'group deleted via API');
    return { ok: true, data: { ungrouped: profilesInGroup.length } };
  });

  // ── POST /groups/:name/profiles/:profileId ────────────────────────────────
  app.post('/groups/:name/profiles/:profileId', async (req, reply) => {
    const profile = profileManager.get(req.params.profileId);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });

    await profileManager.update(req.params.profileId, { group: req.params.name });

    logger?.info({ profileId: req.params.profileId, groupName: req.params.name, via: 'rest' }, 'profile added to group via API');
    return { ok: true, data: { group: req.params.name } };
  });

  // ── DELETE /groups/:name/profiles/:profileId ──────────────────────────────
  app.delete('/groups/:name/profiles/:profileId', async (req, reply) => {
    const profile = profileManager.get(req.params.profileId);
    if (!profile) return reply.code(404).send({ ok: false, error: 'Profile not found' });

    await profileManager.update(req.params.profileId, { group: null });

    logger?.info({ profileId: req.params.profileId, groupName: req.params.name, via: 'rest' }, 'profile removed from group via API');
    return { ok: true, data: { group: null } };
  });

  // ── POST /groups/:name/bulk-move ──────────────────────────────────────────
  app.post('/groups/:name/bulk-move', async (req, reply) => {
    const { profileIds } = req.body || {};
    if (!Array.isArray(profileIds)) {
      return reply.code(400).send({ ok: false, error: 'profileIds array required' });
    }

    let moved = 0;
    let errors = 0;

    for (const profileId of profileIds) {
      try {
        await profileManager.update(profileId, { group: req.params.name });
        moved++;
      } catch (e) {
        errors++;
      }
    }

    logger?.info({ groupName: req.params.name, moved, errors, via: 'rest' }, 'bulk move to group via API');
    return { ok: true, data: { moved, errors } };
  });

  // ── GET /groups/:name/stats ───────────────────────────────────────────────
  app.get('/groups/:name/stats', async (req, reply) => {
    const profiles = profileManager.list();
    const profilesInGroup = profiles.filter(p => p.group === req.params.name);

    const stats = {
      name: req.params.name,
      count: profilesInGroup.length,
      running: 0,
      byAccountType: { gmail: 0, outlook: 0, other: 0 },
      byEngine: { cloakbrowser: 0, patchright: 0, auto: 0 },
    };

    profilesInGroup.forEach(profile => {
      const accType = profile.account?.type || 'other';
      stats.byAccountType[accType] = (stats.byAccountType[accType] || 0) + 1;

      const engine = profile.engine || 'auto';
      stats.byEngine[engine] = (stats.byEngine[engine] || 0) + 1;
    });

    return { ok: true, data: stats };
  });
}

function getGroupColor(name) {
  const colors = [
    '#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1'
  ];
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return colors[Math.abs(hash) % colors.length];
}
