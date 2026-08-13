import React, { useEffect, useState, useCallback, useMemo } from 'react';
import Sidebar from './components/Sidebar.jsx';
import Topbar from './components/Topbar.jsx';
import ProfileTable from './components/ProfileTable.jsx';
import ProfileDetail from './components/ProfileDetail.jsx';
import ProfileForm from './components/ProfileForm.jsx';
import { api, connectStatusSocket } from './api.js';
import CommonExtensions from './components/CommonExtensions.jsx';
import CookieWarmer from './components/CookieWarmer.jsx';
import TrashModal from './components/TrashModal.jsx';
import { parseTxtImport, parseTabularImport, isLegacyBulkFormat } from './importProfiles.js';
import ProxyList from './components/ProxyList.jsx';
import TrafficCapture from './components/TrafficCapture.jsx';
import Scrapers from './components/Scrapers.jsx';
import WalkerPanel from './components/WalkerPanel.jsx';
import SettingsPage from './components/SettingsPage.jsx';

const REFRESH_MS = 8000;
const PAGE_SIZE = 100;

const STORAGE_KEY_THEME    = 'msb.theme';
const STORAGE_KEY_SIDEBAR  = 'msb.sidebar';
const STORAGE_KEY_ALLGROUPS = 'msb.allGroupsMode';

// Зарезервированные виртуальные имена — не берутся из profile.group
const VIRTUAL_GROUP_KEYS = {
  ungrouped: '__ungrouped__',
  minimax:   '__minimax__',
  claude:    '__claude__',
  github:    '__github__',
};
const VIRTUAL_GROUP_NAMES = new Set(['Без групп', 'Minimax', 'Claude', 'GitHub']);

function useAllGroupsMode() {
  const [mode, setMode] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY_ALLGROUPS) || 'all'; } catch { return 'all'; }
  });
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY_ALLGROUPS, mode); } catch {}
  }, [mode]);
  return [mode, setMode];
}

export default function App() {
  const [profiles, setProfiles]     = useState([]);
  const [running, setRunning]       = useState([]);
  const [currentPage, setCurrentPage] = useState('profiles');
  const [selectedId, setSelectedId] = useState(() => {
    try {
      const m = window.location.hash.match(/[#&]lastProfile=([^&]+)/);
      if (m) return decodeURIComponent(m[1]);
    } catch {}
    return null;
  });

  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY_THEME) || 'light'; } catch { return 'light'; }
  });
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY_SIDEBAR) === '1'; } catch { return false; }
  });
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    try { localStorage.setItem(STORAGE_KEY_THEME, theme); } catch {}
  }, [theme]);
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY_SIDEBAR, sidebarCollapsed ? '1' : '0'); } catch {}
  }, [sidebarCollapsed]);

  const [editorOpen, setEditorOpen]     = useState(false);
  const [editorInitial, setEditorInitial] = useState(null);
  const [importBusy, setImportBusy]     = useState(false);
  const [extOpen, setExtOpen]           = useState(false);
  const [warmerOpen, setWarmerOpen]     = useState(false);
  const [warmerRunning, setWarmerRunning] = useState(false);
  const [trashOpen, setTrashOpen]       = useState(false);
  const [trashCount, setTrashCount]     = useState(0);
  const [allGroupsMode, setAllGroupsMode] = useAllGroupsMode();
  const stopWarmerRef = React.useRef(null);

  const [workspaceState, setWorkspaceState] = useState({ phase: 'idle' });
  const workspaceStateRef = React.useRef(workspaceState);
  workspaceStateRef.current = workspaceState;

  const [groupFilter, setGroupFilter] = useState('all');
  const [search, setSearch]           = useState('');
  const [sortBy, setSortBy]           = useState('number');
  const [sortDir, setSortDir]         = useState('asc');
  const [page, setPage]               = useState(1);
  const [selected, setSelected]       = useState(() => new Set());
  const [busyIds, setBusyIds]         = useState(() => new Set());

  // ─── Единственный источник правды для групп ───────────────────────────────
  // Сервер возвращает в /groups всё: реальные группы + виртуальные (Без групп,
  // Minimax, Claude, GitHub) с правильными счётчиками. Нам не нужен
  // sessions-summary отдельно — это дублирует данные и вызывает расхождения.
  const [groups, setGroups] = useState([]);

  const refreshGroups = useCallback(async () => {
    try {
      const data = await api.groups.list();
      if (Array.isArray(data)) setGroups(data);
    } catch {}
  }, []);

  // ─── Profiles + running ───────────────────────────────────────────────────
  const refresh = useCallback(async () => {
    try {
      const [ps, rs] = await Promise.all([api.profiles.list(), api.browser.status()]);
      setProfiles(ps || []);
      setRunning(rs || []);
    } catch (e) {
      console.error('refresh failed', e);
    }
  }, []);

  const refreshTrashCount = useCallback(async () => {
    try {
      const items = await api.trash.list();
      setTrashCount(Array.isArray(items) ? items.length : 0);
    } catch {}
  }, []);

  // Единый цикл обновления — profiles + groups вместе
  useEffect(() => {
    refresh();
    refreshGroups();
    refreshTrashCount();
    const t1 = setInterval(refresh,       REFRESH_MS);
    const t2 = setInterval(refreshGroups, REFRESH_MS);
    const t3 = setInterval(refreshTrashCount, 30000);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); };
  }, [refresh, refreshGroups, refreshTrashCount]);

  // ─── Workspace ────────────────────────────────────────────────────────────
  const refreshWorkspaceStatus = useCallback(async () => {
    try {
      const s = await api.workspace.status();
      if (!s) return;
      if (workspaceStateRef.current.phase === 'launching') return;
      setWorkspaceState({ phase: s.profile ? 'running' : 'idle', profile: s.profile, flask: s.flask, config: s.config, error: null });
    } catch {}
  }, []);

  const handleLaunchWorkspace = useCallback(async () => {
    if (workspaceStateRef.current.phase === 'launching') return;
    setWorkspaceState({ phase: 'launching' });
    try {
      const res = await api.workspace.launch();
      if (res?.ok) {
        setWorkspaceState({ phase: 'running', profile: res.profile, flask: { running: true, url: res.flask?.url, ...res.flask }, browser: res.browser, error: null });
      } else {
        setWorkspaceState({ phase: 'error', error: res?.error || 'launch failed', profile: res?.profile || null, flask: res?.flask || null });
      }
    } catch (e) {
      setWorkspaceState({ phase: 'error', error: e.message || String(e) });
    }
  }, []);

  useEffect(() => {
    refreshWorkspaceStatus();
    const t = setInterval(refreshWorkspaceStatus, 15000);
    return () => clearInterval(t);
  }, [refreshWorkspaceStatus]);

  // ─── WebSocket статус браузеров ───────────────────────────────────────────
  useEffect(() => {
    const disconnect = connectStatusSocket((msg) => {
      if (msg.type === 'snapshot') {
        setRunning(msg.running || []);
      } else if (msg.type === 'started') {
        setRunning((prev) => prev.some((r) => r.id === msg.id) ? prev : [...prev, { id: msg.id }]);
      } else if (msg.type === 'stopped') {
        setRunning((prev) => prev.filter((r) => r.id !== msg.id));
      }
    });
    return disconnect;
  }, []);

  // ─── Derived data из groups (единый источник) ─────────────────────────────
  // Из массива groups, который вернул сервер, строим:
  //   - realGroups: пользовательские (virtual: false)
  //   - виртуальные по имени: Без групп / Minimax / Claude / GitHub
  const { realGroups, ungroupedGroup, minimaxGroup, claudeGroup, githubGroup } = useMemo(() => {
    const real      = groups.filter(g => !g.virtual && !VIRTUAL_GROUP_NAMES.has(g.name));
    const byName    = (name) => groups.find(g => g.name === name) || { name, count: 0, profileIds: [] };
    return {
      realGroups:    real,
      ungroupedGroup: byName('Без групп'),
      minimaxGroup:  byName('Minimax'),
      claudeGroup:   byName('Claude'),
      githubGroup:   byName('GitHub'),
    };
  }, [groups]);

  // ID-сеты для быстрой фильтрации таблицы
  const minimaxIdSet = useMemo(() => new Set(minimaxGroup.profileIds || []), [minimaxGroup]);
  const claudeIdSet  = useMemo(() => new Set(claudeGroup.profileIds  || []), [claudeGroup]);
  const githubIdSet  = useMemo(() => new Set(githubGroup.profileIds  || []), [githubGroup]);

  // ─── Чипы групп ───────────────────────────────────────────────────────────
  const groupChips = useMemo(() => {
    const totalCount     = profiles.length;
    const ungroupedCount = ungroupedGroup.count || 0;

    const allCount = allGroupsMode === 'ungrouped' ? ungroupedCount : totalCount;
    const allLabel = allGroupsMode === 'ungrouped' ? 'Все (без групп)' : 'Все группы';

    const out = [{ key: 'all', label: allLabel, count: allCount }];

    // Пользовательские группы
    for (const g of realGroups) {
      out.push({ key: g.name, label: g.name, count: g.count || 0 });
    }

    // Виртуальные — строго из groups, никаких дублей
    out.push({ key: '__ungrouped__', label: 'Без групп',  count: ungroupedGroup.count || 0 });
    if (minimaxGroup.count > 0) out.push({ key: '__minimax__', label: 'Minimax', count: minimaxGroup.count });
    if (claudeGroup.count  > 0) out.push({ key: '__claude__',  label: 'Claude',  count: claudeGroup.count  });
    if (githubGroup.count  > 0) out.push({ key: '__github__',  label: 'GitHub',  count: githubGroup.count  });

    return out;
  }, [profiles.length, realGroups, ungroupedGroup, minimaxGroup, claudeGroup, githubGroup, allGroupsMode]);

  // ─── Filtering / sorting / paging ─────────────────────────────────────────
  const filtered = useMemo(() => {
    let out = profiles;
    if (groupFilter === 'all') {
      if (allGroupsMode === 'ungrouped') out = out.filter(p => !p.group);
    } else if (groupFilter === '__ungrouped__') {
      out = out.filter(p => !p.group);
    } else if (groupFilter === '__minimax__') {
      out = out.filter(p => minimaxIdSet.has(p.id));
    } else if (groupFilter === '__claude__') {
      out = out.filter(p => claudeIdSet.has(p.id));
    } else if (groupFilter === '__github__') {
      out = out.filter(p => githubIdSet.has(p.id));
    } else {
      out = out.filter(p => p.group === groupFilter);
    }

    const q = search.trim().toLowerCase();
    if (q) {
      out = out.filter(p =>
        (p.name || '').toLowerCase().includes(q) ||
        (p.account?.email || '').toLowerCase().includes(q) ||
        String(p.number || '').includes(q) ||
        (p.notes || '').toLowerCase().includes(q)
      );
    }

    return [...out].sort((a, b) => {
      let cmp;
      if (sortBy === 'number')  cmp = (a.number || 0) - (b.number || 0);
      else if (sortBy === 'account') cmp = String(a.account?.email || '').localeCompare(String(b.account?.email || ''));
      else cmp = String(a[sortBy] || '').localeCompare(String(b[sortBy] || ''));
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [profiles, groupFilter, search, sortBy, sortDir, minimaxIdSet, claudeIdSet, githubIdSet, allGroupsMode]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageItems  = useMemo(() => filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE), [filtered, page]);

  useEffect(() => { setPage(1); }, [groupFilter, search, sortBy, sortDir]);

  const selectedProfile = useMemo(() => profiles.find(p => p.id === selectedId) || null, [profiles, selectedId]);
  const selectedRunning = useMemo(() => running.find(r => r.id === selectedId) || null, [running, selectedId]);

  // ─── CRUD ─────────────────────────────────────────────────────────────────
  const openCreate = () => { setEditorInitial(null); setEditorOpen(true); };
  const openEdit   = async () => {
    if (!selectedProfile) return;
    const full = await api.profiles.get(selectedId);
    setEditorInitial(full);
    setEditorOpen(true);
  };

  const handleSave = async (data) => {
    if (data.id) await api.profiles.update(data.id, data);
    else { const c = await api.profiles.create(data); setSelectedId(c.id); }
    setEditorOpen(false);
    refresh();
  };

  const handleSendToTrash = async (id) => {
    if (running.some(r => r.id === id)) { try { await api.browser.stop(id); } catch {} }
    await api.trash.send(id);
    refresh();
    refreshTrashCount();
  };

  const handleDelete = async () => {
    if (!selectedProfile) return;
    if (!confirm(`Отправить профиль "${selectedProfile.name}" в корзину?`)) return;
    try { await handleSendToTrash(selectedId); setSelectedId(null); }
    catch (e) { alert(`Не удалось: ${e.message || e}`); }
  };

  const handleStart = (id) => doStart(id);
  const handleStop  = (id) => doStop(id);

  async function doStart(id) {
    setBusyIds(prev => new Set(prev).add(id));
    try {
      if (running.some(r => r.id === id)) await api.browser.stop(id);
      await api.browser.start(id, {});
      refresh();
    } finally {
      setBusyIds(prev => { const n = new Set(prev); n.delete(id); return n; });
    }
  }
  async function doStop(id) {
    setBusyIds(prev => new Set(prev).add(id));
    try { await api.browser.stop(id); refresh(); }
    finally { setBusyIds(prev => { const n = new Set(prev); n.delete(id); return n; }); }
  }

  const handleBulkDelete = async (ids) => {
    if (!ids?.length) return;
    if (!confirm(`Отправить ${ids.length} профил${ids.length === 1 ? 'ь' : ids.length < 5 ? 'я' : 'ей'} в корзину?`)) return;
    for (const id of ids) { try { await handleSendToTrash(id); } catch {} }
    setSelected(new Set());
    refresh();
    refreshTrashCount();
  };

  const handleEditProxy = async (id, currentProxy) => {
    const fmt = (p) => p ? `${p.protocol}://${p.username && p.password ? `${p.username}:${p.password}@` : ''}${p.host}:${p.port}` : '';
    const val = prompt('Введите новый прокси (protocol://user:pass@host:port) или оставьте пустым для удаления:', fmt(currentProxy));
    if (val !== null && val.trim() !== fmt(currentProxy)) {
      try { await api.profiles.update(id, { proxy: val.trim() || null }); refresh(); }
      catch (e) { alert(`Ошибка: ${e.message || e}`); }
    }
  };

  const handleMenu = async (action, id) => {
    if (action === 'rename') {
      const p = profiles.find(x => x.id === id);
      if (!p) return;
      const name = prompt('Новое имя:', p.name);
      if (name?.trim() && name !== p.name) { await api.profiles.update(id, { name: name.trim() }); refresh(); }
    } else if (action === 'flag') {
      const p = profiles.find(x => x.id === id);
      if (!p) return;
      await api.profiles.update(id, { flagged: !p.flagged });
      refresh();
    } else if (action === 'moveToEnd') {
      const max = profiles.reduce((m, x) => Math.max(m, x.number || 0), 0);
      await api.profiles.update(id, { sortOrder: max + 1 });
      refresh();
    } else if (action === 'trash') {
      if (!confirm('Отправить профиль в корзину?')) return;
      await handleSendToTrash(id);
    }
  };

  const runImport = async (file, parse, kind) => {
    setImportBusy(true);
    try {
      const text = await file.text();
      const { profiles: newProfiles } = parse(text);
      if (!newProfiles.length) { alert(`Импорт (${kind}): не найдено профилей в "${file.name}".`); return; }
      let created = 0, errors = 0;
      for (const p of newProfiles) { try { await api.profiles.create(p); created++; } catch { errors++; } }
      alert(`Импорт (${kind}) из "${file.name}": создано ${created}.${errors ? ` Ошибок: ${errors}.` : ''}`);
      refresh();
    } catch (e) { alert(`Импорт не удался: ${e.message || e}`); }
    finally { setImportBusy(false); }
  };

  const handleImportTxt = async (file) => {
    const text = await file.text();
    if (isLegacyBulkFormat(text)) {
      try {
        setImportBusy(true);
        const { imported } = await api.profiles.importLegacyBulk(text);
        if (!imported.length) { alert(`Импорт (TXT): не найдено профилей в "${file.name}".`); return; }
        alert(`Импорт (TXT): создано ${imported.length} профилей.`);
        refresh();
      } catch (e) { alert(`Импорт (TXT) не удался: ${e.message || e}`); }
      finally { setImportBusy(false); }
      return;
    }
    runImport(file, parseTxtImport, 'TXT');
  };
  const handleImportTable = (file) => runImport(file, parseTabularImport, 'таблица');

  const onToggleSort = (id) => {
    if (sortBy === id) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortBy(id); setSortDir('asc'); }
  };
  const onSelectAll = (checked) => {
    setSelected(checked ? new Set(pageItems.map(p => p.id)) : new Set());
  };
  const onSelectOne = (id, checked) => {
    setSelected(prev => { const n = new Set(prev); checked ? n.add(id) : n.delete(id); return n; });
  };
  const allChecked = pageItems.length > 0 && pageItems.every(p => selected.has(p.id));

  // ─── Page router ──────────────────────────────────────────────────────────
  const renderPage = () => {
    switch (currentPage) {
      case 'profiles':
        return (
          <>
            <div className="msb-toolbar">
              <button className="msb-toolbar-tab active">🌐 Браузер</button>
              <div className="msb-spacer" />
              <input className="msb-input search" placeholder="🔍 Поиск" value={search} onChange={e => setSearch(e.target.value)} />
              <button className="msb-btn" onClick={openCreate}>📥 Массовый импорт</button>
              <button className="msb-btn primary" onClick={openCreate}>+ Создать профиль</button>
            </div>

            <div className="msb-chips">
              {groupChips.map(chip => (
                <button key={chip.key} className={`msb-chip${groupFilter === chip.key ? ' active' : ''}`} onClick={() => setGroupFilter(chip.key)}>
                  {chip.label}<span className="msb-chip-count">{chip.count}</span>
                </button>
              ))}
            </div>

            <ProfileTable
              profiles={pageItems} running={running} groupFilter={groupFilter} search={search}
              sortBy={sortBy} sortDir={sortDir} onToggleSort={onToggleSort}
              checkedIds={selected} allChecked={allChecked} onSelectAll={onSelectAll} onSelectOne={onSelectOne}
              busyIds={busyIds} onStart={handleStart} onStop={handleStop}
              onMenu={handleMenu} onEditProxy={handleEditProxy}
              onOpenDetail={id => setSelectedId(id)} pageSize={PAGE_SIZE} page={page}
            />

            <div className="msb-table-footer">
              <span>Всего: <strong style={{ color: 'var(--text)' }}>{filtered.length}</strong> профилей</span>
              <button className="msb-icon-btn" onClick={() => handleBulkDelete([...selected])} disabled={selected.size === 0} title="В корзину" style={{ opacity: selected.size === 0 ? 0.4 : 1 }}>🗑</button>
              <button className="msb-icon-btn" onClick={() => setSelected(new Set(pageItems.map(p => p.id)))} disabled={pageItems.length === 0} title="Выбрать всё">☑</button>
              <div className="msb-table-footer-spacer" />
              <button className="msb-page-btn" disabled={page <= 1} onClick={() => setPage(1)}>«</button>
              <button className="msb-page-btn" disabled={page <= 1} onClick={() => setPage(p => Math.max(1, p - 1))}>‹</button>
              <span style={{ minWidth: 70, textAlign: 'center' }}>{page} / {totalPages}</span>
              <button className="msb-page-btn" disabled={page >= totalPages} onClick={() => setPage(p => Math.min(totalPages, p + 1))}>›</button>
              <button className="msb-page-btn" disabled={page >= totalPages} onClick={() => setPage(totalPages)}>»</button>
            </div>
          </>
        );

      case 'trash':
        setTrashOpen(true);
        setCurrentPage('profiles');
        return null;
      case 'extensions':
        return extOpen ? <CommonExtensions onClose={() => setExtOpen(false)} /> : (
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: 24, textAlign: 'center', color: 'var(--text-dim)' }}>
            <button className="msb-btn primary" onClick={() => setExtOpen(true)}>🧩 Открыть общие расширения</button>
          </div>
        );
      case 'warmer':
        return <CookieWarmer profiles={profiles} running={running} onClose={() => { setCurrentPage('profiles'); setWarmerOpen(false); refresh(); }} onRunningChange={setWarmerRunning} stopWarmerRef={stopWarmerRef} />;
      case 'proxies':
        return <ProxyList profiles={profiles} onRefresh={refresh} />;
      case 'traffic':
        return <TrafficCapture profiles={profiles} running={running} />;
      case 'scrapers':
        return <Scrapers />;
      case 'groups':
        return (
          <GroupsView
            profiles={profiles}
            groups={groups}
            onRefresh={() => { refresh(); refreshGroups(); }}
            allGroupsMode={allGroupsMode}
            onAllGroupsModeChange={setAllGroupsMode}
          />
        );
      case 'audit':
        return <PlaceholderPage title="Логи / Аудит" hint="Журнал API-запросов и аудит действий. Endpoint: GET /audit" />;
      case 'monitoring':
        return <PlaceholderPage title="Мониторинг" hint="Графики активности, p95 start time, error rate." />;
      case 'automation':
        return <WalkerPanel profiles={profiles} running={running} onRefresh={refresh} />;
      case 'settings':
        return <SettingsView onImportTxt={handleImportTxt} onImportTable={handleImportTable} importBusy={importBusy} />;
      default:
        return <PlaceholderPage title={currentPage} />;
    }
  };

  return (
    <div className="app">
      <Sidebar
        currentPage={currentPage} onNavigate={setCurrentPage}
        collapsed={sidebarCollapsed} onToggle={() => setSidebarCollapsed(v => !v)}
        profileCount={profiles.length} runningCount={running.length} trashCount={trashCount}
      />
      <div className="msb-main">
        <Topbar
          theme={theme} onToggleTheme={() => setTheme(t => t === 'dark' ? 'light' : 'dark')}
          encryptionEnabled={false}
          onEncryptionClick={() => alert('Шифрование настраивается через API: POST /api/env/encrypt-key')}
          user={{ name: 'MSB Local', avatar: 'M' }}
          workspaceState={workspaceState} onLaunchWorkspace={handleLaunchWorkspace}
        />
        {renderPage()}

        {currentPage === 'profiles' && selectedProfile && (
          <div className="msb-modal-backdrop" onClick={() => setSelectedId(null)} style={{ alignItems: 'stretch', justifyContent: 'flex-end' }}>
            <div onClick={e => e.stopPropagation()} style={{ background: 'var(--bg)', borderLeft: '1px solid var(--border)', width: 'min(620px, 90vw)', height: '100%', overflowY: 'auto', boxShadow: 'var(--shadow-lg)', position: 'relative' }}>
              <button onClick={() => setSelectedId(null)} className="msb-icon-btn" style={{ position: 'absolute', top: 12, right: 12, zIndex: 2 }} title="Закрыть">✕</button>
              <ProfileDetail profile={selectedProfile} runningInfo={selectedRunning} onEdit={openEdit} onDelete={handleDelete} onStart={() => handleStart(selectedId)} onStop={() => handleStop(selectedId)} onRefresh={refresh} />
            </div>
          </div>
        )}
      </div>

      {editorOpen && <ProfileForm initial={editorInitial} onCancel={() => setEditorOpen(false)} onSave={handleSave} />}
      {warmerOpen && <CookieWarmer profiles={profiles} running={running} onClose={() => { setWarmerOpen(false); setWarmerRunning(false); refresh(); }} onRunningChange={setWarmerRunning} stopWarmerRef={stopWarmerRef} />}
      {trashOpen  && <TrashModal open={trashOpen} onClose={() => { setTrashOpen(false); refresh(); refreshTrashCount(); }} onRestored={() => { refresh(); refreshTrashCount(); }} />}
    </div>
  );
}

function PlaceholderPage({ title, hint }) {
  return (
    <div className="msb-empty">
      <h3>{title}</h3>
      <p style={{ maxWidth: 480 }}>{hint || 'В разработке.'}</p>
    </div>
  );
}

// ─── GroupsView ───────────────────────────────────────────────────────────────

const VIRTUAL_DEFAULT_DESCRIPTIONS = {
  'Без групп': 'Профили без `profile.group`. Назначь группу через PATCH /api/profiles/:id { group: null }.',
  'Minimax':   'Профили с активной сессией на minimax.com / hailuo.video (по cookies-snapshot).',
  'Claude':    'Профили с активной сессией на claude.ai / anthropic.com (по cookies-snapshot).',
  'GitHub':    'Профили с активной сессией на github.com (по cookies-snapshot).',
};

const VIRTUAL_ICONS = { 'Без групп': '📭', 'Minimax': '🤖', 'Claude': '🧠', 'GitHub': '🐙' };
const VIRTUAL_COLORS = { 'Без групп': 'var(--text-dim)', 'Minimax': '#06b6d4', 'Claude': '#8b5cf6', 'GitHub': '#1f2328' };

function GroupsView({ profiles, groups, onRefresh, allGroupsMode, onAllGroupsModeChange }) {
  const [expandedGroup, setExpandedGroup] = useState(null);
  const [editingDesc, setEditingDesc]     = useState(null);
  const [descDraft, setDescDraft]         = useState('');
  const [savingDesc, setSavingDesc]       = useState(false);

  const ungroupedCount = profiles.filter(p => !p.group).length;
  const groupedCount   = profiles.length - ungroupedCount;
  const isUngroupedMode = allGroupsMode === 'ungrouped';

  // Данные уже пришли с сервера — просто делим на реальные и виртуальные
  const realRows    = groups.filter(g => !g.virtual && !VIRTUAL_GROUP_NAMES.has(g.name));
  const virtualRows = groups.filter(g => g.virtual  ||  VIRTUAL_GROUP_NAMES.has(g.name));

  const startEditDesc  = (g) => { setEditingDesc(g.name); setDescDraft(g.description || ''); };
  const cancelEditDesc = ()  => { setEditingDesc(null); setDescDraft(''); };

  const saveDesc = async (groupName) => {
    setSavingDesc(true);
    try { await api.groups.updateMeta(groupName, { description: descDraft.trim() }); await onRefresh(); setEditingDesc(null); setDescDraft(''); }
    catch (e) { alert(`Не удалось сохранить: ${e.message || e}`); }
    finally { setSavingDesc(false); }
  };

  const clearDesc = async (groupName) => {
    if (!confirm(`Удалить описание группы «${groupName}»?`)) return;
    setSavingDesc(true);
    try { await api.groups.updateMeta(groupName, { description: '' }); await onRefresh(); }
    catch (e) { alert(`Ошибка: ${e.message || e}`); }
    finally { setSavingDesc(false); }
  };

  const toggleExpand = (key) => {
    setExpandedGroup(prev => prev === key ? null : key);
    if (editingDesc && editingDesc !== key) cancelEditDesc();
  };

  const tdStyle = { padding: '10px 12px', borderBottom: '1px solid var(--border)', verticalAlign: 'middle' };
  const thStyle = { ...tdStyle, background: 'var(--bg-2)', fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.4, color: 'var(--text-dim)' };

  const renderRow = (g) => {
    const isVirtual     = !!g.virtual || VIRTUAL_GROUP_NAMES.has(g.name);
    const count         = g.count ?? 0;
    const color         = g.color || (isVirtual ? VIRTUAL_COLORS[g.name] : 'var(--accent)') || 'var(--accent)';
    const description   = g.description || (isVirtual ? VIRTUAL_DEFAULT_DESCRIPTIONS[g.name] || '' : '');
    const isExpanded    = expandedGroup === g.name;
    const isEditingThis = editingDesc === g.name;

    return (
      <React.Fragment key={g.name}>
        <tr style={{ cursor: 'pointer', background: isExpanded ? 'var(--bg-2)' : 'transparent' }} onClick={() => toggleExpand(g.name)}>
          <td style={tdStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 13, color: 'var(--text-dim)', userSelect: 'none', transition: 'transform .15s', display: 'inline-block', transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)' }}>▶</span>
              {isVirtual
                ? <span style={{ fontSize: 15 }}>{g.icon || VIRTUAL_ICONS[g.name] || '📁'}</span>
                : <span style={{ display: 'inline-block', width: 12, height: 12, background: color, borderRadius: 3, flexShrink: 0 }} />}
              <strong style={{ fontSize: 13 }}>{g.name}</strong>
              {isVirtual && <span style={{ fontSize: 10, padding: '1px 5px', borderRadius: 10, background: 'var(--bg-3, var(--border))', color: 'var(--text-dim)', marginLeft: 2 }}>авто</span>}
            </div>
          </td>
          <td style={{ ...tdStyle, width: 80, textAlign: 'center' }}>
            <span style={{ display: 'inline-block', minWidth: 28, padding: '2px 8px', borderRadius: 12, background: count > 0 ? color : 'var(--border)', color: count > 0 ? '#fff' : 'var(--text-dim)', fontSize: 12, fontWeight: 600 }}>{count}</span>
          </td>
        </tr>
        {isExpanded && (
          <tr style={{ background: 'var(--bg-2)' }}>
            <td colSpan={2} style={{ padding: '12px 16px 16px 36px', borderBottom: '1px solid var(--border)' }}>
              {isEditingThis ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxWidth: 480 }}>
                  <label style={{ fontSize: 11, color: 'var(--text-dim)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.4 }}>Описание «{g.name}»</label>
                  <textarea autoFocus value={descDraft} onChange={e => setDescDraft(e.target.value)} rows={3} placeholder="Введите описание…"
                    style={{ width: '100%', padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 13, resize: 'vertical', fontFamily: 'inherit', outline: 'none', boxSizing: 'border-box' }}
                    onClick={e => e.stopPropagation()}
                    onKeyDown={e => { if (e.key === 'Escape') cancelEditDesc(); if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) saveDesc(g.name); }}
                  />
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="msb-btn primary" disabled={savingDesc} onClick={e => { e.stopPropagation(); saveDesc(g.name); }} style={{ fontSize: 12, padding: '4px 14px' }}>{savingDesc ? 'Сохранение…' : '✓ Сохранить'}</button>
                    <button className="msb-btn" onClick={e => { e.stopPropagation(); cancelEditDesc(); }} style={{ fontSize: 12, padding: '4px 14px' }}>Отмена</button>
                  </div>
                </div>
              ) : (
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                  <div style={{ flex: 1 }}>
                    {description
                      ? <p style={{ margin: 0, fontSize: 13, color: 'var(--text)', lineHeight: 1.5 }}>{description}</p>
                      : <p style={{ margin: 0, fontSize: 12, color: 'var(--text-dim)', fontStyle: 'italic' }}>Описание не задано</p>}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4, flexShrink: 0 }}>
                    <button className="msb-btn" onClick={e => { e.stopPropagation(); startEditDesc(g); }} style={{ fontSize: 12, padding: '4px 12px' }}>✏️ {description ? 'Изменить' : 'Добавить описание'}</button>
                    {g.description && <button className="msb-btn" onClick={e => { e.stopPropagation(); clearDesc(g.name); }} style={{ fontSize: 11, padding: '3px 10px', color: 'var(--text-dim)' }}>🗑 Сбросить</button>}
                  </div>
                </div>
              )}
            </td>
          </tr>
        )}
      </React.Fragment>
    );
  };

  return (
    <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: 24 }}>
      <h2 style={{ margin: '0 0 16px', fontSize: 18 }}>Группы</h2>

      <div style={{ marginBottom: 18, padding: '12px 16px', background: 'var(--bg-2)', border: '1px solid var(--border)', borderRadius: 8 }}>
        <label style={{ display: 'flex', alignItems: 'flex-start', gap: 12, cursor: 'pointer', userSelect: 'none' }}>
          <input type="checkbox" checked={isUngroupedMode} onChange={e => onAllGroupsModeChange(e.target.checked ? 'ungrouped' : 'all')} style={{ marginTop: 3, width: 16, height: 16, cursor: 'pointer' }} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text)' }}>В чипе «Все группы» показывать только профили без группы</div>
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4, lineHeight: 1.5 }}>
              {isUngroupedMode
                ? `Сейчас «Все группы» = ${ungroupedCount} (без групп). ${groupedCount} профил${groupedCount === 1 ? 'ь' : groupedCount < 5 ? 'я' : 'ей'} в группах показаны только в своих чипах.`
                : `Сейчас «Все группы» = ${profiles.length}. Профили в группах также появятся в своих чипах.`}
            </div>
          </div>
        </label>
      </div>

      <div className="msb-table-wrap" style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
        <table className="msb-table" style={{ tableLayout: 'fixed', width: '100%' }}>
          <colgroup><col /><col style={{ width: 90 }} /></colgroup>
          <thead>
            <tr>
              <th style={thStyle}>Группа</th>
              <th style={{ ...thStyle, textAlign: 'center' }}>Профилей</th>
            </tr>
          </thead>
          <tbody>
            {realRows.map(g => renderRow(g))}
            {virtualRows.map(g => renderRow(g))}
            {realRows.length === 0 && virtualRows.length === 0 && (
              <tr><td colSpan={2} style={{ ...tdStyle, textAlign: 'center', color: 'var(--text-dim)', fontStyle: 'italic' }}>Групп пока нет</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <p style={{ marginTop: 12, color: 'var(--text-dim)', fontSize: 12 }}>
        Группы задаются через <code>PATCH /api/profiles/:id {`{ "group": "Name" }`}</code>. Виртуальные группы (<em>авто</em>) определяются по cookies сессии.
      </p>
    </div>
  );
}

function SettingsView({ onImportTxt, onImportTable, importBusy }) {
  const pick = (accept, handler) => {
    const input = document.createElement('input');
    input.type = 'file'; input.accept = accept;
    input.onchange = () => input.files?.[0] && handler(input.files[0]);
    input.click();
  };
  return (
    <div style={{ flex: 1, overflowY: 'auto', minHeight: 0, padding: 24, maxWidth: 720 }}>
      <h2 style={{ margin: '0 0 16px', fontSize: 18 }}>Настройки</h2>
      <Section title="Импорт">
        <button className="msb-btn" disabled={importBusy} onClick={() => pick('.txt', onImportTxt)}>📥 Импорт TXT</button>
        <button className="msb-btn" disabled={importBusy} onClick={() => pick('.csv,.tsv', onImportTable)}>📊 Импорт таблицы</button>
      </Section>
      <Section title="API">
        <div style={{ color: 'var(--text-dim)', fontSize: 12, lineHeight: 1.6 }}>
          Base: <code>http://127.0.0.1:17248</code><br />
          Документация: <a href="/docs" target="_blank" style={{ color: 'var(--accent)' }}>Swagger UI</a>
        </div>
      </Section>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 24, padding: 16, background: 'var(--bg-2)', border: '1px solid var(--border)', borderRadius: 8 }}>
      <h3 style={{ margin: '0 0 12px', fontSize: 13, color: 'var(--text-dim)', textTransform: 'uppercase', letterSpacing: 0.5 }}>{title}</h3>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>{children}</div>
    </div>
  );
}
