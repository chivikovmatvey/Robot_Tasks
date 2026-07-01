import { Fragment, ReactNode } from 'react';

/*
 * Лёгкий безопасный markdown-рендерер для ответов чата-агента.
 * Без dangerouslySetInnerHTML — строит React-узлы, поэтому XSS невозможен.
 * Поддержка: заголовки, жирный/курсив, инлайн-код, блоки кода,
 * маркированные/нумерованные списки, горизонтальная черта, переносы.
 */

// Инлайн: **bold**, *italic*/_italic_, `code`.
function renderInline(text: string): ReactNode[] {
  const tokens = text.split(/(`[^`]+`|\*\*[^*]+?\*\*|\*[^*\n]+?\*|__[^_]+?__|_[^_\n]+?_)/g);
  return tokens.filter((t) => t !== '').map((tok, i) => {
    if (tok.startsWith('`') && tok.endsWith('`') && tok.length > 1) {
      return (
        <code key={i} style={{ background: 'rgba(127,127,127,0.18)', borderRadius: 4, padding: '1px 4px', fontFamily: 'monospace', fontSize: '0.92em' }}>
          {tok.slice(1, -1)}
        </code>
      );
    }
    if (tok.startsWith('**') && tok.endsWith('**')) {
      return <strong key={i}>{tok.slice(2, -2)}</strong>;
    }
    if (tok.startsWith('__') && tok.endsWith('__')) {
      return <strong key={i}>{tok.slice(2, -2)}</strong>;
    }
    if ((tok.startsWith('*') && tok.endsWith('*')) || (tok.startsWith('_') && tok.endsWith('_'))) {
      return <em key={i}>{tok.slice(1, -1)}</em>;
    }
    return <Fragment key={i}>{tok}</Fragment>;
  });
}

interface Block {
  type: 'h' | 'p' | 'ul' | 'ol' | 'code' | 'hr';
  level?: number;
  lines: string[];
}

function parseBlocks(src: string): Block[] {
  const lines = src.replace(/\r\n/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // Блок кода ```
    if (line.trim().startsWith('```')) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        body.push(lines[i]); i++;
      }
      i++; // закрывающий ```
      blocks.push({ type: 'code', lines: body });
      continue;
    }

    // Пустая строка — разделитель
    if (line.trim() === '') { i++; continue; }

    // Горизонтальная черта
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      blocks.push({ type: 'hr', lines: [] }); i++; continue;
    }

    // Заголовок
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      blocks.push({ type: 'h', level: h[1].length, lines: [h[2]] }); i++; continue;
    }

    // Маркированный список
    if (/^\s*[-*•]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*•]\s+/, '')); i++;
      }
      blocks.push({ type: 'ul', lines: items });
      continue;
    }

    // Нумерованный список
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, '')); i++;
      }
      blocks.push({ type: 'ol', lines: items });
      continue;
    }

    // Параграф (до пустой строки или начала другого блока)
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() !== ''
      && !lines[i].trim().startsWith('```')
      && !/^(#{1,4})\s+/.test(lines[i])
      && !/^\s*[-*•]\s+/.test(lines[i])
      && !/^\s*\d+[.)]\s+/.test(lines[i])) {
      para.push(lines[i]); i++;
    }
    blocks.push({ type: 'p', lines: para });
  }
  return blocks;
}

export function Markdown({ text }: { text: string }) {
  const blocks = parseBlocks(text || '');
  return (
    <div className="md">
      {blocks.map((b, i) => {
        if (b.type === 'h') {
          const size = b.level === 1 ? 16 : b.level === 2 ? 15 : 14;
          return <div key={i} style={{ fontWeight: 700, fontSize: size, margin: i ? '0.5em 0 0.2em' : '0 0 0.2em' }}>{renderInline(b.lines[0])}</div>;
        }
        if (b.type === 'hr') {
          return <hr key={i} style={{ border: 'none', borderTop: '1px solid var(--border, #2a2a2a)', margin: '0.5em 0' }} />;
        }
        if (b.type === 'code') {
          return (
            <pre key={i} style={{ background: '#0d0e12', borderRadius: 6, padding: '0.5rem 0.7rem', overflow: 'auto', margin: '0.4em 0', fontSize: 12 }}>
              <code style={{ fontFamily: 'monospace', whiteSpace: 'pre' }}>{b.lines.join('\n')}</code>
            </pre>
          );
        }
        if (b.type === 'ul' || b.type === 'ol') {
          const Tag = b.type === 'ul' ? 'ul' : 'ol';
          return (
            <Tag key={i} style={{ margin: '0.3em 0', paddingLeft: '1.3em' }}>
              {b.lines.map((it, j) => <li key={j} style={{ margin: '0.15em 0' }}>{renderInline(it)}</li>)}
            </Tag>
          );
        }
        // параграф: одиночные переносы → <br>
        return (
          <p key={i} style={{ margin: i ? '0.4em 0 0' : 0 }}>
            {b.lines.map((ln, j) => (
              <Fragment key={j}>{j > 0 && <br />}{renderInline(ln)}</Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
