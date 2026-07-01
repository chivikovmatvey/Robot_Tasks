import { useCallback, useEffect, useState } from 'react';
import { api, type HealthResponse, type InfoResponse } from '../lib/api';

type Status = 'checking' | 'connected' | 'error';

export function HomePage() {
  const [status, setStatus] = useState<Status>('checking');
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [info,   setInfo]   = useState<InfoResponse | null>(null);
  const [error,  setError]  = useState<string>('');
  const [clearBusy, setClearBusy] = useState(false);
  const [clearMsg, setClearMsg] = useState('');
  const [clearIsError, setClearIsError] = useState(false);

  const refreshInfo = useCallback(() => {
    api.info().then(setInfo).catch(() => {});
  }, []);

  useEffect(() => {
    Promise.all([api.health(), api.info()])
      .then(([h, i]) => {
        setHealth(h);
        setInfo(i);
        setStatus('connected');
      })
      .catch((e) => {
        setError(String(e?.message ?? e));
        setStatus('error');
      });
  }, []);

  const handleClearStorage = async (scope: 'temp' | 'all') => {
    if (scope === 'all') {
      const ok = window.confirm(
        'Удалить все готовые архивы из outputs/?\n\n'
        + 'Папки assets/ и configs/ не трогаем.',
      );
      if (!ok) return;
    }
    setClearBusy(true);
    setClearMsg('');
    setClearIsError(false);
    try {
      const r = await api.clearStorage(scope);
      setClearMsg(
        `Очищено (${r.scope}): uploads −${r.uploads_removed}, output −${r.output_cleared}`
        + (r.outputs_cleared ? `, outputs −${r.outputs_cleared}` : ''),
      );
      refreshInfo();
    } catch (e: unknown) {
      setClearIsError(true);
      setClearMsg(e instanceof Error ? e.message : 'Ошибка очистки');
    } finally {
      setClearBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="page-header">
        <h1>Главная</h1>
        <p className="muted">Состояние сервиса и текущая статистика</p>
      </div>

      <div className="grid-2">
        {/* Статус соединения */}
        <div className="card">
          <div className="card-label">Соединение с бэкендом</div>

          {status === 'checking' && (
            <div className="status-row">
              <div className="dot dot-warning" />
              <div>
                <div className="status-text">Проверяю...</div>
                <div className="dim small">localhost:8000</div>
              </div>
            </div>
          )}

          {status === 'connected' && health && (
            <div className="status-row">
              <div className="dot dot-success" />
              <div>
                <div className="status-text">Подключено</div>
                <div className="dim small">
                  {health.service} · {health.version}
                </div>
              </div>
            </div>
          )}

          {status === 'error' && (
            <div className="status-row">
              <div className="dot dot-danger" />
              <div>
                <div className="status-text">Бэкенд не отвечает</div>
                <div className="dim small mono">{error}</div>
                <div className="muted small" style={{ marginTop: 8 }}>
                  Запусти <code className="mono">start.bat</code> или проверь, что{' '}
                  <code className="mono">uvicorn</code> работает на :8000
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Статистика + очистка */}
        <div className="card storage-stats-card">
          <div className="storage-stats-card-head">
            <div className="card-label" style={{ marginBottom: 0 }}>Файлы в storage</div>
            {status === 'connected' && (
              <div className="storage-clear-actions">
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={clearBusy}
                  title="Удалить загрузки (uploads) и черновик (output). Готовые ZIP не трогаем."
                  onClick={() => void handleClearStorage('temp')}
                >
                  {clearBusy ? '…' : 'Очистить временное'}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-danger-outline"
                  disabled={clearBusy}
                  title="То же + все файлы в outputs/ (готовые архивы)."
                  onClick={() => void handleClearStorage('all')}
                >
                  + готовые ZIP
                </button>
              </div>
            )}
          </div>
          {info ? (
            <>
              <div className="stats-grid">
                <div className="stat">
                  <div className="stat-value">{info.uploads}</div>
                  <div className="stat-label">uploads</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{info.outputs}</div>
                  <div className="stat-label">готовые</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{info.assets}</div>
                  <div className="stat-label">фото</div>
                </div>
                <div className="stat">
                  <div className="stat-value">{info.configs}</div>
                  <div className="stat-label">конфиги</div>
                </div>
              </div>
              {clearMsg && (
                <p className={`storage-clear-msg dim small ${clearIsError ? 'error-text' : ''}`}>
                  {clearMsg}
                </p>
              )}
            </>
          ) : (
            <div className="muted small">Ожидание данных...</div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 20 }}>
        <div className="card-label">Скелет проекта запущен</div>
        <p className="muted" style={{ marginTop: 4 }}>
          В следующих этапах сюда добавятся страницы обработки.
          Каждая будет переиспользовать твои существующие Python-скрипты
          через FastAPI-роуты в <code className="mono">backend/main.py</code>.
        </p>
      </div>
    </div>
  );
}
