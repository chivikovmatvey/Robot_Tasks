import { useState, useEffect } from 'react';
import { api } from '../lib/api';

interface ReplacementPair {
  id: number;
  find: string;
  replace: string;
}

interface GeoWordReplacementsProps {
  /** Текущее значение custom_replacements (формат "old => new\n...") */
  value: string;
  onChange: (val: string) => void;
  /** upload_id из scanResult — для автодетекта */
  uploadId?: string;
  /** Целевое ГЕО — для подсказки что подставлять */
  targetGeoId?: string;
  /** Название целевой страны — для плейсхолдера */
  targetCountryName?: string;
}

let _nextId = 1;
function nextId() { return _nextId++; }

/** Парсим строку "old => new\n..." в массив пар */
function parsePairs(raw: string): ReplacementPair[] {
  const pairs: ReplacementPair[] = [];
  for (const line of (raw || '').split('\n')) {
    const s = line.trim();
    if (!s || !s.includes('=>')) continue;
    const [find, ...rest] = s.split('=>');
    pairs.push({ id: nextId(), find: find.trim(), replace: rest.join('=>').trim() });
  }
  return pairs;
}

/** Сериализуем пары обратно в строку */
function serializePairs(pairs: ReplacementPair[]): string {
  return pairs
    .filter(p => p.find.trim())
    .map(p => `${p.find.trim()} => ${p.replace.trim()}`)
    .join('\n');
}

export function GeoWordReplacements({
  value,
  onChange,
  uploadId,
  targetGeoId: _targetGeoId,
  targetCountryName,
}: GeoWordReplacementsProps) {
  const [pairs, setPairs] = useState<ReplacementPair[]>(() => {
    const parsed = parsePairs(value);
    return parsed.length > 0 ? parsed : [{ id: nextId(), find: '', replace: '' }];
  });
  const [detecting, setDetecting] = useState(false);
  const [detectError, setDetectError] = useState('');

  // Синхронизируем пары → value при каждом изменении
  useEffect(() => {
    onChange(serializePairs(pairs));
  }, [pairs]);

  const updatePair = (id: number, field: 'find' | 'replace', val: string) => {
    setPairs(prev => prev.map(p => p.id === id ? { ...p, [field]: val } : p));
  };

  const addPair = () => {
    setPairs(prev => [...prev, { id: nextId(), find: '', replace: '' }]);
  };

  const removePair = (id: number) => {
    setPairs(prev => {
      const next = prev.filter(p => p.id !== id);
      return next.length > 0 ? next : [{ id: nextId(), find: '', replace: '' }];
    });
  };

  const handleDetect = async () => {
    if (!uploadId) return;
    setDetecting(true);
    setDetectError('');
    try {
      const res = await api.geoWords(uploadId);
      if (!res.found_words.length) {
        setDetectError(`Гео-слова исходного ГЕО (${res.source_geo || '?'}) не найдены в тексте`);
        return;
      }
      // Добавляем найденные слова как новые пары с пустым replace
      const newPairs: ReplacementPair[] = res.found_words.map(word => ({
        id: nextId(),
        find: word,
        replace: '',
      }));
      // Убираем пустые строки, добавляем новые
      setPairs(prev => {
        const existing = prev.filter(p => p.find.trim());
        // Не дублируем уже существующие
        const existingFinds = new Set(existing.map(p => p.find));
        const toAdd = newPairs.filter(p => !existingFinds.has(p.find));
        const result = [...existing, ...toAdd];
        return result.length > 0 ? result : [{ id: nextId(), find: '', replace: '' }];
      });
    } catch {
      setDetectError('Ошибка при сканировании');
    } finally {
      setDetecting(false);
    }
  };

  const placeholder_replace = targetCountryName
    ? `напр. ${targetCountryName}`
    : 'на что заменить';

  return (
    <div style={{ marginTop: '2px' }}>
      <div className="image-map-header" style={{ marginBottom: '0.5rem' }}>
        <span className="form-label" style={{ marginBottom: 0 }}>
          Доп. замены в коде{' '}
          <span className="dim" style={{ fontSize: '11px', fontWeight: 400 }}>(необязательно)</span>
        </span>
        {uploadId && (
          <button
            type="button"
            className="btn-link small"
            onClick={handleDetect}
            disabled={detecting}
            title="Найти в HTML упоминания исходного ГЕО и предложить замены"
          >
            {detecting ? 'Сканирую...' : '⚡ Автодетект гео-слов'}
          </button>
        )}
      </div>

      {detectError && (
        <p className="dim small" style={{ marginBottom: '0.5rem', color: 'var(--c-warn, #f59e0b)' }}>
          {detectError}
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
        {pairs.map((pair, idx) => (
          <div key={pair.id} style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <input
              className="form-input mono"
              type="text"
              value={pair.find}
              onChange={e => updatePair(pair.id, 'find', e.target.value)}
              placeholder={idx === 0 ? 'напр. México' : 'что найти'}
              style={{ flex: 1 }}
            />
            <span className="dim" style={{ flexShrink: 0 }}>→</span>
            <input
              className="form-input mono"
              type="text"
              value={pair.replace}
              onChange={e => updatePair(pair.id, 'replace', e.target.value)}
              placeholder={idx === 0 ? placeholder_replace : 'на что заменить'}
              style={{ flex: 1 }}
            />
            <button
              type="button"
              className="btn-link dim"
              onClick={() => removePair(pair.id)}
              title="Удалить"
              style={{ flexShrink: 0, fontSize: '16px', lineHeight: 1 }}
            >
              ×
            </button>
          </div>
        ))}
      </div>

      <div style={{ marginTop: '0.5rem', display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
        <button type="button" className="btn-link small" onClick={addPair}>
          + добавить строку
        </button>
      </div>

      <p className="form-hint dim small" style={{ marginTop: '0.4rem' }}>
        Применяется после стандартной адаптации. Подходит для замены городов, имён и страновых упоминаний.
        Автодетект находит слова исходного ГЕО прямо в HTML оффера.
      </p>
    </div>
  );
}
