import { useNavigate } from 'react-router-dom';
import { Icon } from './Icon';

interface ResultCardProps {
  resultName: string | null;
  resultUrl: string | null;
  success: boolean;
}

export function ResultCard({ resultName, resultUrl, success }: ResultCardProps) {
  const navigate = useNavigate();

  if (!resultName && success) return null;

  if (!success) {
    return (
      <div className="result-card result-error">
        <div className="result-icon">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" />
            <line x1="12" y1="8" x2="12" y2="12" />
            <line x1="12" y1="16" x2="12.01" y2="16" />
          </svg>
        </div>
        <div>
          <div className="result-title">Обработка не удалась</div>
          <div className="dim small">Смотри лог ниже</div>
        </div>
      </div>
    );
  }

  return (
    <div className="result-card result-success">
      <div className="result-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <polyline points="20 6 9 17 4 12" />
        </svg>
      </div>
      <div className="result-info">
        <div className="result-title">Готово</div>
        <div className="result-filename mono small dim">{resultName}</div>
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', marginLeft: 'auto' }}>
        {resultName && (
          <button
            className="btn"
            onClick={() => navigate(`/preview?zip=${encodeURIComponent(resultName)}`)}
            title="Открыть в редакторе превью"
          >
            <Icon name="eye" size={13} /> Preview
          </button>
        )}
        {resultUrl && (
          <a className="btn btn-primary" href={resultUrl} download={resultName ?? undefined}>
            Скачать
          </a>
        )}
      </div>
    </div>
  );
}
