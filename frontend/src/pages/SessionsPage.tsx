import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, SessionSummary } from '../lib/api';

function fmtTime(ts: number) {
  try { return new Date(ts * 1000).toLocaleString(); } catch { return ''; }
}

// Осталось до удаления из архива (expires_at в секундах).
function fmtRemaining(expiresAt: number): string {
  const left = expiresAt - Date.now() / 1000;
  if (left <= 0) return 'удаляется…';
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  return h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
}

function statusColor(s: string) {
  if (s === 'ready' || s === 'adapted') return '#4ade80';
  if (s === 'error') return '#f87171';
  return '#f59e0b';
}

export function SessionsPage() {
  const nav = useNavigate();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [archivedView, setArchivedView] = useState(false);
  const [busy, setBusy] = useState('');

  const load = async (archived = archivedView) => {
    setLoading(true);
    try { setSessions(await api.sessions(archived)); } finally { setLoading(false); }
  };
  useEffect(() => { load(archivedView); /* eslint-disable-next-line */ }, [archivedView]);

  const archive = async (sid: string) => {
    setBusy(sid);
    try { await api.archiveSession(sid); await load(); } finally { setBusy(''); }
  };
  const unarchive = async (sid: string) => {
    setBusy(sid);
    try { await api.unarchiveSession(sid); await load(); } finally { setBusy(''); }
  };

  const tabStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 13, padding: '4px 12px', borderRadius: 8, cursor: 'pointer',
    border: '1px solid var(--border)',
    background: active ? 'var(--accent)' : 'transparent',
    color: active ? '#fff' : 'var(--text-muted)',
  });

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
        <h1 style={{ margin: 0 }}>Сессии адаптации</h1>
        <div style={{ display: 'flex', gap: 6, marginLeft: 8 }}>
          <button style={tabStyle(!archivedView)} onClick={() => setArchivedView(false)}>Активные</button>
          <button style={tabStyle(archivedView)} onClick={() => setArchivedView(true)}>Архив</button>
        </div>
        <div style={{ flex: 1 }} />
        {!archivedView && <Link className="btn btn-primary" to="/sessions/new" style={{ textDecoration: 'none' }}>+ Новая сессия</Link>}
        <button className="btn" onClick={() => load()} disabled={loading}>↻ Обновить</button>
      </div>

      {archivedView && (
        <p className="dim small" style={{ marginTop: 0 }}>
          Архивные сессии хранятся 1 день с момента перемещения, затем удаляются полностью.
        </p>
      )}

      {!loading && sessions.length === 0 && (
        archivedView
          ? <p className="dim">Архив пуст.</p>
          : <p className="dim">Сессий нет. Создай из <Link to="/tasks">задачи</Link>.</p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {sessions.map((s) => {
          const landers = Object.values(s.landers || {});
          return (
            <div
              key={s.id}
              style={{
                display: 'flex', alignItems: 'center', gap: '1rem',
                padding: '0.7rem 1rem', borderRadius: 8,
                border: '1px solid var(--border, #2a2a2a)', background: 'var(--bg-elevated, #141414)',
              }}
            >
              <span style={{
                fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999,
                color: '#fff', background: statusColor(s.status),
              }}>{s.status}</span>
              <div
                style={{ flex: 1, minWidth: 0, cursor: 'pointer' }}
                onClick={() => nav(`/sessions/${s.id}`)}
              >
                <div style={{ fontSize: 14, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
                  {s.offer || s.task_title}
                  {s.is_vsl && (
                    <span style={{
                      fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 999,
                      border: '1px solid var(--accent, #7c6fff)', color: 'var(--accent, #7c6fff)',
                    }}>VSL</span>
                  )}
                </div>
                <div className="dim small" style={{ marginTop: 2 }}>
                  {landers.length} ленд(ов): {landers.map(l => `${l.lander_id} (${l.status})`).join(', ')} · {fmtTime(s.created_at)}
                </div>
              </div>

              {archivedView && s.expires_at && (
                <span className="dim small" title="Останется в архиве до удаления" style={{ whiteSpace: 'nowrap' }}>
                  осталось {fmtRemaining(s.expires_at)}
                </span>
              )}

              {archivedView ? (
                <button className="btn" style={{ fontSize: 12 }} disabled={busy === s.id} onClick={() => unarchive(s.id)}>
                  {busy === s.id ? '…' : 'Восстановить'}
                </button>
              ) : (
                <button className="btn" style={{ fontSize: 12 }} disabled={busy === s.id} onClick={() => archive(s.id)}>
                  {busy === s.id ? '…' : 'В архив'}
                </button>
              )}
              <span className="dim" style={{ cursor: 'pointer' }} onClick={() => nav(`/sessions/${s.id}`)}>→</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
