import React, { useState, useEffect } from 'react';
import { api } from '../api.js';

export default function ProxyList({ profiles = [], onRefresh }) {
  const [proxies, setProxies] = useState([]);
  const [bulkInput, setBulkInput] = useState('');
  const [importing, setImporting] = useState(false);
  const [activeDropdown, setActiveDropdown] = useState(null);

  const fetchProxies = async () => {
    try {
      const data = await api.proxies.list();
      setProxies(data || []);
    } catch (e) {
      console.error('Failed to fetch proxies', e);
    }
  };

  useEffect(() => {
    fetchProxies();
  }, []);

  const handleImport = async () => {
    if (!bulkInput.trim()) return;
    setImporting(true);
    try {
      const res = await api.proxies.bulk(bulkInput);
      alert(`Успешно добавлено: ${res.added}, Пропущено (дубли): ${res.skipped}`);
      setBulkInput('');
      fetchProxies();
    } catch (e) {
      alert(`Ошибка импорта: ${e.message || e}`);
    } finally {
      setImporting(false);
    }
  };

  const handleRemove = async (id) => {
    if (!confirm('Удалить этот прокси из пула?')) return;
    try {
      await api.proxies.remove(id);
      fetchProxies();
    } catch (e) {
      alert(`Ошибка удаления: ${e.message || e}`);
    }
  };

  const handleAssign = async (proxyId, profileId) => {
    try {
      await api.proxies.assign(proxyId, profileId);
      setActiveDropdown(null);
      alert('Прокси назначен профилю');
      if (onRefresh) onRefresh();
    } catch (e) {
      alert(`Ошибка назначения: ${e.message || e}`);
    }
  };

  const assignableProfiles = profiles.filter(p => !p.proxy || !p.proxy.host);

  return (
    <div style={{
      flex: 1,
      padding: '24px',
      display: 'flex',
      flexDirection: 'column',
      gap: '24px',
      overflowY: 'auto',
      background: 'var(--bg)',
      color: 'var(--text)'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ fontSize: '20px', fontWeight: '600', margin: 0 }}>🌐 Proxy Pool</h2>
        <span style={{
          background: 'var(--bg-3)',
          color: 'var(--text-dim)',
          padding: '4px 12px',
          borderRadius: '16px',
          fontSize: '13px',
          fontWeight: '500'
        }}>
          Всего: {proxies.length} прокси
        </span>
      </div>

      {/* Bulk Import */}
      <div style={{
        background: 'var(--bg-2)',
        border: '1px solid var(--border)',
        borderRadius: '12px',
        padding: '16px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px'
      }}>
        <label style={{ fontSize: '13px', fontWeight: '500', color: 'var(--text-dim)' }}>
          Импорт прокси (форматы: host:port, host:port:user:pass, protocol://host:port, protocol://user:pass@host:port)
        </label>
        <textarea
          placeholder="Вставьте список прокси (один на строку)..."
          value={bulkInput}
          onChange={(e) => setBulkInput(e.target.value)}
          rows={5}
          style={{
            width: '100%',
            padding: '12px',
            borderRadius: '8px',
            border: '1px solid var(--border)',
            background: 'var(--bg)',
            color: 'var(--text)',
            fontFamily: 'monospace',
            fontSize: '12px',
            resize: 'vertical',
            outline: 'none',
            transition: 'border-color 0.2s'
          }}
          onFocus={(e) => e.target.style.borderColor = 'var(--accent)'}
          onBlur={(e) => e.target.style.borderColor = 'var(--border)'}
        />
        <button
          onClick={handleImport}
          disabled={importing || !bulkInput.trim()}
          style={{
            alignSelf: 'flex-end',
            padding: '8px 16px',
            background: 'var(--accent)',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            cursor: 'pointer',
            fontWeight: '500',
            opacity: (importing || !bulkInput.trim()) ? 0.6 : 1,
            transition: 'background 0.2s'
          }}
        >
          {importing ? 'Загрузка...' : 'Импортировать'}
        </button>
      </div>

      {/* Proxy List */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {proxies.length === 0 ? (
          <div style={{
            textAlign: 'center',
            padding: '48px 16px',
            color: 'var(--text-dim)',
            border: '2px dashed var(--border)',
            borderRadius: '12px'
          }}>
            <span style={{ fontSize: '32px', display: 'block', marginBottom: '8px' }}>📭</span>
            Нет прокси. Вставьте список выше.
          </div>
        ) : (
          <div style={{
            border: '1px solid var(--border)',
            borderRadius: '12px',
            background: 'var(--bg-2)',
            overflow: 'hidden'
          }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-3)', color: 'var(--text-dim)' }}>
                  <th style={{ textAlign: 'left', padding: '12px 16px', fontWeight: '500' }}>Protocol</th>
                  <th style={{ textAlign: 'left', padding: '12px 16px', fontWeight: '500' }}>Host:Port</th>
                  <th style={{ textAlign: 'left', padding: '12px 16px', fontWeight: '500' }}>Label</th>
                  <th style={{ textAlign: 'right', padding: '12px 16px', fontWeight: '500' }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {proxies.map((proxy) => (
                  <tr key={proxy.id} style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg)' }}>
                    <td style={{ padding: '12px 16px', fontWeight: '600', color: 'var(--accent)' }}>
                      {proxy.protocol.toUpperCase()}
                    </td>
                    <td style={{ padding: '12px 16px', fontFamily: 'monospace' }}>
                      {proxy.host}:{proxy.port}
                      {proxy.username && <span style={{ color: 'var(--text-faint)', marginLeft: '8px' }}>({proxy.username})</span>}
                    </td>
                    <td style={{ padding: '12px 16px', color: 'var(--text-dim)' }}>
                      {proxy.label || '-'}
                    </td>
                    <td style={{ padding: '12px 16px', textAlign: 'right', display: 'flex', gap: '8px', justifyContent: 'flex-end', alignItems: 'center', position: 'relative' }}>
                      {/* Assign button */}
                      <div style={{ position: 'relative' }}>
                        <button
                          onClick={() => setActiveDropdown(activeDropdown === proxy.id ? null : proxy.id)}
                          style={{
                            padding: '6px 12px',
                            background: 'var(--bg-3)',
                            border: '1px solid var(--border)',
                            borderRadius: '6px',
                            cursor: 'pointer',
                            fontSize: '12px',
                            color: 'var(--text)',
                            fontWeight: '500'
                          }}
                        >
                          Назначить профилю ▾
                        </button>
                        {activeDropdown === proxy.id && (
                          <div style={{
                            position: 'absolute',
                            right: 0,
                            top: '32px',
                            zIndex: 1000,
                            background: 'var(--bg)',
                            border: '1px solid var(--border)',
                            borderRadius: '8px',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
                            minWidth: '200px',
                            maxHeight: '200px',
                            overflowY: 'auto',
                            padding: '4px'
                          }}>
                            {assignableProfiles.length === 0 ? (
                              <div style={{ padding: '8px 12px', color: 'var(--text-dim)', fontSize: '11px', textAlign: 'center' }}>
                                Нет профилей без прокси
                              </div>
                            ) : (
                              assignableProfiles.map(p => (
                                <button
                                  key={p.id}
                                  onClick={() => handleAssign(proxy.id, p.id)}
                                  style={{
                                    width: '100%',
                                    padding: '8px 12px',
                                    textAlign: 'left',
                                    background: 'none',
                                    border: 'none',
                                    borderRadius: '4px',
                                    cursor: 'pointer',
                                    fontSize: '12px',
                                    color: 'var(--text)',
                                    display: 'block'
                                  }}
                                  onMouseEnter={(e) => e.target.style.background = 'var(--bg-3)'}
                                  onMouseLeave={(e) => e.target.style.background = 'none'}
                                >
                                  {p.name}
                                </button>
                              ))
                            )}
                          </div>
                        )}
                      </div>

                      {/* Remove button */}
                      <button
                        onClick={() => handleRemove(proxy.id)}
                        style={{
                          padding: '6px 12px',
                          background: 'none',
                          border: 'none',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '12px',
                          color: 'var(--err)',
                          fontWeight: '500'
                        }}
                        onMouseEnter={(e) => e.target.style.background = 'var(--accent-soft)'}
                        onMouseLeave={(e) => e.target.style.background = 'none'}
                      >
                        Удалить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
