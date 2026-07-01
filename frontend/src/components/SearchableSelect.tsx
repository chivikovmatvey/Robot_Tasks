import { useEffect, useMemo, useRef, useState } from 'react';

export interface SearchableOption {
  value: string;
  label: string;
  /** Строка для поиска (уже в lower case или смешанный — нормализуем внутри) */
  keywords: string;
}

interface SearchableSelectProps {
  id?: string;
  options: SearchableOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** Подпись для пустого значения в списке */
  emptyOptionLabel?: string;
  className?: string;
  disabled?: boolean;
}

function norm(s: string) {
  return s.trim().toLowerCase();
}

/**
 * Префиксный поиск по «словам»: код ГЕО, части подписи через ·, слова из keywords.
 * Не используем substring по всей строке — иначе «in» попадает в «Argentina», «Dominicana» и т.д.
 */
function matchesQuery(opt: SearchableOption, rawQuery: string): boolean {
  const q = norm(rawQuery);
  if (!q) return true;
  const parts = q.split(/\s+/).filter(Boolean);

  const id = norm(opt.value);
  const labelSegments = opt.label.split('·').map((s) => norm(s.trim()));
  const kwTokens = norm(opt.keywords)
    .split(/[\s·,:;/|[\]()_-]+/)
    .filter(Boolean);

  const tokenStartsWith = (token: string, part: string) => token.startsWith(part);

  const partMatches = (part: string): boolean => {
    if (id.startsWith(part)) return true;
    for (const seg of labelSegments) {
      if (seg.startsWith(part)) return true;
      for (const w of seg.split(/\s+/).filter(Boolean)) {
        if (tokenStartsWith(w, part)) return true;
      }
    }
    for (const w of kwTokens) {
      if (tokenStartsWith(w, part)) return true;
    }
    return false;
  };

  return parts.every(partMatches);
}

function filterOptions(options: SearchableOption[], query: string): SearchableOption[] {
  if (!norm(query)) return options;
  return options.filter((opt) => matchesQuery(opt, query));
}

export function SearchableSelect({
  id,
  options,
  value,
  onChange,
  placeholder = 'Начни вводить код или название…',
  emptyOptionLabel = '— выбери —',
  className = '',
  disabled = false,
}: SearchableSelectProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [highlight, setHighlight] = useState(0);

  const selected = useMemo(
    () => options.find((o) => o.value === value),
    [options, value],
  );

  const filtered = useMemo(() => filterOptions(options, query), [options, query]);

  const displayValue = open ? query : selected?.label ?? '';

  /** Строки списка: сначала «сброс», затем отфильтрованные */
  const rows = useMemo(() => filtered, [filtered]);
  const maxHighlight = rows.length;

  useEffect(() => {
    if (!open) setQuery('');
  }, [open]);

  useEffect(() => {
    if (open) setHighlight(0);
  }, [query, open, filtered.length]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

  const pick = (v: string) => {
    onChange(v);
    setOpen(false);
    setQuery('');
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (e.key === 'Escape') {
      setOpen(false);
      setQuery('');
      return;
    }
    if (!open && (e.key === 'ArrowDown' || e.key === 'Enter')) {
      setOpen(true);
      return;
    }
    if (!open) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, maxHighlight));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (highlight === 0) pick('');
      else {
        const item = rows[highlight - 1];
        if (item) pick(item.value);
      }
    }
  };

  return (
    <div
      ref={wrapRef}
      className={`searchable-select ${open ? 'searchable-select-open' : ''} ${className}`.trim()}
    >
      <input
        id={id}
        type="text"
        className="form-input"
        disabled={disabled}
        placeholder={placeholder}
        value={displayValue}
        onChange={(e) => {
          const t = e.target.value;
          setQuery(t);
          if (!open) setOpen(true);
        }}
        onFocus={() => {
          if (disabled) return;
          setOpen(true);
          /* Пустое поле — полный список + подсказка «Сейчас: …». Полная подпись в инпуте ломала фильтр. */
          setQuery('');
        }}
        onKeyDown={onKeyDown}
        autoComplete="off"
        spellCheck={false}
        aria-expanded={open}
        aria-haspopup="listbox"
        role="combobox"
      />
      {open && value && selected && query === '' && (
        <div className="searchable-select-current dim small">Сейчас: {selected.label}</div>
      )}
      {open && !disabled && (
        <ul ref={listRef} className="searchable-select-list" role="listbox">
          <li>
            <button
              type="button"
              className={`searchable-select-item ${highlight === 0 ? 'searchable-select-item-highlight' : ''} ${!value ? 'searchable-select-item-active' : ''}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick('')}
              onMouseEnter={() => setHighlight(0)}
            >
              {emptyOptionLabel}
            </button>
          </li>
          {rows.map((opt, i) => (
            <li key={opt.value}>
              <button
                type="button"
                className={`searchable-select-item ${i + 1 === highlight ? 'searchable-select-item-highlight' : ''} ${opt.value === value ? 'searchable-select-item-active' : ''}`}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(opt.value)}
                onMouseEnter={() => setHighlight(i + 1)}
              >
                {opt.label}
              </button>
            </li>
          ))}
          {rows.length === 0 && query.trim() !== '' && (
            <li className="searchable-select-empty">Ничего не найдено</li>
          )}
        </ul>
      )}
    </div>
  );
}

export function geoOptions(geos: import('../lib/api').Geo[]): SearchableOption[] {
  return geos.map((g) => ({
    value: g.id,
    label: `${g.id} · ${g.country_name} · ${g.currency}`,
    /* Без lang / lang_html: иначе «ES» совпадает с lang=ES у AR, MX, CO… вместо кода ES (España) */
    keywords: [g.id, g.country_name, g.currency].filter(Boolean).join(' '),
  }));
}

export function verticalOptions(verticals: import('../lib/api').Vertical[]): SearchableOption[] {
  return verticals.map((v) => ({
    value: v.id,
    label: `${v.label} (${v.exclude_word.trim()})`,
    keywords: [v.id, v.label, v.exclude_word].join(' '),
  }));
}
