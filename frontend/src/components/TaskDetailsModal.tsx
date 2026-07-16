import { Fragment, useEffect, useState } from 'react';
import { api, type TaskDetail } from '../lib/api';

interface TaskRefLite { uid: string; title: string; offer?: string }

const FIELD_ORDER: [string, string][] = [
  ['Offer', 'Оффер'],
  ['Category', 'Категория'],
  ['Target audience', 'Целевая аудитория'],
  ['Lander price', 'Цена ленда'],
  ['Reference lander', 'Референс'],
  ['Promotions', 'Промо'],
  ['Created by', 'Создал'],
  ['Assigned to', 'Назначено'],
  ['Comments', 'Комментарий'],
  ['Description', 'Описание'],
];

// Модалка с полной карточкой задачи. Для объединённых сессий (несколько задач)
// — вкладки сверху для переключения между задачами. Открывается из окна сессии.
export function TaskDetailsModal({ tasks, onClose }: { tasks: TaskRefLite[]; onClose: () => void }) {
  const [active, setActive] = useState(0);
  const [cache, setCache] = useState<Record<string, TaskDetail | null>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  const cur = tasks[active];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    if (!cur || cache[cur.uid] !== undefined) return;
    setLoading(true); setErr('');
    api.taskDetail(cur.uid)
      .then((d) => setCache((c) => ({ ...c, [cur.uid]: d })))
      .catch((e) => { setErr(e.message || 'Не удалось загрузить задачу'); setCache((c) => ({ ...c, [cur.uid]: null })); })
      .finally(() => setLoading(false));
  }, [cur, cache]);

  const detail = cur ? cache[cur.uid] : null;

  return (
    <div onClick={onClose}
         style={{ position: 'fixed', inset: 0, zIndex: 1100, background: 'rgba(0,0,0,0.8)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border, #2a2a2a)', borderRadius: 12, width: 720, maxWidth: '92vw', maxHeight: '88vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* шапка */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', padding: '0.8rem 1rem', borderBottom: '1px solid var(--border, #2a2a2a)' }}>
          <span style={{ fontWeight: 600 }}>Детали задачи{tasks.length > 1 ? ` (${tasks.length})` : ''}</span>
          <div style={{ flex: 1 }} />
          {detail?.url && <a href={detail.url} target="_blank" rel="noopener" className="btn" style={{ fontSize: 12, textDecoration: 'none' }}>AdRobot ↗</a>}
          <button className="btn" style={{ fontSize: 13 }} onClick={onClose} title="Закрыть (Esc)">✕</button>
        </div>

        {/* вкладки задач (для объединённых) */}
        {tasks.length > 1 && (
          <div style={{ display: 'flex', gap: 4, padding: '0.5rem 1rem', borderBottom: '1px solid var(--border, #2a2a2a)', overflowX: 'auto' }}>
            {tasks.map((t, i) => (
              <button key={t.uid} className={`btn ${i === active ? 'btn-primary' : ''}`}
                      style={{ fontSize: 11, whiteSpace: 'nowrap' }} onClick={() => setActive(i)}
                      title={t.title}>
                {t.offer || t.title || `Задача ${i + 1}`}
              </button>
            ))}
          </div>
        )}

        {/* содержимое: flex:1 + minHeight:0 обязательны — иначе flex-родитель с
            overflow:hidden обрезает блок и прокрутка не работает */}
        <div style={{ flex: 1, minHeight: 0, padding: '1rem', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
          {loading && <p className="dim small" style={{ margin: 0 }}>Загружаю карточку…</p>}
          {err && <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 8, fontSize: 12 }}>{err}</div>}

          {detail && (() => {
            const fields = FIELD_ORDER.filter(([k]) => detail.fields[k] && detail.fields[k] !== '-');
            const allAtt = detail.comments.flatMap((c) => c.attachments).concat(detail.attachments || []);
            // дедуп вложений по url
            const seen = new Set<string>();
            const atts = allAtt.filter((a) => (seen.has(a.url) ? false : (seen.add(a.url), true)));
            return (
              <>
                {detail.title && <div style={{ fontSize: 14, fontWeight: 600 }}>{detail.title}</div>}

                {fields.length > 0 && (
                  <Block title="Поля задачи">
                    <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '0.45rem 1rem', alignItems: 'baseline' }}>
                      {fields.map(([k, label]) => (
                        <Fragment key={k}>
                          <div className="dim small" style={{ whiteSpace: 'nowrap' }}>{label}</div>
                          <div style={{ fontSize: 13, whiteSpace: 'pre-line', wordBreak: 'break-word', minWidth: 0 }}>{detail.fields[k]}</div>
                        </Fragment>
                      ))}
                    </div>
                  </Block>
                )}

                {atts.length > 0 && (
                  <Block title={`Вложения · ${atts.length}`}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                      {atts.map((a, j) => (
                        <a key={j} href={api.attachmentUrl(a.url)} target="_blank" rel="noopener"
                           className="btn" style={{ fontSize: 11, textDecoration: 'none', display: 'inline-flex', gap: 6, alignItems: 'center' }}
                           title={a.filename}>
                          <span style={{ fontSize: 9, fontWeight: 700, color: '#fff', background: a.kind === 'archive' ? '#7c6fff' : a.kind === 'image' ? '#38bdf8' : a.kind === 'site' ? '#22c55e' : '#94a3b8', borderRadius: 3, padding: '1px 4px' }}>
                            {a.kind === 'archive' ? 'ZIP' : a.kind === 'image' ? 'IMG' : a.kind === 'site' ? 'SITE' : 'FILE'}
                          </span>
                          <span style={{ maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.filename}</span>
                        </a>
                      ))}
                    </div>
                  </Block>
                )}

                <Block title={`Комментарии · ${detail.comments.length}`}>
                  {detail.comments.length === 0 && <p className="dim small" style={{ margin: 0 }}>Нет комментариев.</p>}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
                    {detail.comments.map((c, i) => (
                      <div key={i} style={{ display: 'flex', gap: '0.6rem' }}>
                        <div style={{ flexShrink: 0, width: 28, height: 28, borderRadius: '50%', background: 'var(--accent-soft, rgba(124,111,255,0.18))', color: 'var(--accent, #7c6fff)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600 }}>
                          {(c.author || '?').slice(0, 2).toUpperCase()}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 12 }}><b>{c.author || '—'}</b>{c.time ? <span className="dim"> · {c.time}</span> : null}</div>
                          {c.text && <div style={{ fontSize: 13, whiteSpace: 'pre-line', marginTop: 3 }}>{c.text}</div>}
                        </div>
                      </div>
                    ))}
                  </div>
                </Block>
              </>
            );
          })()}
        </div>
      </div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 10, background: 'var(--bg, #0d0e12)', overflow: 'hidden' }}>
      <div className="dim" style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.6, padding: '0.5rem 0.8rem', borderBottom: '1px solid var(--border, #2a2a2a)' }}>{title}</div>
      <div style={{ padding: '0.7rem 0.8rem' }}>{children}</div>
    </div>
  );
}
