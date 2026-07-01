import { useState } from 'react';
import { api, ProcessResult } from '../lib/api';
import { Dropzone } from '../components/Dropzone';
import { LogViewer } from '../components/LogViewer';
import { ResultCard } from '../components/ResultCard';

export function CleanPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<Array<{ source: string; result: ProcessResult }>>([]);
  const [error, setError] = useState<string>('');

  const handleProcess = async () => {
    if (!files.length) return;
    setBusy(true);
    setError('');
    setResults([]);
    try {
      const next: Array<{ source: string; result: ProcessResult }> = [];
      for (const f of files) {
        const res = await api.clean(f);
        next.push({ source: f.name, result: res });
      }
      setResults(next);
    } catch (e: any) {
      setError(e?.message || 'Ошибка');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h1>Clean</h1>
        <p className="muted">
          Очистка лендинга от чужого кода: метрик, скриптов, hidden inputs.
          Снимает <code className="mono">maxlength</code>/<code className="mono">minlength</code> с инпутов имени и телефона.
        </p>
      </div>

      <div className="card">
        <Dropzone files={files} onFiles={setFiles} multiple disabled={busy} />

        <div className="form-actions">
          <button
            className="btn btn-primary"
            disabled={!files.length || busy}
            onClick={handleProcess}
          >
            {busy ? 'Очищаю...' : `Очистить (${files.length})`}
          </button>
          {files.length > 0 && !busy && (
            <button className="btn" onClick={() => { setFiles([]); setResults([]); setError(''); }}>
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

      {results.map(({ source, result }) => (
        <div key={`${source}-${result.result_name ?? 'no-result'}`}>
          <div className="card-label">Файл: {source}</div>
          <ResultCard
            resultName={result.result_name}
            resultUrl={result.result_url}
            success={result.success}
          />
          <LogViewer lines={result.log} title={`Лог обработки: ${source}`} />
        </div>
      ))}
    </div>
  );
}
