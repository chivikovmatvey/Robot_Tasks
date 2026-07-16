import { useEffect, useRef, useState } from 'react';
import { api, vslCommentsHarvestStream, VslVideoStatus } from '../lib/api';
import { Icon } from './Icon';

/**
 * Редактор VSL-ленда: всё через config.php эталонного шаблона.
 *
 * Сверху — постоянные поля (продукт, заголовок, цены, фото product.png,
 * видео, комментарии с +/-), ниже — остальные блоки конфига по темам,
 * булевы значения — выпадающими списками true/false.
 */

// '590 MXN' → ['590', 'MXN']
function splitPrice(s: string): [string, string] {
  const m = (s || '').match(/[\d.,]+/);
  if (!m) return ['', (s || '').trim()];
  const num = m[0];
  const cur = ((s || '').slice(0, m.index) + (s || '').slice((m.index || 0) + num.length)).trim();
  return [num, cur];
}

function doubleNum(num: string): string {
  const n = parseFloat((num || '').replace(',', '.'));
  if (isNaN(n)) return '';
  const v = n * 2;
  return Number.isInteger(v) ? String(v) : String(v);
}

// Русские названия тем (блоков верхнего уровня config.php).
const THEME_LABELS: Record<string, string> = {
  settings: 'Настройки и метрики',
  notifications: 'Тексты уведомлений формы',
  header: 'Шапка',
  title: 'Заголовок (прочее)',
  video: 'Видео (прочее)',
  backfix: 'Backfix',
  callbackWidget: 'Колбек-виджет',
  orderForm: 'Форма заказа (прочее)',
  mediaLogos: 'Логотипы СМИ',
  fakeChat: 'Чат и комментарии (прочее)',
  footer: 'Футер',
};

// Пути, показанные в основных полях, — в общих темах их не дублируем.
const MAIN_PATHS = new Set([
  'pageTitle',
  'title.text', 'title.highlightPhrases',
  'video.src', 'video.poster',
  'orderForm.newPrice', 'orderForm.oldPrice', 'orderForm.discountText', 'orderForm.productImage',
  'fakeChat.preparedComments',
]);

const inputStyle: React.CSSProperties = { fontSize: 12 };

function FieldRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
      <span className="dim" style={{ fontSize: 11 }}>{label}</span>
      {children}
    </label>
  );
}

/** Универсальный редактор значения конфига (bool/число/строка/массив/объект). */
function ValueEditor({ value, path, onChange }: {
  value: any;
  path: string;
  onChange: (v: any) => void;
}) {
  if (typeof value === 'boolean') {
    return (
      <select className="form-input" style={inputStyle} value={value ? 'true' : 'false'}
              onChange={(e) => onChange(e.target.value === 'true')}>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (typeof value === 'number') {
    return (
      <input className="form-input" style={inputStyle} type="number" value={value}
             onChange={(e) => onChange(e.target.value === '' ? 0 : Number(e.target.value))} />
    );
  }
  if (typeof value === 'string') {
    if (value.length > 60) {
      return (
        <textarea className="form-input" style={{ ...inputStyle, resize: 'vertical' }} rows={2}
                  value={value} onChange={(e) => onChange(e.target.value)} />
      );
    }
    return (
      <input className="form-input" style={inputStyle} value={value}
             onChange={(e) => onChange(e.target.value)} />
    );
  }
  if (value === null) {
    return <input className="form-input" style={inputStyle} value="null" disabled />;
  }
  if (Array.isArray(value)) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {value.map((v, i) => (
          <div key={i} style={{ display: 'flex', gap: 4, alignItems: 'flex-start' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <ValueEditor value={v} path={`${path}[${i}]`}
                           onChange={(nv) => onChange(value.map((x, j) => (j === i ? nv : x)))} />
            </div>
            <button className="btn" title="Удалить элемент" style={{ fontSize: 11, padding: '2px 7px', flexShrink: 0 }}
                    onClick={() => onChange(value.filter((_, j) => j !== i))}>−</button>
          </div>
        ))}
        <button className="btn" style={{ fontSize: 11, alignSelf: 'flex-start' }}
                onClick={() => {
                  const proto = value.length ? value[value.length - 1] : '';
                  const blank = typeof proto === 'object' && proto !== null
                    ? JSON.parse(JSON.stringify(proto))
                    : (typeof proto === 'number' ? 0 : typeof proto === 'boolean' ? false : '');
                  onChange([...value, blank]);
                }}>+ элемент</button>
      </div>
    );
  }
  // объект — вложенные поля
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingLeft: 10, borderLeft: '2px solid var(--border, #2a2a2a)' }}>
      {Object.entries(value).map(([k, v]) => {
        const p = `${path}.${k}`;
        if (MAIN_PATHS.has(p)) return null;
        return (
          <FieldRow key={k} label={k}>
            <ValueEditor value={v} path={p}
                         onChange={(nv) => onChange({ ...value, [k]: nv })} />
          </FieldRow>
        );
      })}
    </div>
  );
}

interface Comment {
  index?: number;
  name: string;
  text: string;
  likes: number;
  avatar: string;
  appearAtPercent: number;
  replies: any[];
}

export function VslPanel({ sid, lid, hasOutput, onChanged }: {
  sid: string;
  lid: string;
  hasOutput: boolean;
  onChanged: () => void;
}) {
  const [cfg, setCfg] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [imgV, setImgV] = useState(0);       // cache-bust превью фото
  const [imgBusy, setImgBusy] = useState(false);
  const [groupPhotoNote, setGroupPhotoNote] = useState('');
  const [commentsOpen, setCommentsOpen] = useState(false);  // блок комментов свёрнут по умолчанию
  // библиотека комментариев по вертикалям
  const [commentsLib, setCommentsLib] = useState<{ code: string; vertical: string; sets: number; comments: number; langs: string[] }[]>([]);
  const [libVert, setLibVert] = useState('');
  const [libBusy, setLibBusy] = useState('');       // '', 'apply', 'translate', 'harvest'
  const [libNote, setLibNote] = useState('');
  const [harvestLog, setHarvestLog] = useState<string[]>([]);

  // видео
  const [video, setVideo] = useState<VslVideoStatus | null>(null);
  const [m3u8, setM3u8] = useState('');
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [starting, setStarting] = useState(false);
  const [nameDraft, setNameDraft] = useState('');
  const [renaming, setRenaming] = useState(false);
  const pollRef = useRef<any>(null);

  const loadVideo = () => api.vslVideoStatus(sid, lid)
    .then((v) => {
      setVideo(v);
      setNameDraft((cur) => cur || v.archive_name || v.suggested_name || '');
      return v;
    })
    .catch(() => null);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      api.vslConfig(sid, lid)
        .then((r) => {
          setCfg(r.config); setDirty(false);
          // Авто-подтяжка фото продукта группы с AdRobot, пока стоит шаблонное.
          const pi = (r.config.orderForm || {}).productImage || '';
          if (pi !== 'product.png') fetchGroupPhoto();
        })
        .catch((e) => setError(e.message || 'Не удалось прочитать config.php')),
      loadVideo(),
    ]).finally(() => setLoading(false));
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sid, lid]);

  // Подмешивает свежие ссылки видео с сервера в ЛОКАЛЬНЫЙ конфиг — не
  // затирая несохранённые правки других полей (loadConfig бы их стёр).
  const mergeVideoLinks = async () => {
    try {
      const r = await api.vslConfig(sid, lid);
      setCfg((c) => (c ? {
        ...c,
        video: { ...(c.video || {}), src: r.config.video?.src || '', poster: r.config.video?.poster || '' },
      } : c));
    } catch { /* не критично */ }
  };

  // Пока идёт конвертация — poll статуса; по завершении подтянуть ссылки
  // (они обновились в config.php на бэке).
  useEffect(() => {
    if (video?.state === 'running' && !pollRef.current) {
      pollRef.current = setInterval(async () => {
        const v = await loadVideo();
        if (v && v.state !== 'running') {
          clearInterval(pollRef.current); pollRef.current = null;
          if (v.state === 'done') { await mergeVideoLinks(); onChanged(); }
        }
      }, 2500);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [video?.state]);

  const patch = (updater: (c: Record<string, any>) => Record<string, any>) => {
    setCfg((c) => (c ? updater(c) : c));
    setDirty(true);
  };
  const setPath = (path: string[], v: any) => patch((c) => {
    const next = { ...c };
    let cur: any = next;
    for (let i = 0; i < path.length - 1; i++) {
      cur[path[i]] = { ...(cur[path[i]] || {}) };
      cur = cur[path[i]];
    }
    cur[path[path.length - 1]] = v;
    return next;
  });

  const save = async () => {
    if (!cfg) return;
    setSaving(true); setError('');
    try {
      await api.vslConfigSave(sid, lid, cfg);
      setDirty(false);
      setSavedAt(Date.now());
      onChanged();
    } catch (e: any) {
      setError(e.message || 'Не удалось сохранить конфиг');
    } finally { setSaving(false); }
  };

  // Локально помечаем product.png (сервер уже записал) — БЕЗ loadConfig,
  // чтобы не стереть несохранённые правки остальных полей.
  const markProductPng = () => {
    setCfg((c) => (c ? { ...c, orderForm: { ...(c.orderForm || {}), productImage: 'product.png' } } : c));
    setImgV((v) => v + 1);
    onChanged();
  };

  const uploadProductImage = async (f: File) => {
    setImgBusy(true); setError(''); setGroupPhotoNote('');
    try {
      await api.vslProductImage(sid, lid, f);
      markProductPng();
    } catch (e: any) {
      setError(e.message || 'Не удалось загрузить фото');
    } finally { setImgBusy(false); }
  };

  const fetchGroupPhoto = async () => {
    setImgBusy(true);
    try {
      const r = await api.vslProductImageFromGroup(sid, lid);
      if (r.found) {
        markProductPng();
        setGroupPhotoNote(`Фото группы «${r.offer}» установлено как product.png`);
      } else {
        setGroupPhotoNote(`Фото для группы «${r.offer}» на странице оффера не найдено`);
      }
    } catch (e: any) {
      setGroupPhotoNote(e.message || 'Не удалось подтянуть фото группы');
    } finally { setImgBusy(false); }
  };

  const startVideo = async () => {
    if (!videoFile && !m3u8.trim()) { setError('Выбери mp4-файл или вставь ссылку m3u8'); return; }
    setStarting(true); setError('');
    try {
      await api.vslVideoStart(sid, lid, { file: videoFile || undefined, m3u8Url: m3u8.trim() || undefined });
      setVideo((v) => ({ ...(v || { steps: [], archive_name: '', archive_ready: false }), state: 'running', steps: [] } as VslVideoStatus));
    } catch (e: any) {
      setError(e.message || 'Не удалось запустить подготовку видео');
    } finally { setStarting(false); }
  };

  // ── библиотека VSL-комментариев по вертикалям ──
  useEffect(() => {
    api.vslCommentsLibrary().then((r) => setCommentsLib(r.library)).catch(() => {});
  }, []);

  const reloadCfgFromServer = async () => {
    const r = await api.vslConfig(sid, lid);
    setCfg(r.config); setDirty(false);
  };

  const applyLibComments = async () => {
    if (!libVert || !cfg) return;
    setLibBusy('apply'); setLibNote('');
    try {
      if (dirty) await api.vslConfigSave(sid, lid, cfg); // не терять правки полей
      const r = await api.vslCommentsApply(sid, lid, libVert);
      await reloadCfgFromServer();
      setCommentsOpen(true);
      setLibNote(`Вставлено ${r.applied} комментариев (наборов: ${r.sets}, аватарок на ленде: ${r.avatars}`
        + (r.skipped ? `, ${r.skipped} не влезло — не хватило аватарок)` : ')'));
      onChanged();
    } catch (e: any) {
      setLibNote(e.message || 'Ошибка подстановки');
    } finally { setLibBusy(''); }
  };

  const translateLibComments = async () => {
    if (!cfg) return;
    setLibBusy('translate'); setLibNote('перевожу имя и текст (deepseek)…');
    try {
      if (dirty) await api.vslConfigSave(sid, lid, cfg);
      const r = await api.vslCommentsTranslate(sid, lid);
      await reloadCfgFromServer();
      setCommentsOpen(true);
      setLibNote(`Переведено на ${r.lang}: ${r.changed} из ${r.blocks} блоков`);
      onChanged();
    } catch (e: any) {
      setLibNote(e.message || 'Ошибка перевода');
    } finally { setLibBusy(''); }
  };

  const harvestLibComments = async () => {
    if (!confirm('Собрать комментарии из VSL-офферов Keitaro? Это скачивание лендов, займёт несколько минут.')) return;
    setLibBusy('harvest'); setHarvestLog([]); setLibNote('');
    try {
      await vslCommentsHarvestStream((ev) => {
        if (ev.type === 'step') setHarvestLog((l) => [...l.slice(-40), ev.message || '']);
        else if (ev.type === 'done') { setCommentsLib(ev.library || []); setLibNote('Сбор завершён'); }
        else if (ev.type === 'error') setLibNote(ev.error || 'Ошибка сбора');
      });
    } catch (e: any) {
      setLibNote(e.message || 'Ошибка сбора');
    } finally { setLibBusy(''); setHarvestLog([]); }
  };

  const renameArchive = async () => {
    const name = nameDraft.trim();
    if (!name || name === video?.archive_name) return;
    setRenaming(true); setError('');
    try {
      const r = await api.vslVideoRename(sid, lid, name);
      setNameDraft(r.archive_name);
      // Ссылки берём из ответа — локально, без loadConfig (не трогаем правки).
      setCfg((c) => (c ? { ...c, video: { ...(c.video || {}), src: r.src, poster: r.poster } } : c));
      await loadVideo();
      onChanged();
    } catch (e: any) {
      setError(e.message || 'Не удалось переименовать архив');
    } finally { setRenaming(false); }
  };

  if (loading) return <p className="dim small">Читаю config.php…</p>;
  if (!cfg) return <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, fontSize: 12 }}>{error || 'Конфиг не прочитан'}</div>;

  const title = cfg.title || {};
  const orderForm = cfg.orderForm || {};
  const videoCfg = cfg.video || {};
  const fakeChat = cfg.fakeChat || {};
  const comments: Comment[] = fakeChat.preparedComments || [];
  const highlights: string[] = title.highlightPhrases || [];
  const productImage = orderForm.productImage || '';

  const setNewPrice = (v: string) => {
    const [num, cur] = splitPrice(v);
    const dbl = doubleNum(num);
    patch((c) => ({
      ...c,
      orderForm: {
        ...(c.orderForm || {}),
        newPrice: v,
        oldPrice: dbl ? `${dbl} ${cur}`.trim() : (c.orderForm?.oldPrice || ''),
      },
    }));
  };

  const setComment = (i: number, k: keyof Comment, v: any) => {
    const next = comments.map((cm, j) => (j === i ? { ...cm, [k]: v } : cm));
    setPath(['fakeChat', 'preparedComments'], next);
  };
  const addComment = () => {
    const last = comments[comments.length - 1];
    const next: Comment = {
      index: comments.length,
      name: '',
      text: '',
      likes: 10,
      avatar: `avatars/${comments.length + 1}.jpeg`,
      appearAtPercent: Math.min(95, (last?.appearAtPercent ?? 0) + 10),
      replies: [],
    };
    setPath(['fakeChat', 'preparedComments'], [...comments, next]);
  };
  const removeComment = (i: number) => {
    const next = comments.filter((_, j) => j !== i).map((cm, j) => ({ ...cm, index: j }));
    setPath(['fakeChat', 'preparedComments'], next);
  };

  const themeKeys = Object.keys(cfg).filter((k) => k !== 'pageTitle');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}><Icon name="play" size={13} /> VSL — конфиг ленда</span>
        <span className="dim small">все изменения через config.php</span>
      </div>

      {error && (
        <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, fontSize: 12 }}>{error}</div>
      )}

      {/* ── основные поля ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
        <FieldRow label="Название продукта (pageTitle)">
          <input className="form-input" style={inputStyle} value={cfg.pageTitle || ''}
                 onChange={(e) => setPath(['pageTitle'], e.target.value)} />
        </FieldRow>
        <FieldRow label="Скидка (discountText)">
          <input className="form-input" style={inputStyle} value={orderForm.discountText || ''}
                 onChange={(e) => setPath(['orderForm', 'discountText'], e.target.value)} />
        </FieldRow>
        <FieldRow label="Новая цена (newPrice)">
          <input className="form-input" style={inputStyle} value={orderForm.newPrice || ''}
                 placeholder="590 MXN" onChange={(e) => setNewPrice(e.target.value)} />
        </FieldRow>
        <FieldRow label="Старая цена (×2 авто)">
          <input className="form-input" style={inputStyle} value={orderForm.oldPrice || ''}
                 onChange={(e) => setPath(['orderForm', 'oldPrice'], e.target.value)} />
        </FieldRow>
      </div>

      <FieldRow label="Заголовок (title.text)">
        <textarea className="form-input" style={{ ...inputStyle, resize: 'vertical' }} rows={3}
                  value={title.text || ''} onChange={(e) => setPath(['title', 'text'], e.target.value)} />
      </FieldRow>
      <FieldRow label="Выделенные красным фразы (highlightPhrases)">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {highlights.map((h, i) => (
            <div key={i} style={{ display: 'flex', gap: 4 }}>
              <input className="form-input" style={{ ...inputStyle, flex: 1 }} value={h}
                     onChange={(e) => setPath(['title', 'highlightPhrases'], highlights.map((x, j) => (j === i ? e.target.value : x)))} />
              <button className="btn" style={{ fontSize: 11, padding: '2px 7px' }}
                      onClick={() => setPath(['title', 'highlightPhrases'], highlights.filter((_, j) => j !== i))}>−</button>
            </div>
          ))}
          <button className="btn" style={{ fontSize: 11, alignSelf: 'flex-start' }}
                  onClick={() => setPath(['title', 'highlightPhrases'], [...highlights, ''])}>+ фраза</button>
        </div>
      </FieldRow>

      {/* ── фото продукта ── */}
      <div style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, padding: '0.7rem', display: 'flex', gap: 12, alignItems: 'center' }}>
        {productImage ? (
          <img
            src={`${api.landerFileUrl(sid, lid, productImage, hasOutput)}&v=${imgV}`}
            alt="product"
            style={{ width: 72, height: 72, objectFit: 'contain', borderRadius: 6, background: 'var(--bg-elevated, #141414)', flexShrink: 0 }}
            onError={(e) => { (e.target as HTMLImageElement).style.opacity = '0.2'; }}
          />
        ) : (
          <div style={{ width: 72, height: 72, borderRadius: 6, background: 'var(--bg-elevated, #141414)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
            <Icon name="image" size={22} />
          </div>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 600 }}>Фото продукта</div>
          <div className="dim small" style={{ marginTop: 2 }}>
            Текущее: <code>{productImage || '—'}</code>. Загруженное сохранится как <code>product.png</code> и пропишется в конфиг.
          </div>
          {groupPhotoNote && (
            <div className="small" style={{ marginTop: 3, color: groupPhotoNote.includes('установлено') ? '#4ade80' : 'var(--warning, #e8a857)' }}>
              {groupPhotoNote}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, flexShrink: 0 }}>
          <label className="btn" style={{ fontSize: 12, cursor: 'pointer', textAlign: 'center' }}>
            {imgBusy ? 'Загружаю…' : <><Icon name="image" size={13} /> Заменить фото</>}
            <input type="file" accept="image/*" style={{ display: 'none' }} disabled={imgBusy}
                   onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadProductImage(f); e.currentTarget.value = ''; }} />
          </label>
          <button className="btn" style={{ fontSize: 12 }} disabled={imgBusy}
                  title="Найти фото продукта группы на странице оффера AdRobot и поставить как product.png"
                  onClick={fetchGroupPhoto}>
            <Icon name="search" size={12} /> Фото группы
          </button>
        </div>
      </div>

      {/* ── видео ── */}
      <div style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, padding: '0.7rem', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ fontSize: 12, fontWeight: 600 }}><Icon name="play" size={12} /> Видео</div>
        <FieldRow label="Ссылка на видео (video.src)">
          <input className="form-input" style={{ ...inputStyle, fontFamily: 'monospace' }} value={videoCfg.src || ''}
                 onChange={(e) => setPath(['video', 'src'], e.target.value)} />
        </FieldRow>

        {video?.state !== 'running' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div className="dim small">Подготовка нового видео: mp4-файл или ссылка m3u8 → конвертация (HLS 360/480/720p + постер) → архив для сервера.</div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <label className="btn" style={{ fontSize: 12, cursor: 'pointer' }}>
                <Icon name="download" size={12} /> {videoFile ? videoFile.name : 'Выбрать mp4'}
                <input type="file" accept="video/mp4" style={{ display: 'none' }}
                       onChange={(e) => setVideoFile(e.target.files?.[0] || null)} />
              </label>
              <span className="dim small">или</span>
              <input className="form-input" style={{ ...inputStyle, flex: 1, minWidth: 180, fontFamily: 'monospace' }}
                     value={m3u8} onChange={(e) => setM3u8(e.target.value)}
                     placeholder="https://…/master.m3u8" />
              <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={starting} onClick={startVideo}>
                {starting ? 'Запускаю…' : 'Подготовить видео'}
              </button>
            </div>
          </div>
        )}

        {video?.state === 'running' && (
          <div style={{ padding: '0.5rem 0.7rem', background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>Конвертирую видео… (может занять несколько минут)</div>
            {video.steps.map((s, i) => <div key={i} className="dim">{i + 1}. {s}</div>)}
          </div>
        )}
        {video?.state === 'error' && (
          <div style={{ padding: '0.5rem 0.7rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, fontSize: 12, whiteSpace: 'pre-wrap' }}>
            Видео: {video.error}
          </div>
        )}

        {video?.archive_ready && video.state !== 'running' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '0.5rem 0.7rem', background: 'rgba(74,222,128,0.08)', borderRadius: 6 }}>
            <div style={{ fontSize: 12 }}>
              <Icon name="check" size={12} /> Архив видео готов
              {video.archive_size ? <span className="dim"> · {(video.archive_size / 1024 / 1024).toFixed(1)} MB</span> : null}
              <span className="dim"> · внутри — содержимое папки видео (promo/ + постер), как для сервера</span>
            </div>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="dim small" style={{ flexShrink: 0 }}>Имя архива (= папки на сервере):</span>
              <input className="form-input" style={{ ...inputStyle, fontFamily: 'monospace', flex: 1, minWidth: 160 }}
                     value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} />
              <button className="btn" style={{ fontSize: 12 }} disabled={renaming || !nameDraft.trim() || nameDraft.trim() === video.archive_name}
                      onClick={renameArchive}>
                {renaming ? '…' : 'Переименовать'}
              </button>
              <a className="btn btn-primary" style={{ fontSize: 12, textDecoration: 'none' }}
                 href={api.vslVideoDownloadUrl(sid, lid)}>
                <Icon name="download" size={13} /> Скачать архив
              </a>
            </div>
            <div className="dim small">При переименовании ссылки src/poster в конфиге обновятся автоматически (меняется только имя папки).</div>
          </div>
        )}
      </div>

      {/* ── комментарии (свёрнуты по умолчанию; каждый — компактный details) ── */}
      <div style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, padding: '0.55rem 0.7rem', display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button onClick={() => setCommentsOpen((v) => !v)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text)', fontSize: 12, fontWeight: 600, padding: 0, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ display: 'inline-block', transition: 'transform .12s', transform: commentsOpen ? 'rotate(90deg)' : 'none' }}>▸</span>
            Комментарии ({comments.length})
          </button>
          <button className="btn" style={{ fontSize: 11, marginLeft: 'auto' }}
                  onClick={() => { setCommentsOpen(true); addComment(); }}>+ Комментарий</button>
        </div>

        {/* ── библиотека комментариев по вертикалям ── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <select className="form-input" style={{ fontSize: 11, width: 250, padding: '2px 6px' }}
                  value={libVert} onChange={(e) => setLibVert(e.target.value)} disabled={!!libBusy}>
            <option value="">— вертикаль из библиотеки —</option>
            {commentsLib.map((v) => (
              <option key={v.code} value={v.code}>
                {v.code} · {v.vertical} · {v.comments} комм. ({v.langs.join(', ')})
              </option>
            ))}
          </select>
          <button className="btn" disabled={!libVert || !!libBusy} onClick={applyLibComments}
                  title="Вставить в конфиг все сохранённые комментарии вертикали"
                  style={{ fontSize: 11 }}>
            {libBusy === 'apply' ? 'Вставляю…' : 'Подставить'}
          </button>
          <button className="btn" disabled={!comments.length || !!libBusy} onClick={translateLibComments}
                  title="Перевести имя и текст комментариев на язык гео ленда (deepseek)"
                  style={{ fontSize: 11 }}>
            {libBusy === 'translate' ? 'Перевожу…' : 'Перевести (по гео)'}
          </button>
          <button className="btn" disabled={!!libBusy} onClick={harvestLibComments}
                  title="Скачать VSL-ленды из Keitaro и пополнить библиотеку комментариев"
                  style={{ fontSize: 11, marginLeft: 'auto' }}>
            {libBusy === 'harvest' ? 'Собираю…' : 'Собрать из Keitaro'}
          </button>
        </div>
        {libNote && <div className="dim small">{libNote}</div>}
        {libBusy === 'harvest' && harvestLog.length > 0 && (
          <div className="dim" style={{ fontSize: 10, maxHeight: 110, overflowY: 'auto', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, padding: '4px 6px' }}>
            {harvestLog.map((l, i) => <div key={i}>{l}</div>)}
          </div>
        )}
        {commentsOpen && comments.map((cm, i) => (
          <details key={i} style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, padding: '0.3rem 0.5rem' }}>
            <summary style={{ cursor: 'pointer', fontSize: 11, display: 'flex', alignItems: 'center', gap: 6, listStyle: 'none' }}>
              <span className="dim" style={{ fontWeight: 600, flexShrink: 0 }}>{i + 1}.</span>
              <span style={{ fontWeight: 600, flexShrink: 0 }}>{cm.name || 'без имени'}</span>
              <span className="dim" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>
                {cm.text || '…'}
              </span>
              <span className="dim" style={{ flexShrink: 0 }}>{cm.appearAtPercent}%</span>
              <button className="btn" title="Удалить комментарий" style={{ fontSize: 11, padding: '0 6px', flexShrink: 0 }}
                      onClick={(e) => { e.preventDefault(); removeComment(i); }}>−</button>
            </summary>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 5, marginTop: 6 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: 5 }}>
                <FieldRow label="Имя">
                  <input className="form-input" style={inputStyle} value={cm.name}
                         onChange={(e) => setComment(i, 'name', e.target.value)} />
                </FieldRow>
                <FieldRow label="Лайки">
                  <input className="form-input" style={inputStyle} type="number" value={cm.likes}
                         onChange={(e) => setComment(i, 'likes', Number(e.target.value) || 0)} />
                </FieldRow>
                <FieldRow label="% видео">
                  <input className="form-input" style={inputStyle} type="number" value={cm.appearAtPercent}
                         onChange={(e) => setComment(i, 'appearAtPercent', Number(e.target.value) || 0)} />
                </FieldRow>
              </div>
              <FieldRow label="Текст">
                <textarea className="form-input" style={{ ...inputStyle, resize: 'vertical' }} rows={2}
                          value={cm.text} onChange={(e) => setComment(i, 'text', e.target.value)} />
              </FieldRow>
              <FieldRow label="Аватар (путь в архиве)">
                <input className="form-input" style={{ ...inputStyle, fontFamily: 'monospace' }} value={cm.avatar}
                       onChange={(e) => setComment(i, 'avatar', e.target.value)} />
              </FieldRow>
            </div>
          </details>
        ))}
      </div>

      {/* ── остальные блоки конфига по темам ── */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 2 }}>Остальные настройки конфига</div>
        {themeKeys.map((key) => {
          const v = cfg[key];
          if (typeof v !== 'object' || v === null || Array.isArray(v)) {
            return (
              <div key={key} style={{ padding: '0.45rem 0.6rem', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6 }}>
                <FieldRow label={THEME_LABELS[key] || key}>
                  <ValueEditor value={v} path={key} onChange={(nv) => setPath([key], nv)} />
                </FieldRow>
              </div>
            );
          }
          const entries = Object.entries(v).filter(([k]) => !MAIN_PATHS.has(`${key}.${k}`));
          if (!entries.length) return null;
          return (
            <details key={key} style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, padding: '0.45rem 0.6rem' }}>
              <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>
                {THEME_LABELS[key] || key}
                {'enabled' in v && (
                  <span className="dim small" style={{ marginLeft: 6 }}>
                    {v.enabled ? '· включено' : '· выключено'}
                  </span>
                )}
              </summary>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
                {entries.map(([k, val]) => (
                  <FieldRow key={k} label={k}>
                    <ValueEditor value={val} path={`${key}.${k}`}
                                 onChange={(nv) => setPath([key, k], nv)} />
                  </FieldRow>
                ))}
              </div>
            </details>
          );
        })}
      </div>

      {/* ── сохранение ── */}
      <div style={{ position: 'sticky', bottom: 0, background: 'var(--bg, #0d0e12)', padding: '0.5rem 0', display: 'flex', gap: 8, alignItems: 'center', borderTop: '1px solid var(--border, #2a2a2a)' }}>
        <button className="btn btn-primary" onClick={save} disabled={saving || !dirty} style={{ fontSize: 13 }}>
          {saving ? 'Сохраняю…' : <><Icon name="save" size={13} /> Сохранить конфиг</>}
        </button>
        <a className="btn" style={{ fontSize: 13, textDecoration: 'none' }}
           title="Скачать архив ленда с текущим конфигом и адаптацией"
           href={api.vslDownloadUrl(sid, lid)}>
          <Icon name="download" size={13} /> Скачать ленд
        </a>
        {dirty && <span className="dim small">есть несохранённые изменения</span>}
        {!dirty && savedAt > 0 && <span className="small" style={{ color: '#4ade80' }}>✓ сохранено</span>}
      </div>
    </div>
  );
}
