import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';

/** Извлекает валидные ID лендов (2–7 цифр) из произвольного текста. */
function parseIds(text: string): string[] {
  return Array.from(new Set(
    (text || '').split(/[\s,;]+/).map((s) => s.trim()).filter((s) => /^\d{2,7}$/.test(s)),
  ));
}

/**
 * Показывает названия офферов-доноров из Keitaro по введённым ID.
 * Дебаунс + кэш на бэке (повторные ID мгновенны). Keitaro медленный/хрупкий —
 * подсказка справочная, не блокирует ввод.
 */
export function OfferNamesHint({ idsText, compact = false }: { idsText: string; compact?: boolean }) {
  const [names, setNames] = useState<Record<string, string | null>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const reqId = useRef(0);

  const ids = parseIds(idsText);
  const key = ids.join(',');

  useEffect(() => {
    if (!ids.length) { setNames({}); setErr(''); return; }
    const myReq = ++reqId.current;
    setLoading(true); setErr('');
    const t = setTimeout(() => {
      api.offerNames(ids)
        .then((r) => {
          if (myReq !== reqId.current) return;  // пришёл устаревший ответ
          setNames((prev) => ({ ...prev, ...(r.names || {}) }));
          if (r.error) setErr(r.error);
        })
        .catch((e) => { if (myReq === reqId.current) setErr(e.message || 'Ошибка'); })
        .finally(() => { if (myReq === reqId.current) setLoading(false); });
    }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (!ids.length) return null;

  return (
    <div style={{ marginTop: 6, display: 'flex', flexDirection: 'column', gap: 3 }}>
      {ids.map((id) => {
        const known = id in names;
        const name = names[id];
        return (
          <div key={id} style={{ fontSize: compact ? 10 : 12, lineHeight: 1.35, display: 'flex', gap: 6, alignItems: 'baseline' }}>
            <code style={{ flexShrink: 0, color: 'var(--accent, #7c6fff)' }}>{id}</code>
            <span style={{
              color: name ? 'var(--text)' : 'var(--text-muted)',
              wordBreak: 'break-word',
            }}>
              {known
                ? (name || '— не найден в Keitaro')
                : (loading ? 'проверяю в Keitaro…' : '…')}
            </span>
          </div>
        );
      })}
      {err && <div className="dim" style={{ fontSize: 10, color: '#f59e0b' }}>{err}</div>}
    </div>
  );
}
