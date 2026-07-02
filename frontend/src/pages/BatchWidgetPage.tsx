import { useRef, useState, ChangeEvent, DragEvent } from 'react';
import { api, BatchWidgetResponse, BatchWidgetResult } from '../lib/api';
import { LogViewer } from '../components/LogViewer';
import { Icon } from '../components/Icon';

export function BatchWidgetPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [discount, setDiscount] = useState('50%');
  const [busy, setBusy] = useState(false);
  const [response, setResponse] = useState<BatchWidgetResponse | null>(null);
  const [error, setError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const [filter, setFilter] = useState<'all' | 'success' | 'failed'>('all');
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = (incoming: FileList | File[]) => {
    const zips = Array.from(incoming).filter(
      (f) => f.name.toLowerCase().endsWith('.zip')
    );
    if (zips.length === 0) {
      setError('Только .zip файлы принимаются');
      return;
    }
    setError('');
    // Дедупликация по имени
    const merged = [...files];
    for (const f of zips) {
      if (!merged.find((m) => m.name === f.name && m.size === f.size)) {
        merged.push(f);
      }
    }
    setFiles(merged);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files) addFiles(e.dataTransfer.files);
  };

  const handleSelect = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(e.target.files);
    e.target.value = '';
  };

  const removeFile = (idx: number) => {
    setFiles(files.filter((_, i) => i !== idx));
  };

  const clearAll = () => {
    setFiles([]);
    setResponse(null);
    setError('');
  };

  const handleProcess = async () => {
    if (files.length === 0) return;
    setBusy(true);
    setError('');
    setResponse(null);
    try {
      const res = await api.batchWidget(files, {}, discount);
      setResponse(res);
    } catch (e: any) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusy(false);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  const filteredResults =
    response?.results.filter((r) => {
      if (filter === 'success') return r.success;
      if (filter === 'failed') return !r.success;
      return true;
    }) ?? [];

  return (
    <div className="page">
      <div className="page-header">
        <h1>Batch Widget</h1>
        <p className="muted">
          Пакетная вставка нового виджета в группу лендингов. Принимает несколько ZIP, возвращает HTML-файлы с готовым виджетом.
          JS-бандл нужно класть руками отдельно.
        </p>
      </div>

      {/* Дропзона + файлы */}
      <div className="card">
        <div
          className={`dropzone ${dragOver ? 'dropzone-active' : ''} ${busy ? 'dropzone-disabled' : ''}`}
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
            accept=".zip"
            multiple
            onChange={handleSelect}
            style={{ display: 'none' }}
            disabled={busy}
          />
          <div className="dropzone-icon">
            <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
          </div>
          <div className="dropzone-text">
            {files.length > 0 ? `Добавить ещё (выбрано ${files.length})` : 'Перетащи ZIP-архивы сюда'}
          </div>
          <div className="dropzone-hint dim small">или клик — можно много за раз</div>
        </div>

        {files.length > 0 && (
          <div className="batch-files-list">
            <div className="card-label">
              Файлов в очереди: {files.length}
              <button className="btn-link small" onClick={clearAll} style={{ marginLeft: 12 }}>
                очистить всё
              </button>
            </div>
            <div className="batch-files">
              {files.map((f, i) => (
                <div key={`${f.name}-${i}`} className="batch-file-row">
                  <span className="mono small batch-file-name">{f.name}</span>
                  <span className="dim small">{formatSize(f.size)}</span>
                  {!busy && (
                    <button className="btn-link small" onClick={() => removeFile(i)}>
                      ✕
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="form-row" style={{ marginTop: 12 }}>
          <label className="form-label" style={{ maxWidth: 200 }}>
            Скидка в виджете
            <input
              className="form-input"
              type="text"
              value={discount}
              onChange={(e) => setDiscount(e.target.value)}
              placeholder="50%"
              disabled={busy}
            />
          </label>
        </div>

        <div className="form-actions">
          <button
            className="btn btn-primary"
            disabled={files.length === 0 || busy}
            onClick={handleProcess}
          >
            {busy
              ? `Обрабатываю ${files.length}...`
              : `Обработать ${files.length || ''}`}
          </button>
        </div>
      </div>

      {error && (
        <div className="card error-card">
          <strong>Ошибка:</strong> {error}
        </div>
      )}

      {/* Результаты */}
      {response && (
        <div className="card">
          <div className="batch-summary">
            <div className="batch-stats">
              <div className="batch-stat">
                <div className="batch-stat-value">{response.total}</div>
                <div className="batch-stat-label">всего</div>
              </div>
              <div className="batch-stat batch-stat-ok">
                <div className="batch-stat-value">{response.success}</div>
                <div className="batch-stat-label">успешно</div>
              </div>
              <div className="batch-stat batch-stat-fail">
                <div className="batch-stat-value">{response.failed}</div>
                <div className="batch-stat-label">с ошибкой</div>
              </div>
            </div>

            {response.batch_url && response.batch_name && (
              <a className="btn btn-primary" href={response.batch_url} download={response.batch_name}>
                Скачать все ({response.success}) одним ZIP
              </a>
            )}
          </div>

          <div className="batch-filter">
            <button
              className={`btn-tab ${filter === 'all' ? 'btn-tab-active' : ''}`}
              onClick={() => setFilter('all')}
            >
              Все ({response.total})
            </button>
            <button
              className={`btn-tab ${filter === 'success' ? 'btn-tab-active' : ''}`}
              onClick={() => setFilter('success')}
            >
              Успешно ({response.success})
            </button>
            <button
              className={`btn-tab ${filter === 'failed' ? 'btn-tab-active' : ''}`}
              onClick={() => setFilter('failed')}
              disabled={response.failed === 0}
            >
              С ошибкой ({response.failed})
            </button>
          </div>

          <table className="batch-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Статус</th>
                <th>Country</th>
                <th>Lang</th>
                <th>Excl</th>
                <th>Product</th>
                <th>Скачать</th>
              </tr>
            </thead>
            <tbody>
              {filteredResults.map((r) => (
                <ResultRow key={r.file_id + r.source_name} r={r} batchName={response.batch_name} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {response && response.log.length > 0 && (
        <LogViewer lines={response.log} title="Общий лог обработки" />
      )}
    </div>
  );
}


function ResultRow({ r, batchName }: { r: BatchWidgetResult; batchName: string | null }) {
  const [expanded, setExpanded] = useState(false);
  const statusBadge = (() => {
    if (!r.success) return <span className="badge badge-danger">ошибка</span>;
    if (r.status === 'updated') return <span className="badge">обновлён</span>;
    if (r.status === 'replaced_old') return <span className="badge badge-success">заменён</span>;
    return <span className="badge badge-success">вставлен</span>;
  })();

  return (
    <>
      <tr className={r.success ? '' : 'batch-row-fail'} onClick={() => setExpanded(!expanded)}>
        <td className="mono small">{r.file_id}</td>
        <td>{statusBadge}</td>
        <td className="mono small">{r.detected?.country || '—'}</td>
        <td className="mono small">{r.detected?.language || '—'}</td>
        <td className="mono small">{r.detected?.exclude_word?.trim() || '—'}</td>
        <td className="small">{r.detected?.product_name || '—'}</td>
        <td>
          {r.success && batchName ? (
            <a
              className="btn-link small"
              href={api.batchWidgetHtmlUrl(r.file_id, batchName)}
              download={`${r.file_id}.html`}
              onClick={(e) => e.stopPropagation()}
            >
              {r.file_id}.html ↓
            </a>
          ) : (
            <span className="dim small">—</span>
          )}
        </td>
      </tr>
      {expanded && (
        <tr className="batch-row-detail">
          <td colSpan={7}>
            <div className="dim small">{r.source_name}</div>
            {r.error && <div className="error-text small"><Icon name="alert" size={11} /> {r.error}</div>}
            <div className="batch-log mono small">
              {r.log.map((l, i) => (
                <div key={i}>{l}</div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
