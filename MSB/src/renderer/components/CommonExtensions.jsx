import React, { useState, useEffect, useRef } from 'react';
import { api } from '../api.js';

/**
 * Модальное окно управления общими расширениями браузера.
 * Расширения подключаются ко ВСЕМ профилям при каждом запуске.
 */
export default function CommonExtensions({ onClose }) {
  const [extensions, setExtensions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [inputVal, setInputVal] = useState('');
  const [adding, setAdding] = useState(false);
  const [status, setStatus] = useState(null); // { ok, msg }
  const inputRef = useRef(null);
  const fileInputRef = useRef(null);

  // Electron: системный dialog, window picker
  const isElectron = typeof window !== 'undefined' && typeof window.msb?.extensions?.pickFolder === 'function';

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.extensions.list();
      // result может быть массивом или { extensions: [...] }
      const list = Array.isArray(result) ? result
        : Array.isArray(result?.extensions) ? result.extensions
        : [];
      setExtensions(list);
    } catch (e) {
      setError('Ошибка загрузки: ' + (e?.message || e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    setTimeout(() => inputRef.current?.focus(), 100);
  }, []);

  const showStatus = (ok, msg, ms = 3500) => {
    setStatus({ ok, msg });
    setTimeout(() => setStatus(null), ms);
  };

  const toList = (result) =>
    Array.isArray(result) ? result
    : Array.isArray(result?.extensions) ? result.extensions
    : null;

  const refreshList = async (result) => {
    const list = toList(result);
    if (list !== null) { setExtensions(list); return; }
    await load();
  };

  // --- Добавить путь вручную ---
  const handleAdd = async () => {
    const p = inputVal.trim();
    if (!p) { inputRef.current?.focus(); return; }
    setAdding(true);
    try {
      const result = await api.extensions.add(p);
      await refreshList(result);
      setInputVal('');
      showStatus(true, result?.added === false ? 'Уже добавлено' : 'Добавлено ✓');
    } catch (e) {
      showStatus(false, 'Ошибка: ' + (e?.message || e));
    } finally {
      setAdding(false);
      inputRef.current?.focus();
    }
  };

  // --- Выбрать папку через диалог (только Electron) ---
  const handlePickFolder = async () => {
    setAdding(true);
    try {
      const result = await api.extensions.pickFolder();
      if (result?.canceled) return;
      if (result?.error) { showStatus(false, result.error); return; }
      await refreshList(result);
      showStatus(true, 'Добавлено: ' + (result.path?.split(/[/\\]/).pop() || ''));
    } catch (e) {
      showStatus(false, 'Ошибка: ' + (e?.message || e));
    } finally {
      setAdding(false);
    }
  };

  // --- Загрузить .crx (работает и в web, и в Electron) ---
  const handleCrxFile = async (file) => {
    if (!file) return;
    setAdding(true);
    try {
      const data = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(r.result.split(',')[1]);
        r.onerror = rej;
        r.readAsDataURL(file);
      });
      const result = await api.extensions.installCrx(file.name, data);
      if (!result?.installed) {
        showStatus(false, 'Ошибка: ' + (result?.error || 'неизвестно'));
      } else {
        await refreshList(result);
        showStatus(true, file.name + ' установлено ✓');
      }
    } catch (e) {
      showStatus(false, 'Ошибка: ' + (e?.message || e));
    } finally {
      setAdding(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  // --- Добавить встроенный Tampermonkey ---
  const handleAddTampermonkey = async () => {
    setAdding(true);
    try {
      const result = await api.extensions.addTampermonkey();
      if (result?.error) {
        showStatus(false, result.error);
      } else {
        await refreshList(result);
        showStatus(true, result?.added === false ? 'Tampermonkey уже добавлен' : '🔌 Tampermonkey добавлен ✓');
      }
    } catch (e) {
      showStatus(false, 'Ошибка: ' + (e?.message || e));
    } finally {
      setAdding(false);
    }
  };

  const handleRemove = async (extPath) => {
    try {
      const result = await api.extensions.remove(extPath);
      await refreshList(result);
    } catch (e) {
      showStatus(false, 'Ошибка удаления: ' + (e?.message || e));
    }
  };

  const handleClear = async () => {
    if (!confirm('Удалить все общие расширения?')) return;
    try {
      const result = await api.extensions.clear();
      await refreshList(result);
      showStatus(true, 'Список очищен');
    } catch (e) {
      showStatus(false, 'Ошибка: ' + (e?.message || e));
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleAdd();
    if (e.key === 'Escape') onClose();
  };

  const S = {
    overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.65)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center' },
    modal: { background: '#1a1d23', border: '1px solid #333', borderRadius: 10, padding: '20px 22px', width: 540, maxWidth: '95vw', maxHeight: '85vh', display: 'flex', flexDirection: 'column', gap: 10, boxShadow: '0 8px 32px rgba(0,0,0,0.6)', color: '#e0e0e0' },
    inputRow: { display: 'flex', gap: 8 },
    input: { flex: 1, padding: '7px 10px', borderRadius: 6, border: '1px solid #444', background: '#23272e', color: '#e0e0e0', fontSize: 12, outline: 'none' },
    btnRow: { display: 'flex', gap: 6, flexWrap: 'wrap' },
    btn: { padding: '6px 12px', fontSize: 11, borderRadius: 5, border: '1px solid #555', background: '#2a2d35', color: '#ccc', cursor: 'pointer', whiteSpace: 'nowrap', minWidth: 0, display: 'inline-flex', alignItems: 'center', gap: 4 },
    extItem: { display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 6, background: '#23272e' },
    statusBar: (ok) => ({ padding: '5px 10px', borderRadius: 6, fontSize: 11, background: ok ? 'rgba(46,160,67,0.15)' : 'rgba(224,50,50,0.15)', border: `1px solid ${ok ? '#2ea043' : '#c0392b'}`, color: ok ? '#4caf72' : '#e05555' }),
  };

  return (
    <div style={S.overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div style={S.modal}>

        {/* Заголовок */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <span style={{ fontSize: 15, fontWeight: 600 }}>🧩 Общие расширения</span>
            <span style={{ fontSize: 11, opacity: 0.5, marginLeft: 8 }}>загружаются во ВСЕ профили</span>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 18, color: '#888' }}>✕</button>
        </div>

        {/* Ввод пути вручную */}
        <div style={S.inputRow}>
          <input
            ref={inputRef}
            value={inputVal}
            onChange={(e) => setInputVal(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Путь к папке расширения (с manifest.json)"
            style={S.input}
          />
          <button className="primary" onClick={handleAdd} disabled={adding || !inputVal.trim()} style={{ whiteSpace: 'nowrap', padding: '7px 14px', fontSize: 12 }}>
            + Добавить
          </button>
        </div>

        {/* Кнопки быстрого добавления */}
        <div style={S.btnRow}>
          {isElectron && (
            <button style={S.btn} onClick={handlePickFolder} disabled={adding} title="Открыть диалог выбора папки расширения">
              📂 Выбрать папку…
            </button>
          )}
          <button style={S.btn} onClick={() => fileInputRef.current?.click()} disabled={adding} title="Загрузить .crx — распакуется в MSB/extensions/">
            📦 Загрузить .crx…
          </button>
          <button style={S.btn} onClick={handleAddTampermonkey} disabled={adding} title="Добавить Tampermonkey из MSB/extensions/tampermonkey/">
            🔌 Tampermonkey
          </button>
          <input ref={fileInputRef} type="file" accept=".crx" style={{ display: 'none' }} onChange={(e) => handleCrxFile(e.target.files?.[0])} />
        </div>

        {/* Статус */}
        {status && <div style={S.statusBar(status.ok)}>{status.ok ? '✓' : '✗'} {status.msg}</div>}

        {/* Подсказка */}
        <p style={{ margin: 0, fontSize: 11, opacity: 0.45, lineHeight: 1.6 }}>
          Папка с <code>manifest.json</code> подключается ко всем профилям при следующем запуске браузера.
          Для Tampermonkey: загрузи <code>.crx</code> через кнопку выше — он распакуется автоматически.
        </p>

        {/* Список */}
        <div style={{ flex: 1, overflowY: 'auto', minHeight: 40, maxHeight: 260, display: 'flex', flexDirection: 'column', gap: 4 }}>
          {loading && <div style={{ textAlign: 'center', padding: 16, opacity: 0.4, fontSize: 12 }}>Загрузка...</div>}
          {error && <div style={{ textAlign: 'center', padding: 12, color: '#e05', fontSize: 12 }}>{error}</div>}
          {!loading && !error && extensions.length === 0 && (
            <div style={{ textAlign: 'center', padding: 16, opacity: 0.4, fontSize: 12 }}>Нет добавленных расширений</div>
          )}
          {extensions.map((extPath) => {
            const name = extPath.split(/[/\\]/).filter(Boolean).pop() || extPath;
            return (
              <div key={extPath} style={S.extItem}>
                <span style={{ fontSize: 14 }}>{name.toLowerCase().includes('tamper') ? '🔌' : '🧩'}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#4a9eff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</div>
                  <div style={{ fontSize: 10, opacity: 0.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{extPath}</div>
                </div>
                <button onClick={() => handleRemove(extPath)} title="Удалить" style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#c0392b', fontSize: 15, padding: '0 4px' }}>✕</button>
              </div>
            );
          })}
        </div>

        {/* Футер */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 11, opacity: 0.4 }}>{extensions.length > 0 ? `${extensions.length} расшир.` : ''}</span>
          <div style={{ display: 'flex', gap: 8 }}>
            {extensions.length > 0 && <button onClick={handleClear} style={{ fontSize: 11, padding: '4px 12px' }}>Очистить всё</button>}
            <button onClick={onClose} style={{ fontSize: 11, padding: '4px 12px' }}>Закрыть</button>
          </div>
        </div>

      </div>
    </div>
  );
}
