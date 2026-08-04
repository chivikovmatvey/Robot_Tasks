import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, type TaskSummary, type SessionSummary, type PublishedHistory, type AiStatus } from '../lib/api';
import { Icon } from '../components/Icon';

// Главная — рабочая сводка: задачи в работе, активные сессии, публикации,
// состояние сервисов. Быстрые переходы к делу вместо служебной статистики.
export function HomePage() {
  const nav = useNavigate();
  const [tasks, setTasks] = useState<TaskSummary[] | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [published, setPublished] = useState<PublishedHistory | null>(null);
  const [pubWeek, setPubWeek] = useState<PublishedHistory | null>(null);
  const [ai, setAi] = useState<AiStatus | null>(null);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    api.health().then(() => setBackendOk(true)).catch((e) => { setBackendOk(false); setErr(String(e?.message ?? e)); });
    api.tasks().then(setTasks).catch(() => setTasks([]));
    api.sessions().then(setSessions).catch(() => setSessions([]));
    api.published('day').then(setPublished).catch(() => {});
    api.published('week').then(setPubWeek).catch(() => {});
    api.aiStatus().then(setAi).catch(() => {});
  }, []);

  const statusColor = (s: string) => {
    const u = (s || '').toUpperCase();
    if (u.includes('PENDING')) return '#f59e0b';
    if (u.includes('PROCESS')) return '#7c6fff';
    if (u.includes('REVIEW')) return '#38bdf8';
    if (u.includes('ACCEPT')) return '#4ade80';
    return '#94a3b8';
  };

  const activeTasks = (tasks || []).filter((t) => {
    const u = t.status.toUpperCase();
    return u.includes('PENDING') || u.includes('PROCESS');
  });
  const pendingCount = activeTasks.filter((t) => t.status.toUpperCase().includes('PENDING')).length;
  const inWorkCount = activeTasks.filter((t) => t.status.toUpperCase().includes('PROCESS')).length;
  const todayCount = published?.groups?.length
    ? published.groups.reduce((n, g) => n + g.count, 0) : 0;
  const weekCount = pubWeek?.groups?.length
    ? pubWeek.groups.reduce((n, g) => n + g.count, 0) : 0;

  const Tile = ({ value, label, to, color }: { value: string | number; label: string; to: string; color?: string }) => (
    <Link to={to} className="card" style={{ textDecoration: 'none', color: 'inherit', flex: 1, minWidth: 140 }}>
      <div style={{ fontSize: 26, fontWeight: 700, color: color || 'var(--text)' }}>{value}</div>
      <div className="dim small">{label}</div>
    </Link>
  );

  return (
    <div className="page">
      <div className="page-header" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div>
          <h1>Главная</h1>
          <p className="muted">Что сейчас в работе</p>
        </div>
        <div style={{ flex: 1 }} />
        {backendOk === false && (
          <span className="small" style={{ color: '#f87171' }} title={err}>⛔ бэкенд не отвечает</span>
        )}
        {ai?.balance != null && (
          <span className="dim small" title="Баланс aitunnel (нейро-функции)">ИИ: {ai.balance.toFixed(0)} ₽</span>
        )}
        <button className="btn btn-primary" onClick={() => nav('/sessions/new')} style={{ fontSize: 13 }}>
          <Icon name="plus" size={13} /> Новая сессия
        </button>
      </div>

      {/* цифры дня */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
        <Tile value={pendingCount} label="задач ждут (PENDING)" to="/tasks" color="#f59e0b" />
        <Tile value={inWorkCount} label="задач в работе" to="/tasks" color="#7c6fff" />
        <Tile value={(sessions || []).length} label="активных сессий" to="/sessions" />
        <Tile value={todayCount} label="залито сегодня" to="/published" color="#4ade80" />
        <Tile value={weekCount} label="залито за неделю" to="/published" />
      </div>

      <div className="grid-2">
        {/* задачи */}
        <div className="card">
          <div className="card-label" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            Задачи <Link to="/tasks" className="dim small" style={{ marginLeft: 'auto' }}>все →</Link>
          </div>
          {tasks === null && <p className="dim small">Загружаю…</p>}
          {tasks !== null && activeTasks.length === 0 && <p className="dim small">Нет задач в очереди — отдыхай.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {activeTasks.slice(0, 7).map((t) => (
              <div key={t.uid} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}
                   onClick={() => nav('/tasks')} title={t.title}>
                <span style={{ width: 8, height: 8, borderRadius: 999, flexShrink: 0, background: statusColor(t.status) }} />
                {t.created && <span className="dim small mono" style={{ flexShrink: 0 }}>{t.created}</span>}
                <span style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {t.offer || t.title}
                </span>
                <span className="dim small" style={{ marginLeft: 'auto', flexShrink: 0 }}>{t.status}</span>
              </div>
            ))}
          </div>
        </div>

        {/* сессии */}
        <div className="card">
          <div className="card-label" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            Сессии адаптации <Link to="/sessions" className="dim small" style={{ marginLeft: 'auto' }}>все →</Link>
          </div>
          {sessions === null && <p className="dim small">Загружаю…</p>}
          {sessions !== null && sessions.length === 0 && <p className="dim small">Активных сессий нет.</p>}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {(sessions || []).slice(0, 7).map((s) => (
              <Link key={s.id} to={`/sessions/${s.id}`}
                    style={{ display: 'flex', alignItems: 'center', gap: 8, textDecoration: 'none', color: 'inherit' }}>
                <Icon name="play" size={12} />
                <span style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.offer || s.task_title || s.id}
                </span>
                <span className="dim small" style={{ marginLeft: 'auto', flexShrink: 0 }}>
                  {Object.keys(s.landers || {}).length} ленд(ов) · {s.status}
                </span>
              </Link>
            ))}
          </div>
        </div>
      </div>

      {/* публикации за сегодня */}
      {published && published.groups.length > 0 && (
        <div className="card" style={{ marginTop: 20 }}>
          <div className="card-label" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            Залито сегодня <Link to="/published" className="dim small" style={{ marginLeft: 'auto' }}>вся история →</Link>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {published.groups.map((g) => (
              <div key={g.key} style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 13 }}>
                <span className="dim small" style={{ flexShrink: 0 }}>{g.label}</span>
                <code className="mono" style={{ wordBreak: 'break-word' }}>{g.ids.join(', ')}</code>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
