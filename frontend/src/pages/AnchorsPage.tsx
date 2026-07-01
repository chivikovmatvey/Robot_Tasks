import { useState } from 'react';
import { api, ProcessResult } from '../lib/api';
import { Dropzone } from '../components/Dropzone';
import { LogViewer } from '../components/LogViewer';
import { ResultCard } from '../components/ResultCard';

export function AnchorsPage() {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProcessResult | null>(null);
  const [error, setError] = useState<string>('');

  const handleProcess = async () => {
    if (!file) return;
    setBusy(true);
    setError('');
    setResult(null);
    try {
      const res = await api.anchors(file);
      setResult(res);
    } catch (e: any) {
      setError(e?.message || 'Ошибка обработки');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h1>Anchors</h1>
        <p className="muted">Починка якорных ссылок в HTML</p>
      </div>

      <div className="card">
        <Dropzone file={file} onFile={setFile} disabled={busy} />

        <div className="form-actions">
          <button
            className="btn btn-primary"
            disabled={!file || busy}
            onClick={handleProcess}
          >
            {busy ? 'Обрабатываю...' : 'Починить якоря'}
          </button>
          {file && !busy && (
            <button className="btn" onClick={() => { setFile(null); setResult(null); setError(''); }}>
              Очистить
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="card error-card">
          <strong>Ошибка:</strong> {error}
        </div>
      )}

      {result && (
        <ResultCard
          resultName={result.result_name}
          resultUrl={result.result_url}
          success={result.success}
        />
      )}

      {result && <LogViewer lines={result.log} />}
    </div>
  );
}
