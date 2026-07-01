import { useEffect, useState, useRef, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';

interface ZipFile {
  path: string;
  size: number;
}

const TEXT_EXTS = new Set(['.php', '.html', '.htm', '.css', '.js', '.json', '.txt', '.xml']);
const PREVIEW_EXTS = new Set(['.php', '.html', '.htm']);
const IMAGE_EXTS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg']);

function getExt(path: string) {
  const dot = path.lastIndexOf('.');
  return dot !== -1 ? path.slice(dot).toLowerCase() : '';
}

function formatSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function extColor(ext: string) {
  if (['.php'].includes(ext)) return '#818cf8';
  if (['.html', '.htm'].includes(ext)) return '#f97316';
  if (['.css'].includes(ext)) return '#38bdf8';
  if (['.js'].includes(ext)) return '#facc15';
  if (IMAGE_EXTS.has(ext)) return '#4ade80';
  return '#94a3b8';
}

export function PreviewPage() {
  const [searchParams] = useSearchParams();
  const [outputs, setOutputs] = useState<{ name: string; size: number; url: string }[]>([]);
  const [selectedZip, setSelectedZip] = useState<string>(searchParams.get('zip') || '');
  const [files, setFiles] = useState<ZipFile[]>([]);
  const [selectedFile, setSelectedFile] = useState<string>('');
  const [code, setCode] = useState<string>('');
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [loadingCode, setLoadingCode] = useState(false);
  const [view, setView] = useState<'code' | 'preview' | 'split'>('split');

  // Редактирование
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string>('');
  const [savedFlash, setSavedFlash] = useState(false);
  // Версия для cache-bust iframe после сохранения
  const [previewVersion, setPreviewVersion] = useState(0);

  const iframeRef = useRef<HTMLIFrameElement>(null);
  // Рефы чтобы Ctrl+S всегда видел актуальное состояние
  const stateRef = useRef({ selectedZip, selectedFile, code, dirty });
  stateRef.current = { selectedZip, selectedFile, code, dirty };

  useEffect(() => {
    fetch('/api/outputs')
      .then(r => r.json())
      .then((data) => {
        setOutputs(data);
        const zipFromUrl = searchParams.get('zip');
        if (zipFromUrl) loadZip(zipFromUrl);
      })
      .catch(console.error);
  }, []);

  const confirmDiscard = () => {
    if (!stateRef.current.dirty) return true;
    return window.confirm('Есть несохранённые правки. Перейти без сохранения?');
  };

  const loadZip = async (name: string) => {
    if (!confirmDiscard()) return;
    setSelectedZip(name);
    setFiles([]);
    setSelectedFile('');
    setCode('');
    setDirty(false);
    setSaveError('');
    setLoadingFiles(true);
    try {
      const res = await fetch(`/api/preview/${encodeURIComponent(name)}/files`);
      const data = await res.json();
      setFiles(data);
      const main = data.find((f: ZipFile) =>
        f.path === 'index.php' || f.path === 'index.html'
      );
      if (main) loadFile(name, main.path, true);
    } finally {
      setLoadingFiles(false);
    }
  };

  const loadFile = async (zip: string, path: string, skipConfirm = false) => {
    if (!skipConfirm && !confirmDiscard()) return;
    setSelectedFile(path);
    setDirty(false);
    setSaveError('');
    const ext = getExt(path);
    if (!TEXT_EXTS.has(ext)) { setCode(''); return; }
    setLoadingCode(true);
    try {
      // raw=1 — исходник как text/plain для панели кода
      const res = await fetch(`/api/preview/${encodeURIComponent(zip)}/file?path=${encodeURIComponent(path)}&raw=1`);
      const text = await res.text();
      setCode(text);
    } finally {
      setLoadingCode(false);
    }
  };

  const saveFile = useCallback(async () => {
    const { selectedZip: zip, selectedFile: path, code: content, dirty: isDirty } = stateRef.current;
    if (!zip || !path || !isDirty) return;
    setSaving(true);
    setSaveError('');
    try {
      const res = await fetch(`/api/preview/${encodeURIComponent(zip)}/file`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, content }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setDirty(false);
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
      // Обновляем размер в списке файлов + перезагружаем iframe
      setFiles(prev => prev.map(f => f.path === path ? { ...f, size: data.size } : f));
      setPreviewVersion(v => v + 1);
    } catch (e: any) {
      setSaveError(e.message || 'Ошибка сохранения');
    } finally {
      setSaving(false);
    }
  }, []);

  // Ctrl+S / Cmd+S
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        saveFile();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [saveFile]);

  // Предупреждение при закрытии вкладки с несохранёнными правками
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (stateRef.current.dirty) { e.preventDefault(); e.returnValue = ''; }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, []);

  const selectedExt = getExt(selectedFile);
  const editable = !!selectedFile && TEXT_EXTS.has(selectedExt);

  const previewUrl = selectedZip && selectedFile && PREVIEW_EXTS.has(selectedExt)
    ? `/api/preview/${encodeURIComponent(selectedZip)}/render?path=${encodeURIComponent(selectedFile)}&v=${previewVersion}`
    : null;

  const imageUrl = selectedZip && selectedFile && IMAGE_EXTS.has(selectedExt)
    ? `/api/preview/${encodeURIComponent(selectedZip)}/file?path=${encodeURIComponent(selectedFile)}&v=${previewVersion}`
    : null;

  return (
    <div className="page" style={{ display: 'flex', flexDirection: 'column', height: '100vh', padding: 0, gap: 0 }}>

      {/* Шапка */}
      <div style={{ padding: '0.75rem 1.25rem', borderBottom: '1px solid var(--c-border, #2a2a2a)', display: 'flex', gap: '1rem', alignItems: 'center', flexShrink: 0 }}>
        <h1 style={{ margin: 0, fontSize: 18 }}>Просмотр результата</h1>

        <select
          className="form-input"
          style={{ maxWidth: 400, flex: 1 }}
          value={selectedZip}
          onChange={e => loadZip(e.target.value)}
        >
          <option value="">— выбери архив —</option>
          {outputs.map(o => (
            <option key={o.name} value={o.name}>{o.name}</option>
          ))}
        </select>

        {/* Переключатель вида */}
        <div style={{ display: 'flex', gap: 0, border: '1px solid var(--c-border, #333)', borderRadius: 6, overflow: 'hidden' }}>
          {(['split', 'code', 'preview'] as const).map(v => (
            <button
              key={v}
              onClick={() => setView(v)}
              style={{
                padding: '0.3rem 0.75rem', border: 'none', cursor: 'pointer', fontSize: 12,
                background: view === v ? 'var(--c-accent, #7c6fff)' : 'transparent',
                color: view === v ? '#fff' : 'var(--c-muted, #888)',
              }}
            >
              {v === 'split' ? '⬛⬛ Split' : v === 'code' ? '{ } Код' : '👁 Превью'}
            </button>
          ))}
        </div>

        {/* Сохранить */}
        {editable && (
          <button
            className="btn"
            onClick={saveFile}
            disabled={!dirty || saving}
            title="Ctrl+S"
            style={{
              fontSize: 13, padding: '0.3rem 0.75rem',
              background: dirty ? 'var(--c-accent, #7c6fff)' : 'transparent',
              color: dirty ? '#fff' : 'var(--c-muted, #666)',
              border: '1px solid var(--c-border, #333)', borderRadius: 6,
              cursor: dirty ? 'pointer' : 'default',
            }}
          >
            {saving ? 'Сохраняю…' : savedFlash ? '✓ Сохранено' : dirty ? '● Сохранить' : 'Сохранить'}
          </button>
        )}

        {selectedZip && (
          <a
            href={`/api/download/${encodeURIComponent(selectedZip)}`}
            className="btn btn-primary"
            style={{ fontSize: 13, padding: '0.3rem 0.75rem', textDecoration: 'none' }}
          >
            ↓ Скачать
          </a>
        )}
      </div>

      {saveError && (
        <div style={{ padding: '0.4rem 1.25rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', fontSize: 12, flexShrink: 0 }}>
          Ошибка сохранения: {saveError}
        </div>
      )}

      {/* Основная область */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>

        {/* Файлы — боковая панель */}
        <div style={{
          width: 220, flexShrink: 0, borderRight: '1px solid var(--c-border, #2a2a2a)',
          overflowY: 'auto', background: 'var(--c-surface, #141414)',
        }}>
          {loadingFiles && <p className="dim small" style={{ padding: '0.5rem 1rem' }}>Загрузка...</p>}
          {files.map(f => {
            const ext = getExt(f.path);
            const isSelected = f.path === selectedFile;
            return (
              <button
                key={f.path}
                onClick={() => loadFile(selectedZip, f.path)}
                title={f.path}
                style={{
                  display: 'flex', flexDirection: 'column', width: '100%', textAlign: 'left',
                  padding: '0.4rem 0.75rem', border: 'none', cursor: 'pointer',
                  background: isSelected ? 'var(--c-accent-dim, rgba(124,111,255,0.15))' : 'transparent',
                  borderLeft: isSelected ? '2px solid var(--c-accent, #7c6fff)' : '2px solid transparent',
                }}
              >
                <span style={{ fontSize: 12, color: extColor(ext), fontFamily: 'monospace', wordBreak: 'break-all' }}>
                  {f.path}{isSelected && dirty ? ' ●' : ''}
                </span>
                <span style={{ fontSize: 10, color: 'var(--c-muted, #555)' }}>{formatSize(f.size)}</span>
              </button>
            );
          })}
        </div>

        {/* Код + превью */}
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

          {/* Код (редактор) */}
          {(view === 'code' || view === 'split') && (
            <div style={{
              flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden',
              borderRight: view === 'split' ? '1px solid var(--c-border, #2a2a2a)' : 'none',
            }}>
              <div style={{ padding: '0.35rem 0.75rem', borderBottom: '1px solid var(--c-border, #222)', fontSize: 11, color: 'var(--c-muted, #666)', display: 'flex', justifyContent: 'space-between' }}>
                <span>{selectedFile || 'Выбери файл'}{dirty && <span style={{ color: 'var(--c-accent, #7c6fff)' }}> ● не сохранено</span>}</span>
                {editable && <span style={{ opacity: 0.6 }}>редактируемый · Ctrl+S</span>}
              </div>
              {loadingCode
                ? <p className="dim small" style={{ padding: '1rem' }}>Загрузка...</p>
                : imageUrl
                  ? <img src={imageUrl} style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', margin: 'auto', display: 'block', padding: '1rem' }} />
                  : (
                    <textarea
                      value={code}
                      onChange={e => { setCode(e.target.value); setDirty(true); }}
                      spellCheck={false}
                      style={{
                        flex: 1, resize: 'none', border: 'none', outline: 'none',
                        background: 'var(--c-bg, #0d0d0d)', color: 'var(--c-text, #e5e5e5)',
                        fontFamily: 'monospace', fontSize: 12, lineHeight: 1.6,
                        padding: '0.75rem 1rem', overflowY: 'auto', tabSize: 2,
                      }}
                    />
                  )
              }
            </div>
          )}

          {/* Превью */}
          {(view === 'preview' || view === 'split') && (
            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <div style={{ padding: '0.35rem 0.75rem', borderBottom: '1px solid var(--c-border, #222)', fontSize: 11, color: 'var(--c-muted, #666)' }}>
                Визуальный превью {!previewUrl && <span style={{ color: 'var(--c-warn, #f59e0b)' }}>— выбери .php или .html файл</span>}
                {previewUrl && dirty && <span style={{ color: 'var(--c-warn, #f59e0b)' }}> — сохрани (Ctrl+S) чтобы обновить</span>}
              </div>
              {previewUrl
                ? (
                  <iframe
                    key={previewVersion}
                    ref={iframeRef}
                    src={previewUrl}
                    style={{ flex: 1, border: 'none', background: '#fff' }}
                    title="preview"
                  />
                )
                : (
                  <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <p className="dim small">Выбери .php или .html файл для превью</p>
                  </div>
                )
              }
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
