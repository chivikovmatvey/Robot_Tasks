import { Fragment, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, TaskSummary, TaskGroup, TaskDetail, CommentAttachment } from '../lib/api';
import { Icon } from '../components/Icon';

function statusColor(s: string) {
  const u = (s || '').toUpperCase();
  if (u.includes('PENDING')) return '#f59e0b';
  if (u.includes('PROCESS')) return '#7c6fff';
  if (u.includes('REVIEW')) return '#38bdf8';
  if (u.includes('ACCEPT')) return '#4ade80';
  return '#94a3b8';
}

export function TasksPage() {
  const nav = useNavigate();
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [groups, setGroups] = useState<TaskGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  // Ручной выбор задач для объединения в одну сессию (uid'ы отмеченных чекбоксов).
  const [selected, setSelected] = useState<string[]>([]);
  const load = async (refresh = false) => {
    setLoading(true);
    setError('');
    try {
      const [t, g] = await Promise.all([api.tasks(refresh), api.taskGroups(refresh)]);
      setTasks(t);
      setGroups(g);
    } catch (e: any) {
      setError(e.message || 'Ошибка загрузки задач');
    } finally {
      setLoading(false);
    }
  };

  // Перейти к созданию объединённой сессии из кластера задач на один оффер.
  const mergeGroup = (g: TaskGroup) => {
    const uids = g.tasks.map((t) => t.uid).join(',');
    nav(`/sessions/new?tasks=${encodeURIComponent(uids)}`);
  };

  const toggleSelect = (uid: string) => {
    setSelected((prev) => prev.includes(uid) ? prev.filter((u) => u !== uid) : [...prev, uid]);
  };

  // Объединить вручную отмеченные задачи (порядок — как в списке задач).
  const mergeSelected = () => {
    const uids = tasks.filter((t) => selected.includes(t.uid)).map((t) => t.uid).join(',');
    nav(`/sessions/new?tasks=${encodeURIComponent(uids)}`);
  };

  useEffect(() => { load(false); }, []);

  return (
    <div className="page">
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '1rem' }}>
        <h1 style={{ margin: 0 }}>Задачи</h1>
        <span className="dim small">пул Anyone + личные (mch)</span>
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={() => load(true)} disabled={loading}>
          {loading ? 'Обновляю…' : '↻ Обновить'}
        </button>
      </div>

      {error && (
        <div style={{ padding: '0.6rem 1rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, marginBottom: '1rem', fontSize: 13 }}>
          {error}
        </div>
      )}

      {groups.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginBottom: '1.25rem' }}>
          {groups.map((g) => (
            <div
              key={g.offer_key}
              style={{
                display: 'flex', alignItems: 'center', gap: '1rem',
                padding: '0.7rem 1rem', borderRadius: 8,
                border: '1px solid var(--accent, #7c6fff)',
                background: 'var(--accent-soft, rgba(124,111,255,0.12))',
              }}
            >
              <span style={{ fontSize: 20, display: 'inline-flex' }} title="Несколько задач на один оффер"><Icon name="shuffle" size={18} /></span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 14, fontWeight: 600 }}>
                  {g.count} задачи на один оффер: {g.offer}
                </div>
                <div className="dim small" style={{ marginTop: 2 }}>
                  Можно обработать за один раз — ленды объединятся в одну сессию, привязка к задачам сохранится.
                </div>
              </div>
              <button
                className="btn" style={{ fontSize: 12, whiteSpace: 'nowrap' }}
                title="Отметить задачи группы чекбоксами — лишние можно снять и объединить только нужные"
                onClick={() => setSelected(g.tasks.map((t) => t.uid))}
              >
                Выбрать чекбоксами
              </button>
              <button className="btn btn-primary" style={{ fontSize: 12, whiteSpace: 'nowrap' }} onClick={() => mergeGroup(g)}>
                Объединить {g.count} →
              </button>
            </div>
          ))}
        </div>
      )}

      {selected.length > 0 && (
        <div
          style={{
            position: 'sticky', top: 0, zIndex: 5,
            display: 'flex', alignItems: 'center', gap: '1rem',
            padding: '0.7rem 1rem', borderRadius: 8, marginBottom: '0.75rem',
            border: '1px solid var(--accent, #7c6fff)',
            background: 'var(--bg-elevated, #141414)',
          }}
        >
          <span style={{ fontSize: 20, display: 'inline-flex' }} title="Выбранные задачи"><Icon name="shuffle" size={18} /></span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              Выбрано задач: {selected.length}
            </div>
            <div className="dim small" style={{ marginTop: 2 }}>
              {selected.length < 2
                ? 'Отметь чекбоксами ещё хотя бы одну задачу, чтобы объединить их в одну сессию.'
                : 'Выбранные задачи объединятся в одну сессию, привязка лендов к задачам сохранится.'}
            </div>
          </div>
          <button className="btn" style={{ fontSize: 12, whiteSpace: 'nowrap' }} onClick={() => setSelected([])}>
            Сбросить
          </button>
          <button className="btn btn-primary" style={{ fontSize: 12, whiteSpace: 'nowrap' }} disabled={selected.length < 2} onClick={mergeSelected}>
            Объединить {selected.length} →
          </button>
        </div>
      )}

      {!loading && tasks.length === 0 && <p className="dim">Задач нет.</p>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {tasks.map((t) => (
          <TaskRow
            key={t.uid + t.title}
            t={t}
            selected={selected.includes(t.uid)}
            onToggleSelect={() => toggleSelect(t.uid)}
            onCreateSession={() => nav(`/sessions/new?task=${encodeURIComponent(t.uid)}`)}
            onChanged={() => load(true)}
          />
        ))}
      </div>
    </div>
  );
}

// Поля карточки для подробного вида.
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

function TaskRow({ t, selected, onToggleSelect, onCreateSession, onChanged }: {
  t: TaskSummary; selected: boolean; onToggleSelect: () => void; onCreateSession: () => void; onChanged: () => void;
}) {
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState('');     // url вложения, по которому идёт действие
  const [note, setNote] = useState('');
  const [err, setErr] = useState('');
  const [starting, setStarting] = useState(false);

  const startWorking = async () => {
    setStarting(true); setErr('');
    try {
      await api.taskChangeStatus(t.uid, 'IN_PROCESS');
      onChanged();
    } catch (e: any) {
      setErr(e.message || 'Не удалось взять задачу в работу');
      setStarting(false);
    }
  };

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !detail) {
      setLoading(true); setErr('');
      try {
        setDetail(await api.taskDetail(t.uid));
      } catch (e: any) {
        setErr(e.message || 'Не удалось загрузить задачу');
      } finally {
        setLoading(false);
      }
    }
  };

  // Архив из комментария → новая сессия из задачи + этот ленд → перейти в сессию.
  const adaptArchive = async (att: CommentAttachment) => {
    setBusy(att.url); setErr(''); setNote('');
    try {
      const s = await api.createSession({ task_uid: t.uid });
      await api.landerFromUrl(s.id, att.url, att.filename, undefined, t.uid);
      nav(`/sessions/${s.id}`);
    } catch (e: any) {
      setErr(e.message || 'Не удалось создать сессию из архива');
    } finally {
      setBusy('');
    }
  };

  // Ссылка на сайт из задачи → новая сессия + скачать лендинг скрапером.
  const adaptSite = async (att: CommentAttachment) => {
    setBusy(att.url); setErr(''); setNote('');
    try {
      const s = await api.createSession({ task_uid: t.uid });
      await api.landerFromSite(s.id, att.url, t.uid);
      nav(`/sessions/${s.id}`);
    } catch (e: any) {
      setErr(e.message || 'Не удалось скачать лендинг по ссылке');
    } finally {
      setBusy('');
    }
  };

  // Картинка из комментария → storage/assets (для замены фото через image_map).
  const importImage = async (att: CommentAttachment) => {
    setBusy(att.url); setErr(''); setNote('');
    try {
      const r = await api.assetFromUrl(att.url, att.filename);
      setNote(`Добавлено в assets: ${r.name} — выбери его в image_map при адаптации`);
    } catch (e: any) {
      setErr(e.message || 'Не удалось импортировать картинку');
    } finally {
      setBusy('');
    }
  };

  return (
    <div style={{ borderRadius: 8, border: '1px solid var(--border, #2a2a2a)', background: 'var(--bg-elevated, #141414)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', padding: '0.7rem 1rem' }}>
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggleSelect}
          title="Отметить для объединения в одну сессию"
          style={{ flexShrink: 0, width: 15, height: 15, cursor: 'pointer', accentColor: 'var(--accent, #7c6fff)' }}
        />
        <button
          onClick={toggle}
          title={open ? 'Свернуть' : 'Развернуть'}
          style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-muted)', fontSize: 13, width: 18, flexShrink: 0 }}
        >
          {open ? '▾' : '▸'}
        </button>
        <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, color: '#fff', background: statusColor(t.status), whiteSpace: 'nowrap' }}>
          {t.status}
        </span>
        <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={toggle}>
          <div style={{ fontSize: 14, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {t.offer || t.title}
          </div>
          <div className="dim small" style={{ marginTop: 2 }}>
            {t.category} · от {t.created_by} → {t.assigned_to}
            {t.deadline ? ` · ⏰ ${t.deadline}` : ''}
          </div>
        </div>
        <a href={t.url} target="_blank" rel="noopener" className="btn" style={{ fontSize: 12, textDecoration: 'none' }}>AdRobot ↗</a>
        {t.status.toUpperCase().includes('PENDING') && (
          <button className="btn" style={{ fontSize: 12, whiteSpace: 'nowrap' }} disabled={starting}
                  onClick={startWorking} title="Взять задачу в работу (Start working)">
            {starting ? '…' : '▶ В работу'}
          </button>
        )}
        <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={onCreateSession}>Создать сессию →</button>
      </div>

      {!open && err && (
        <div style={{ padding: '0 1rem 0.6rem', color: '#f87171', fontSize: 12 }}>{err}</div>
      )}

      {open && (
        <div style={{ borderTop: '1px solid var(--border, #2a2a2a)', padding: '1rem', background: 'var(--bg, #0d0e12)', display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
          {loading && <p className="dim small" style={{ margin: 0 }}>Загружаю карточку…</p>}
          {err && <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 8, fontSize: 12 }}>{err}</div>}
          {note && <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(74,222,128,0.12)', color: '#4ade80', borderRadius: 8, fontSize: 12 }}>{note}</div>}

          {detail && (() => {
            const fields = FIELD_ORDER.filter(([k]) => detail.fields[k] && detail.fields[k] !== '-');
            const allAttachments = detail.comments.flatMap((c) => c.attachments);
            const archives = allAttachments.filter((a) => a.kind === 'archive');
            const images = allAttachments.filter((a) => a.kind === 'image');
            const sites = allAttachments.filter((a) => a.kind === 'site');
            return (
              <>
                {/* Блок: детали задачи */}
                {fields.length > 0 && (
                  <Block title="Детали задачи">
                    <div style={{ display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '0.45rem 1rem', alignItems: 'baseline' }}>
                      {fields.map(([k, label]) => (
                        <Fragment key={k}>
                          <div className="dim small" style={{ whiteSpace: 'nowrap' }}>{label}</div>
                          <div style={{ fontSize: 13, whiteSpace: 'pre-line' }}>{detail.fields[k]}</div>
                        </Fragment>
                      ))}
                    </div>
                  </Block>
                )}

                {/* Блок: вложения (быстрые действия) */}
                {allAttachments.length > 0 && (
                  <Block title={`Вложения · ${allAttachments.length}`}>
                    {(archives.length > 0 || images.length > 0 || sites.length > 0) && (
                      <div className="dim small" style={{ marginBottom: '0.6rem' }}>
                        {archives.length > 0 && <span>Архивы можно адаптировать как ленд. </span>}
                        {sites.length > 0 && <span>Ссылки на сайты — скачать лендинг скрапером. </span>}
                        {images.length > 0 && <span>Картинки — отправить в замену фото.</span>}
                      </div>
                    )}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.6rem' }}>
                      {allAttachments.map((a, j) => (
                        <AttachmentChip
                          key={j}
                          att={a}
                          busy={busy === a.url}
                          onAdapt={() => adaptArchive(a)}
                          onImport={() => importImage(a)}
                          onAdaptSite={() => adaptSite(a)}
                        />
                      ))}
                    </div>
                  </Block>
                )}

                {/* Блок: комментарии */}
                <Block title={`Комментарии · ${detail.comments.length}`}>
                  {detail.comments.length === 0 && <p className="dim small" style={{ margin: 0 }}>Нет комментариев.</p>}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.7rem' }}>
                    {detail.comments.map((c, i) => (
                      <div key={i} style={{ display: 'flex', gap: '0.6rem' }}>
                        <div style={{ flexShrink: 0, width: 28, height: 28, borderRadius: '50%', background: 'var(--accent-soft, rgba(124,111,255,0.18))', color: 'var(--accent, #7c6fff)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600 }}>
                          {(c.author || '?').slice(0, 2).toUpperCase()}
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 12 }}>
                            <b>{c.author || '—'}</b>{c.time ? <span className="dim"> · {c.time}</span> : null}
                          </div>
                          {c.text && <div style={{ fontSize: 13, whiteSpace: 'pre-line', marginTop: 3 }}>{c.text}</div>}
                          {c.attachments.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginTop: 8 }}>
                              {c.attachments.map((a, j) => (
                                <AttachmentChip
                                  key={j}
                                  att={a}
                                  busy={busy === a.url}
                                  onAdapt={() => adaptArchive(a)}
                                  onImport={() => importImage(a)}
                                />
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </Block>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

// Блок-карточка для подробного вида задачи.
function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 10, background: 'var(--bg-elevated, #141414)', overflow: 'hidden' }}>
      <div className="dim" style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.6, padding: '0.5rem 0.8rem', borderBottom: '1px solid var(--border, #2a2a2a)' }}>
        {title}
      </div>
      <div style={{ padding: '0.7rem 0.8rem' }}>{children}</div>
    </div>
  );
}

function kindLabel(kind: string): string {
  if (kind === 'archive') return 'ZIP';
  if (kind === 'image') return 'IMG';
  if (kind === 'site') return 'SITE';
  return 'FILE';
}

function AttachmentChip({ att, busy, onAdapt, onImport, onAdaptSite }: {
  att: CommentAttachment; busy: boolean; onAdapt: () => void; onImport: () => void; onAdaptSite?: () => void;
}) {
  const isImg = att.kind === 'image';
  const isArchive = att.kind === 'archive';
  const isSite = att.kind === 'site';
  // Адаптировать можно robotmediaassets и облачные архивы (Google Drive/Яндекс Диск).
  const isCloud = /drive\.google\.com|docs\.google\.com|disk\.yandex|yadi\.sk/i.test(att.url);
  // Сайт-ссылку скачиваем скрапером (не «внешняя» в смысле «только открыть»).
  const isExternal = !isSite && !att.url.includes('robotmediaassets.com') && !isCloud;
  const tagColor = isArchive ? '#7c6fff' : isImg ? '#38bdf8' : isSite ? '#22c55e' : '#94a3b8';
  return (
    <div style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, background: 'var(--bg, #0d0e12)', display: 'flex', flexDirection: 'column', width: 188, overflow: 'hidden' }}>
      {isImg && !isExternal && (
        <a href={api.attachmentUrl(att.url)} target="_blank" rel="noopener" style={{ display: 'block', background: '#000' }}>
          <img src={api.attachmentUrl(att.url)} alt={att.filename} style={{ width: '100%', height: 100, objectFit: 'cover', display: 'block' }} />
        </a>
      )}
      <div style={{ padding: '0.5rem 0.6rem', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{ flexShrink: 0, fontSize: 9, fontWeight: 700, letterSpacing: 0.5, color: '#fff', background: tagColor, borderRadius: 4, padding: '1px 5px' }}>
            {kindLabel(att.kind)}
          </span>
          <span style={{ fontSize: 11, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={att.filename}>
            {att.filename}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
          {isSite ? (
            <a href={att.url} target="_blank" rel="noopener" className="btn" style={{ fontSize: 10, textDecoration: 'none' }}>Открыть ↗</a>
          ) : isExternal ? (
            <a href={att.url} target="_blank" rel="noopener" className="btn" style={{ fontSize: 10, textDecoration: 'none' }}>Открыть ↗</a>
          ) : (
            <a href={api.attachmentUrl(att.url, true)} className="btn" style={{ fontSize: 10, textDecoration: 'none' }}>Скачать</a>
          )}
          {isSite && onAdaptSite && (
            <button className="btn btn-primary" style={{ fontSize: 10 }} disabled={busy} onClick={onAdaptSite}>
              {busy ? '…' : 'Скачать лендинг'}
            </button>
          )}
          {isArchive && !isExternal && (
            <button className="btn btn-primary" style={{ fontSize: 10 }} disabled={busy} onClick={onAdapt}>
              {busy ? '…' : 'Адаптировать ленд'}
            </button>
          )}
          {isImg && !isExternal && (
            <button className="btn" style={{ fontSize: 10 }} disabled={busy} onClick={onImport}>
              {busy ? '…' : 'В замену фото'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
