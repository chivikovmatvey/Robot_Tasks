import { LogLine } from '../lib/api';

interface LogViewerProps {
  lines: LogLine[];
  title?: string;
}

export function LogViewer({ lines, title = 'Лог обработки' }: LogViewerProps) {
  if (!lines.length) return null;

  return (
    <div className="log-viewer">
      <div className="log-viewer-header">
        <span className="log-viewer-title">{title}</span>
        <span className="dim small mono">{lines.length} {pluralize(lines.length, 'строка', 'строки', 'строк')}</span>
      </div>
      <div className="log-viewer-body">
        {lines.map((line, i) => (
          <div key={i} className={`log-line log-${line.level}`}>
            {line.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function pluralize(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}
