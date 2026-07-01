import { useState } from 'react';
import { api, OptimizeImage, ProcessResult } from '../lib/api';
import { Dropzone } from '../components/Dropzone';
import { LogViewer } from '../components/LogViewer';

type Step = 'upload' | 'preview' | 'done';

export function OptimizePage() {
  const [file, setFile] = useState<File | null>(null);
  const [step, setStep] = useState<Step>('upload');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uploadId, setUploadId] = useState('');
  const [images, setImages] = useState<OptimizeImage[]>([]);
  const [skipped, setSkipped] = useState<string[]>([]);
  const [result, setResult] = useState<ProcessResult | null>(null);

  const handleScan = async () => {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const res = await api.optimizeScan(file);
      setUploadId(res.upload_id);
      setImages(res.scan.images);
      setSkipped(res.scan.skipped);
      setStep('preview');
    } catch (e: any) {
      setError(e?.message || 'Ошибка сканирования');
    } finally {
      setBusy(false);
    }
  };

  const handleOptimize = async () => {
    setBusy(true);
    setError('');
    try {
      const res = await api.optimizeRun(uploadId);
      // Считаем успехом если есть файл — даже если success=false
      if (res.result_name) res.success = true;
      setResult(res);
      setStep('done');
    } catch (e: any) {
      setError(e?.message || 'Ошибка конвертации');
    } finally {
      setBusy(false);
    }
  };

  const handleReset = () => {
    setFile(null);
    setStep('upload');
    setError('');
    setUploadId('');
    setImages([]);
    setSkipped([]);
    setResult(null);
  };

  const totalSavingEst = images.reduce((acc, img) => acc + img.size_kb * 0.45, 0);

  return (
    <div className="page">
      <div className="page-header">
        <h1>Optimize</h1>
        <p className="muted">
          Конвертация PNG/JPG/JPEG → WebP и сжатие. Пути в HTML, PHP и CSS обновляются автоматически.
          GIF и видео не трогаются.
        </p>
      </div>

      {/* ШАГ 1 — загрузка */}
      {step === 'upload' && (
        <div className="card">
          <Dropzone
            file={file}
            onFile={(f) => setFile(f)}
            multiple={false}
            disabled={busy}
          />
          <div className="form-actions">
            <button
              className="btn btn-primary"
              disabled={!file || busy}
              onClick={handleScan}
            >
              {busy ? 'Сканирую…' : `Просканировать (1)`}
            </button>
          </div>
          {error && <p className="error-text">{error}</p>}
        </div>
      )}

      {/* ШАГ 2 — превью */}
      {step === 'preview' && (
        <div className="card">
          <div className="section-label">
            ЧТО БУДЕТ КОНВЕРТИРОВАНО — {file?.name}
          </div>

          {images.length === 0 ? (
            <p className="muted" style={{ padding: '12px 0' }}>
              PNG/JPG/JPEG файлов для конвертации не найдено.
            </p>
          ) : (
            <>
              <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 16 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
                    <th style={{ padding: '6px 8px', color: 'var(--muted)', fontWeight: 500, fontSize: 12 }}>Файл</th>
                    <th style={{ padding: '6px 8px', color: 'var(--muted)', fontWeight: 500, fontSize: 12, textAlign: 'right' }}>Размер</th>
                    <th style={{ padding: '6px 8px', color: 'var(--muted)', fontWeight: 500, fontSize: 12 }}>Результат</th>
                  </tr>
                </thead>
                <tbody>
                  {images.map((img) => (
                    <tr key={img.path} style={{ borderBottom: '1px solid var(--border-subtle)' }}>
                      <td style={{ padding: '7px 8px', fontSize: 13, fontFamily: 'monospace', color: 'var(--text)' }}>
                        {img.name}
                      </td>
                      <td style={{ padding: '7px 8px', fontSize: 13, textAlign: 'right', color: 'var(--muted)' }}>
                        {img.size_kb} KB
                      </td>
                      <td style={{ padding: '7px 8px', fontSize: 13, color: 'var(--accent-green)' }}>
                        → {img.name.replace(/\.(png|jpg|jpeg)$/i, '.webp')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div style={{ display: 'flex', gap: 24, marginBottom: 16, fontSize: 13, color: 'var(--muted)' }}>
                <span>Файлов: <strong style={{ color: 'var(--text)' }}>{images.length}</strong></span>
                <span>Ожидаемая экономия: <strong style={{ color: 'var(--accent-green)' }}>~{Math.round(totalSavingEst)} KB</strong></span>
              </div>
            </>
          )}

          {skipped.length > 0 && (
            <p style={{ fontSize: 12, color: 'var(--muted)', marginBottom: 12 }}>
              Пропущены (favicon): {skipped.map(s => s.split('/').pop()).join(', ')}
            </p>
          )}

          <div className="form-actions">
            <button
              className="btn btn-primary"
              disabled={busy || images.length === 0}
              onClick={handleOptimize}
            >
              {busy ? 'Конвертирую…' : `Конвертировать ${images.length} файлов`}
            </button>
            <button className="btn" onClick={handleReset}>
              Сброс
            </button>
          </div>
          {error && <p className="error-text">{error}</p>}
        </div>
      )}

      {/* ШАГ 3 — результат */}
      {step === 'done' && result && (
        <>
          <div className="card">
            {result.result_name ? (
              <div className="result-card result-success">
                <div className="result-icon">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </div>
                <div className="result-info">
                  <div className="result-title">Готово</div>
                  <div className="result-filename mono small dim">{result.result_name}</div>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <a className="btn btn-primary" href={result.result_url ?? '#'} download={result.result_name}>
                    Скачать
                  </a>
                  <button className="btn" onClick={handleReset}>Сброс</button>
                </div>
              </div>
            ) : (
              <div className="result-card result-error">
                <div className="result-icon">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
                  </svg>
                </div>
                <div>
                  <div className="result-title">Обработка не удалась</div>
                  <div className="dim small">Смотри лог ниже</div>
                </div>
                <button className="btn" onClick={handleReset}>Сброс</button>
              </div>
            )}
          </div>
          {result.log && Array.isArray(result.log) && result.log.length > 0 && (
            <div className="card" style={{ marginTop: 12 }}>
              <div className="section-label">ЛОГ ОБРАБОТКИ</div>
              <LogViewer lines={result.log ?? []} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
