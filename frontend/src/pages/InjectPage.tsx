import { useState } from 'react';
import { api, ProcessResult, ScanResponse } from '../lib/api';
import { Dropzone } from '../components/Dropzone';
import { LogViewer } from '../components/LogViewer';
import { ResultCard } from '../components/ResultCard';
import { InjectForm, InjectFormValues } from '../components/InjectForm';
import { ZipImagePicker } from '../components/ZipImagePicker';

const DEFAULT_VALUES: InjectFormValues = {
  country: '',
  language: '',
  vertical_id: '',
  exclude_word: '',
  price_new: '',
  price_old: '',
  prod_img: 'product.webp',
  custom_replacements: '',
};

export function InjectPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [activeFileIndex, setActiveFileIndex] = useState(0);
  const [values, setValues] = useState<InjectFormValues>(DEFAULT_VALUES);
  const [busy, setBusy] = useState(false);
  const [busyScan, setBusyScan] = useState(false);
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null);
  const [completedResults, setCompletedResults] = useState<Array<{ source: string; result: ProcessResult }>>([]);
  const [error, setError] = useState<string>('');

  const allDone =
    files.length > 0 &&
    completedResults.length === files.length &&
    !busy;

  const scanFile = async (file: File) => {
    setBusyScan(true);
    setScanResult(null);
    try {
      const res = await api.scan(file);
      setScanResult(res);
    } catch {
      // ignore — image picker just won't show
    } finally {
      setBusyScan(false);
    }
  };

  const handleFiles = (next: File[]) => {
    setFiles(next);
    setCompletedResults([]);
    setActiveFileIndex(0);
    setError('');
    setScanResult(null);
    if (next.length > 0) {
      scanFile(next[0]);
    }
  };

  const dropzoneDisabled =
    busy ||
    (files.length > 0 && !allDone && (completedResults.length > 0 || activeFileIndex > 0));

  const handleProcess = async () => {
    if (!files.length || !values.country) return;
    setBusy(true);
    setError('');
    try {
      const f = files[activeFileIndex];
      const { vertical_id: _vid, ...payload } = values;
      const res = await api.inject(f, payload as Record<string, string>);
      setCompletedResults((prev) => [...prev, { source: f.name, result: res }]);
      const nextIdx = activeFileIndex + 1;
      setActiveFileIndex(nextIdx);
      setScanResult(null);
      if (nextIdx < files.length) {
        scanFile(files[nextIdx]);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Ошибка');
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setFiles([]);
    setActiveFileIndex(0);
    setCompletedResults([]);
    setError('');
    setValues(DEFAULT_VALUES);
    setScanResult(null);
  };

  const processLabel = () => {
    if (busy) return 'Внедряю...';
    if (files.length <= 1) return 'Внедрить';
    if (activeFileIndex < files.length - 1) {
      return `Внедрить и следующий (${activeFileIndex + 1}/${files.length})`;
    }
    return `Внедрить и завершить (${files.length}/${files.length})`;
  };

  return (
    <div className="page">
      <div className="page-header">
        <h1>Inject</h1>
        <p className="muted">
          Вставка counters, backfix, виджета и формы в сырой лендинг.
          Несколько ZIP обрабатываются по очереди: один архив за раз, общий список результатов в конце.
        </p>
      </div>

      <div className="card">
        <div className="card-label">Архивы</div>
        <Dropzone files={files} onFiles={handleFiles} multiple disabled={dropzoneDisabled} />
        {files.length > 1 && !allDone && (
          <p className="form-hint dim small" style={{ marginTop: '0.75rem' }}>
            В очереди {files.length} архивов. После обработки каждого можно при необходимости изменить параметры перед следующим.
          </p>
        )}
        {allDone && (
          <p className="form-hint dim small" style={{ marginTop: '0.75rem' }}>
            Все архивы обработаны. Скачай результаты ниже или нажми «Сброс» для новой партии.
          </p>
        )}
      </div>

      <div className="card">
        <div className="card-label">Параметры инжекта</div>
        {files.length > 1 && !allDone && (
          <p className="muted small" style={{ marginBottom: '0.75rem' }}>
            Сейчас: <span className="mono">{files[activeFileIndex]?.name}</span>
            {' '}({activeFileIndex + 1} из {files.length})
          </p>
        )}

        {busyScan && (
          <p className="dim small" style={{ marginBottom: '0.75rem' }}>Сканирую изображения...</p>
        )}

        {scanResult && (
          <ZipImagePicker
            uploadId={scanResult.upload_id}
            images={scanResult.detection.all_images}
            selectedName={values.prod_img}
            onSelect={(name) => setValues({ ...values, prod_img: name })}
          />
        )}

        <InjectForm values={values} onChange={setValues} />

        <div className="form-actions">
          <button
            className="btn btn-primary"
            disabled={!files.length || !values.country || busy || allDone}
            onClick={handleProcess}
          >
            {processLabel()}
          </button>
          {(files.length > 0 || completedResults.length > 0) && !busy && (
            <button className="btn" type="button" onClick={reset}>
              Сброс
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="card error-card">
          <strong>Ошибка:</strong> {error}
        </div>
      )}

      {completedResults.length > 0 && (
        <div className="card">
          <div className="card-label">
            Результаты ({completedResults.length}
            {files.length > 1 ? ` из ${files.length}` : ''})
          </div>
          <p className="dim small">
            Готовые архивы — ниже по одному.
          </p>
        </div>
      )}

      {completedResults.map(({ source, result }) => (
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
