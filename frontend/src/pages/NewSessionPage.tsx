import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams, Link } from 'react-router-dom';
import { api } from '../lib/api';
import { OfferNamesHint } from '../components/OfferNamesHint';
import { Icon } from '../components/Icon';

// Клиентский разбор ID лендов из текста задачи (зеркало бэкенда).
// id ленда — это 4-5 цифр (отсев мусора: цены «299», годы, длинные числа).
// Зеркало бэкенда extract_lander_ids в services/session.py.
function extractIds(refLander: string, description: string): string[] {
  const ids: string[] = [];
  const add = (v: string) => { if (v && !ids.includes(v)) ids.push(v); };
  for (const m of (refLander || '').matchAll(/\(ID:\s*(\d+)\)/g)) add(m[1]);
  for (const line of (refLander || '').split('\n')) {
    const m = line.match(/^\s*(\d{4,5})\b/);
    if (m) add(m[1]);
  }
  for (const m of (description || '').matchAll(/(?<!\d)(\d{4,5})(?!\d)/g)) add(m[1]);
  return ids;
}

function parseIdsInput(text: string): string[] {
  return Array.from(new Set(
    (text || '').split(/[\s,;]+/).map((s) => s.trim()).filter((s) => /^\d{4,5}$/.test(s))
  ));
}

// Ссылки на лендинги (http/https) — будут скачаны скрапером и добавлены в сессию.
function parseUrlsInput(text: string): string[] {
  return Array.from(new Set(
    (text || '').split(/[\s,;\n]+/).map((s) => s.trim()).filter((s) => /^https?:\/\/\S+$/i.test(s))
  ));
}

interface SourceTask { uid: string; title: string; offer: string; ids: string[] }

export function NewSessionPage() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const taskUid = sp.get('task') || '';
  // Несколько задач на один оффер (объединённая сессия): ?tasks=uid1,uid2,...
  const taskUids = (sp.get('tasks') || '').split(',').map((s) => s.trim()).filter(Boolean);
  const isMulti = taskUids.length > 1;

  const [offer, setOffer] = useState('');
  const [idsText, setIdsText] = useState('');
  const [urlsText, setUrlsText] = useState('');
  const [sourceTasks, setSourceTasks] = useState<SourceTask[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');

  // Префилл из одной задачи.
  useEffect(() => {
    if (isMulti || !taskUid) return;
    api.taskDetail(taskUid)
      .then((d) => {
        setOffer(d.fields['Offer'] || '');
        const ids = extractIds(d.fields['Reference lander'] || '', d.fields['Description'] || '');
        setIdsText(ids.join(', '));
      })
      .catch((e) => setError('Не удалось подтянуть задачу: ' + e.message));
  }, [taskUid, isMulti]);

  // Префилл из нескольких задач: тянем каждую, собираем ленды и привязку.
  useEffect(() => {
    if (!isMulti) return;
    Promise.all(taskUids.map((u) => api.taskDetail(u)))
      .then((details) => {
        setOffer(details[0]?.fields['Offer'] || '');
        const st: SourceTask[] = details.map((d) => ({
          uid: d.uid,
          title: d.title,
          offer: d.fields['Offer'] || '',
          ids: extractIds(d.fields['Reference lander'] || '', d.fields['Description'] || ''),
        }));
        setSourceTasks(st);
      })
      .catch((e) => setError('Не удалось подтянуть задачи: ' + e.message));
  }, [sp]);

  const create = async () => {
    if (isMulti) {
      setBusy(true); setError(''); setStatus('Создаю объединённую сессию…');
      try {
        const s = await api.createSession({ task_uids: taskUids, offer: offer || undefined });
        nav(`/sessions/${s.id}`);
      } catch (e: any) {
        setError(e.message || 'Ошибка создания сессии');
        setStatus('');
      } finally {
        setBusy(false);
      }
      return;
    }

    const ids = parseIdsInput(idsText);
    const urls = parseUrlsInput(urlsText);
    if (ids.length === 0 && files.length === 0 && urls.length === 0) {
      setError('Укажи ID ленда, ссылку на лендинг или загрузи архив');
      return;
    }
    setBusy(true); setError(''); setStatus('Создаю сессию…');
    try {
      const s = await api.createSession({
        task_uid: taskUid || undefined,
        lander_ids: ids,
        offer: offer || undefined,
      });
      for (let i = 0; i < files.length; i++) {
        setStatus(`Загружаю архив ${i + 1}/${files.length}…`);
        await api.uploadLander(s.id, files[i]);
      }
      for (let i = 0; i < urls.length; i++) {
        setStatus(`Скачиваю лендинг ${i + 1}/${urls.length}…`);
        await api.landerFromSite(s.id, urls[i], taskUid || undefined);
      }
      nav(`/sessions/${s.id}`);
    } catch (e: any) {
      setError(e.message || 'Ошибка создания сессии');
      setStatus('');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page" style={{ maxWidth: 720 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
        <Link to="/sessions" className="dim" style={{ textDecoration: 'none' }}>←</Link>
        <h1 style={{ margin: 0 }}>{isMulti ? 'Объединённая сессия' : 'Новая сессия'}</h1>
      </div>

      {taskUid && !isMulti && <p className="dim small">Из задачи: <code>{taskUid}</code></p>}

      {isMulti && (
        <div style={{ marginBottom: '1.25rem' }}>
          <p className="dim small" style={{ marginTop: 0 }}>
            Объединение <b>{taskUids.length}</b> задач на один оффер в одну сессию.
            Каждый ленд сохранит привязку к своей задаче — позже можно отправить на проверку по каждой.
          </p>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {sourceTasks.map((t) => (
              <div key={t.uid} style={{
                display: 'flex', alignItems: 'center', gap: '0.75rem',
                padding: '0.55rem 0.8rem', borderRadius: 8,
                border: '1px solid var(--border, #2a2a2a)', background: 'var(--bg-elevated, #141414)',
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {t.title || t.uid}
                  </div>
                  <div className="dim small" style={{ marginTop: 2 }}>
                    {t.offer}{t.ids.length ? ` · ленды: ${t.ids.join(', ')}` : ' · ленды не распознаны'}
                  </div>
                </div>
                <span className="dim small" style={{ fontFamily: 'monospace' }}>{t.ids.length} лендов</span>
              </div>
            ))}
            {sourceTasks.length === 0 && <p className="dim small">Загружаю задачи…</p>}
          </div>
        </div>
      )}

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: '1rem' }}>
        <span className="dim" style={{ fontSize: 12 }}>Целевой оффер</span>
        <input className="form-input" value={offer} onChange={(e) => setOffer(e.target.value)} placeholder="VA Ultravix Low MX" />
      </label>

      {!isMulti && <>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: '0.5rem' }}>
        <span className="dim" style={{ fontSize: 12 }}>ID лендов из Keitaro (через запятую/пробел)</span>
        <input className="form-input" value={idsText} onChange={(e) => setIdsText(e.target.value)} placeholder="9224, 14278" />
      </label>
      <OfferNamesHint idsText={idsText} />
      <p className="dim small" style={{ marginTop: 6 }}>Будут скачаны из Keitaro автоматически.</p>

      <div style={{ margin: '1rem 0', textAlign: 'center', color: 'var(--text-muted,#666)', fontSize: 12 }}>— или / и —</div>

      <label style={{ display: 'flex', flexDirection: 'column', gap: 4, marginBottom: '0.5rem' }}>
        <span className="dim" style={{ fontSize: 12 }}>Ссылки на лендинги (каждая с новой строки или через пробел)</span>
        <textarea
          className="form-input"
          value={urlsText}
          onChange={(e) => setUrlsText(e.target.value)}
          placeholder={'https://example.com/lander/\nhttps://site2.com/promo'}
          rows={2}
          style={{ resize: 'vertical', fontFamily: 'monospace', fontSize: 12 }}
        />
      </label>
      <p className="dim small" style={{ marginTop: 6 }}>
        Будут скачаны скрапером (Playwright). Для гео-защищённых лендов прокси
        можно выбрать уже внутри сессии (блок «Добавить ленд»).
      </p>

      <div style={{ margin: '1rem 0', textAlign: 'center', color: 'var(--text-muted,#666)', fontSize: 12 }}>— или / и —</div>

      <span className="dim" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>Загрузить готовые архивы (.zip)</span>
      <label
        style={{
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          gap: 4, padding: '1.25rem', cursor: 'pointer', textAlign: 'center',
          border: '1.5px dashed var(--border, #3a3a3a)', borderRadius: 10,
          background: 'var(--bg-elevated, #141414)', transition: 'border-color .15s',
        }}
        onDragOver={(e) => { e.preventDefault(); e.currentTarget.style.borderColor = 'var(--accent, #7c6fff)'; }}
        onDragLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border, #3a3a3a)'; }}
        onDrop={(e) => {
          e.preventDefault();
          e.currentTarget.style.borderColor = 'var(--border, #3a3a3a)';
          const dropped = Array.from(e.dataTransfer.files || []).filter((f) => f.name.toLowerCase().endsWith('.zip'));
          if (dropped.length) setFiles(dropped);
        }}
      >
        <div style={{ fontSize: 22, opacity: 0.7 }}>↑</div>
        <div style={{ fontSize: 13 }}>Перетащи .zip сюда или <span style={{ color: 'var(--accent, #7c6fff)' }}>выбери файл</span></div>
        <input
          type="file"
          accept=".zip"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => setFiles(Array.from(e.target.files || []))}
        />
      </label>
      {files.length > 0 && (
        <div style={{ marginTop: '0.6rem', display: 'flex', flexDirection: 'column', gap: 4 }}>
          {files.map((f) => (
            <div key={f.name} style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              fontSize: 12, padding: '0.35rem 0.6rem', borderRadius: 6,
              background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border, #2a2a2a)',
            }}>
              <span style={{ fontFamily: 'monospace' }}><Icon name="archive" size={12} /> {f.name}</span>
              <span className="dim">{(f.size / 1024 / 1024).toFixed(1)} MB</span>
            </div>
          ))}
        </div>
      )}
      </>}

      {error && (
        <div style={{ padding: '0.6rem 1rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, margin: '1rem 0', fontSize: 13 }}>
          {error}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginTop: '1.5rem' }}>
        <button className="btn btn-primary" onClick={create} disabled={busy}>
          {busy ? 'Создаю…' : isMulti ? `Объединить ${taskUids.length} задач →` : 'Создать сессию →'}
        </button>
        {status && <span className="dim small">{status}</span>}
      </div>
    </div>
  );
}
