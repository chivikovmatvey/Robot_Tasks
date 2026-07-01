import { useCallback, useEffect, useState } from 'react';
import { api, type PublishedHistory } from '../lib/api';

type Period = 'day' | 'week' | 'month' | 'all';
const PERIODS: { key: Period; label: string }[] = [
  { key: 'day', label: 'По дням' },
  { key: 'week', label: 'По неделям' },
  { key: 'month', label: 'По месяцам' },
  { key: 'all', label: 'Всего' },
];

export function PublishedPage() {
  const [period, setPeriod] = useState<Period>('day');
  const [data, setData] = useState<PublishedHistory | null>(null);
  const [addId, setAddId] = useState('');
  const [addDate, setAddDate] = useState('');
  const [error, setError] = useState('');
  const [copied, setCopied] = useState('');

  const load = useCallback(() => {
    api.published(period).then(setData).catch((e) => setError(e.message || 'Ошибка загрузки'));
  }, [period]);

  useEffect(() => { load(); }, [load]);

  const add = async () => {
    const id = parseInt(addId.trim(), 10);
    if (!id) { setError('Введи числовой id'); return; }
    setError('');
    try {
      await api.addPublished(id, addDate || undefined);
      setAddId(''); setAddDate('');
      load();
    } catch (e: any) {
      setError(e.message || 'Не удалось добавить');
    }
  };

  const del = async (id: number) => {
    if (!confirm(`Убрать id ${id} из истории?`)) return;
    try { await api.deletePublished(id); load(); } catch (e: any) { setError(e.message || ''); }
  };

  const copy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(key);
      setTimeout(() => setCopied(''), 1500);
    } catch {
      setError('Буфер обмена недоступен');
    }
  };

  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.5rem' }}>
        <h1 style={{ margin: 0 }}>Опубликованные ленды</h1>
        {data && <span className="dim small">всего {data.total}</span>}
      </div>

      {/* переключатель периода */}
      <div style={{ display: 'flex', gap: 6, marginBottom: '1rem' }}>
        {PERIODS.map((p) => (
          <button key={p.key} className={`btn ${period === p.key ? 'btn-primary' : ''}`}
                  style={{ fontSize: 12 }} onClick={() => setPeriod(p.key)}>
            {p.label}
          </button>
        ))}
      </div>

      {/* ручное добавление id */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <input className="form-input" style={{ width: 120 }} placeholder="id, напр. 20123"
               value={addId} onChange={(e) => setAddId(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') add(); }} />
        <input className="form-input" type="date" style={{ width: 160 }}
               value={addDate} onChange={(e) => setAddDate(e.target.value)} title="Дата (по умолчанию сегодня)" />
        <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={add}>+ Добавить id</button>
        <span className="dim small">встанет на своё место по дате и возрастанию</span>
      </div>

      {error && (
        <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, margin: '0.5rem 0', fontSize: 13 }}>{error}</div>
      )}

      {/* группы */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {data?.groups.length === 0 && <p className="dim">Пока ничего не опубликовано.</p>}
        {data?.groups.map((g) => (
          <div key={g.key} style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 10, padding: '0.7rem 0.9rem', background: 'var(--bg-elevated, #141414)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
              <span style={{ fontWeight: 600 }}>{g.label}</span>
              <span className="dim small">— {g.count} шт</span>
              <button className="btn" style={{ fontSize: 11, marginLeft: 'auto' }}
                      onClick={() => copy(g.copy, g.key)} title="Скопировать все id за период в одну строку без запятых">
                {copied === g.key ? '✓ скопировано' : '⧉ копировать'}
              </button>
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {g.ids.map((id) => (
                <span key={id} className="mono" title="Клик — убрать из истории"
                      onClick={() => del(id)}
                      style={{ fontSize: 13, padding: '2px 6px', borderRadius: 4, background: 'var(--bg, #0d0d0d)', border: '1px solid var(--border, #2a2a2a)', cursor: 'pointer' }}>
                  {id}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
