import { useEffect, useState, useRef, ChangeEvent, DragEvent } from 'react';
import { api } from '../lib/api';

interface AssetsInlinePanelProps {
  /** Вызвать после успешной загрузки или удаления — родитель обновит список имён */
  onAssetsChanged: () => void;
}

/**
 * Загрузка и просмотр storage/assets/ — встроено в Scan + Adapt (отдельная страница Assets не нужна).
 */
export function AssetsInlinePanel({ onAssetsChanged }: AssetsInlinePanelProps) {
  const [assets, setAssets] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const refresh = () =>
    api.assets().then(setAssets).catch((e) => setError(String(e)));

  useEffect(() => {
    void refresh();
  }, []);

  const handleUpload = async (files: FileList | File[]) => {
    setBusy(true);
    setError('');
    try {
      for (const f of Array.from(files)) {
        await api.uploadAsset(f);
      }
      await refresh();
      onAssetsChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки');
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (filename: string) => {
    if (!confirm(`Удалить ${filename}?`)) return;
    try {
      await api.deleteAsset(filename);
      await refresh();
      onAssetsChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка');
    }
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files?.length) {
      void handleUpload(e.dataTransfer.files);
    }
  };

  const handleSelect = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) {
      void handleUpload(e.target.files);
      e.target.value = '';
    }
  };

  return (
    <div className="assets-inline-panel">
      <div className="card-label">Фото для замены (storage/assets/)</div>
      <p className="form-hint dim small" style={{ marginBottom: '10px' }}>
        Загрузи сюда новые картинки — они появятся в выпадающих списках ниже. Можно несколько файлов за раз.
      </p>

      <div
        className={`dropzone assets-inline-dropzone ${dragOver ? 'dropzone-active' : ''} ${busy ? 'dropzone-disabled' : ''}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (!busy) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => !busy && inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".png,.jpg,.jpeg,.webp,.gif"
          multiple
          onChange={handleSelect}
          style={{ display: 'none' }}
          disabled={busy}
        />
        <div className="dropzone-icon">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
            <circle cx="8.5" cy="8.5" r="1.5" />
            <polyline points="21 15 16 10 5 21" />
          </svg>
        </div>
        <div className="dropzone-text">
          {busy ? 'Загружаю…' : 'Перетащи фото сюда или кликни'}
        </div>
        <div className="dropzone-hint dim small">PNG, JPG, WEBP, GIF</div>
      </div>

      {error && (
        <div className="error-text" style={{ marginTop: '8px' }}>
          <strong>Ошибка:</strong> {error}
        </div>
      )}

      <div className="card-label" style={{ marginTop: '14px' }}>
        Загружено · {assets.length} {assets.length === 1 ? 'файл' : 'файлов'}
      </div>
      {assets.length === 0 ? (
        <div className="dim small">Пока пусто — добавь файлы в зону выше.</div>
      ) : (
        <div className="assets-grid assets-inline-grid">
          {assets.map((name) => (
            <div key={name} className="asset-card">
              <div className="asset-thumb">
                <img
                  src={`/api/assets-file/${encodeURIComponent(name)}`}
                  alt={name}
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              </div>
              <div className="asset-name mono small" title={name}>{name}</div>
              <button type="button" className="btn asset-delete" onClick={() => void handleDelete(name)}>
                Удалить
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
