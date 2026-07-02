import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import CodeMirror, { type ReactCodeMirrorRef } from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { openSearchPanel } from '@codemirror/search';
import { oneDark } from '@codemirror/theme-one-dark';
import { html } from '@codemirror/lang-html';
import { css as cssLang } from '@codemirror/lang-css';
import { javascript } from '@codemirror/lang-javascript';
import { php } from '@codemirror/lang-php';
import { json as jsonLang } from '@codemirror/lang-json';
import { Icon } from './Icon';

// ============================================================================
// Редактор кода ленда: превью с пикером блоков (клик по блоку → переход к
// строке кода), CodeMirror с подсветкой/автодополнением, панель структуры
// и применённых CSS-правил с live-редактированием и записью в файл.
// Работает и с адаптированным zip (outputs), и с исходником (session__sid__lid).
// ============================================================================

const TEXT_EXTS = new Set(['php', 'html', 'htm', 'css', 'js', 'json', 'txt', 'xml']);

// Русские подписи панели поиска/замены CodeMirror (Ctrl+F).
const RU_PHRASES = EditorState.phrases.of({
  'Find': 'Найти',
  'Replace': 'Заменить',
  'next': 'след.',
  'previous': 'пред.',
  'all': 'все',
  'match case': 'регистр',
  'by word': 'слово целиком',
  'regexp': 'regexp',
  'replace': 'заменить',
  'replace all': 'заменить все',
  'close': 'закрыть',
  'current match': 'текущее совпадение',
  'replaced $ matches': 'заменено: $',
  'replaced match on line $': 'заменено на строке $',
  'on line': 'на строке',
});

function extOf(path: string): string {
  return (path.split('.').pop() || '').toLowerCase();
}

function langFor(path: string) {
  switch (extOf(path)) {
    case 'php': return [php()];
    case 'html': case 'htm': return [html()];
    case 'css': return [cssLang()];
    case 'js': return [javascript()];
    case 'json': return [jsonLang()];
    default: return [];
  }
}

/** Точка входа превью — повторяет логику бэкенда (_entry_path). */
function entryOf(files: string[]): string {
  for (const cand of ['index.php', 'index.html', 'index.htm']) {
    if (files.includes(cand)) return cand;
  }
  const root = files.find((f) => !f.includes('/') && ['php', 'html', 'htm'].includes(extOf(f)));
  return root || files[0] || 'index.php';
}

/** Внутренний путь css-файла из href таблицы стилей (…/file?path=assets%2Fx.css). */
function innerPathOf(href: string | null): string | null {
  if (!href) return null;
  try { return new URL(href, window.location.origin).searchParams.get('path'); } catch { return null; }
}

/** Гибкий regex по селектору: терпит различия в пробелах между файлом и CSSOM. */
function selectorPattern(sel: string): string {
  return sel.trim()
    .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    .replace(/\s*,\s*/g, '\\s*,\\s*')
    .replace(/\s+/g, '\\s+');
}

/** Заменяет тело единственного вхождения правила `selector { … }` в css-тексте.
 *  null — не нашли ровно одно вхождение (пусть правит руками). */
function patchCssRule(text: string, selector: string, decls: string): string | null {
  let re: RegExp;
  try {
    re = new RegExp('(?:^|[};{]|\\*\\/)\\s*' + selectorPattern(selector) + '\\s*\\{', 'gi');
  } catch { return null; }
  const ms = [...text.matchAll(re)];
  if (ms.length !== 1) return null;
  const open = (ms[0].index as number) + ms[0][0].length;
  const close = text.indexOf('}', open);
  if (close === -1) return null;
  const parts = decls.split(';').map((s) => s.trim()).filter(Boolean);
  const body = parts.length ? '\n  ' + parts.join(';\n  ') + ';\n' : '\n';
  return text.slice(0, open) + body + text.slice(close);
}

/** Пишет style="…" в открывающий тег по точной позиции (line/col из data-src-*). */
function patchInlineStyle(text: string, line: number, col: number, tag: string, styleVal: string): string | null {
  const lines = text.split('\n');
  if (line < 1 || line > lines.length) return null;
  const l = lines[line - 1];
  const idx = Math.max(0, col - 1);
  if (!l.slice(idx).toLowerCase().startsWith('<' + tag.toLowerCase())) return null;
  const gt = l.indexOf('>', idx);
  if (gt === -1) return null; // тег растянут на несколько строк — не рискуем
  let tagStr = l.slice(idx, gt);
  const val = styleVal.trim().replace(/"/g, "'");
  const styleRe = /\s?style\s*=\s*(["'])[^"']*\1/i;
  if (styleRe.test(tagStr)) {
    tagStr = tagStr.replace(styleRe, val ? ` style="${val}"` : '');
  } else if (val) {
    if (tagStr.endsWith('/')) tagStr = tagStr.slice(0, -1).trimEnd() + ` style="${val}"/`;
    else tagStr = tagStr + ` style="${val}"`;
  }
  lines[line - 1] = l.slice(0, idx) + tagStr + l.slice(gt);
  return lines.join('\n');
}

// ---------------------------------------------------------------------------

interface Crumb { tag: string; id: string; cls: string[] }
interface RuleView { id: number; selector: string; media?: string; innerPath: string | null; decls: string }
interface Picked {
  tag: string; id: string; cls: string[];
  line: number | null; col: number | null;
  w: number; h: number;
  crumbs: Crumb[];
  kids: Crumb[];
  rules: RuleView[];
  inline: string;
}
interface Jump { line?: number; col?: number; needle?: string }

/** Тема сайта из data-theme на <html> — чтобы редактор кода совпадал по цвету. */
function useSiteTheme(): 'dark' | 'light' {
  const [t, setT] = useState<'dark' | 'light'>(
    () => (document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'));
  useEffect(() => {
    const mo = new MutationObserver(() =>
      setT(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark'));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => mo.disconnect();
  }, []);
  return t;
}

function crumbOf(el: Element): Crumb {
  return {
    tag: el.tagName.toLowerCase(),
    id: (el as HTMLElement).id || '',
    cls: Array.from(el.classList || []).filter((c) => c !== 'undefined'),
  };
}

function crumbLabel(c: Crumb): string {
  return c.tag + (c.id ? `#${c.id}` : '') + (c.cls.length ? '.' + c.cls.slice(0, 2).join('.') : '');
}

/** Собирает CSS-правила, применённые к элементу (включая @media, без пседвоклассов). */
function collectRules(el: Element, doc: Document): { rules: RuleView[]; refs: Map<number, CSSStyleRule> } {
  const rules: RuleView[] = [];
  const refs = new Map<number, CSSStyleRule>();
  let idc = 0;
  const walk = (list: CSSRuleList, innerPath: string | null, media?: string) => {
    for (const r of Array.from(list)) {
      const anyR = r as any;
      if (anyR.selectorText !== undefined) {
        let matched = false;
        try { matched = el.matches(anyR.selectorText); } catch { /* невалидный селектор */ }
        if (!matched) {
          // пробуем без псевдоклассов/элементов (:hover, ::before…)
          const bases = String(anyR.selectorText).split(',')
            .map((s) => s.replace(/::?[a-zA-Z-]+(\([^)]*\))?/g, '').trim())
            .filter(Boolean);
          for (const b of bases) {
            try { if (el.matches(b)) { matched = true; break; } } catch { /* ignore */ }
          }
        }
        if (matched) {
          const id = idc++;
          refs.set(id, anyR as CSSStyleRule);
          rules.push({ id, selector: anyR.selectorText, media, innerPath, decls: anyR.style.cssText });
        }
      } else if (anyR.cssRules) {
        walk(anyR.cssRules, innerPath, anyR.conditionText || media);
      }
    }
  };
  for (const sheet of Array.from(doc.styleSheets)) {
    let list: CSSRuleList;
    try { list = (sheet as CSSStyleSheet).cssRules; } catch { continue; } // сторонний origin
    walk(list, innerPathOf((sheet as CSSStyleSheet).href));
  }
  return { rules, refs };
}

// ---------------------------------------------------------------------------

export function LanderEditor({ zipName }: { zipName: string }) {
  const [ver, setVer] = useState(0);
  const [files, setFiles] = useState<string[]>([]);
  const [buffers, setBuffers] = useState<Record<string, { text: string; saved: string }>>({});
  const [activePath, setActivePath] = useState('');
  const [pickerOn, setPickerOn] = useState(false);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [ruleEdits, setRuleEdits] = useState<Record<number, string>>({});
  const [inlineEdit, setInlineEdit] = useState<string>('');
  const [msg, setMsg] = useState('');
  const [saving, setSaving] = useState(false);
  const [splitPct, setSplitPct] = useState(44);
  const [panelH, setPanelH] = useState(42); // % высоты правой колонки под панель стилей

  const cmRef = useRef<ReactCodeMirrorRef>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const ruleRefs = useRef<Map<number, CSSStyleRule>>(new Map());
  const pickedElRef = useRef<Element | null>(null);
  const crumbEls = useRef<Element[]>([]);
  const kidEls = useRef<Element[]>([]);
  const hoverPrev = useRef<{ el: HTMLElement; outline: string; offset: string } | null>(null);
  const pendingJump = useRef<Jump | null>(null);
  const lastPickPos = useRef<{ line: number; col: number } | null>(null);
  const pickerCleanup = useRef<(() => void) | null>(null);
  const msgTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const siteTheme = useSiteTheme();
  const entryFile = useMemo(() => entryOf(files), [files]);
  const previewUrl = `/api/preview/${encodeURIComponent(zipName)}/render?path=index.php&edit=1&v=${ver}`;
  const buf = buffers[activePath];
  const dirtyPaths = useMemo(
    () => Object.keys(buffers).filter((p) => buffers[p].text !== buffers[p].saved),
    [buffers],
  );

  const flash = useCallback((m: string) => {
    setMsg(m);
    if (msgTimer.current) clearTimeout(msgTimer.current);
    msgTimer.current = setTimeout(() => setMsg(''), 3500);
  }, []);

  // ---- файлы ----------------------------------------------------------------
  useEffect(() => {
    let dead = false;
    fetch(`/api/preview/${encodeURIComponent(zipName)}/files`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((list: { path: string }[]) => {
        if (dead) return;
        const txt = list.map((f) => f.path).filter((p) => TEXT_EXTS.has(extOf(p)));
        setFiles(txt);
      })
      .catch((e) => !dead && flash(`Список файлов: ${e.message}`));
    return () => { dead = true; };
  }, [zipName, flash]);

  const fetchFile = useCallback(async (path: string): Promise<string> => {
    const r = await fetch(`/api/preview/${encodeURIComponent(zipName)}/file?path=${encodeURIComponent(path)}&raw=1`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.text();
  }, [zipName]);

  const openFile = useCallback(async (path: string) => {
    if (!buffers[path]) {
      try {
        const text = await fetchFile(path);
        setBuffers((b) => (b[path] ? b : { ...b, [path]: { text, saved: text } }));
      } catch (e: any) {
        flash(`Не открыл ${path}: ${e.message}`);
        return;
      }
    }
    setActivePath(path);
  }, [buffers, fetchFile, flash]);

  // авто-открытие точки входа
  useEffect(() => {
    if (files.length && !activePath) void openFile(entryFile);
  }, [files, activePath, entryFile, openFile]);

  // ---- сохранение -----------------------------------------------------------
  const saveFile = useCallback(async (path: string, content: string) => {
    setSaving(true);
    try {
      const r = await fetch(`/api/preview/${encodeURIComponent(zipName)}/file`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, content }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => null);
        throw new Error(d?.detail || `HTTP ${r.status}`);
      }
      setBuffers((b) => ({ ...b, [path]: { text: content, saved: content } }));
      setVer((v) => v + 1); // перезагрузит превью (и разметку data-src-line)
      flash(`✓ Сохранено: ${path}`);
    } catch (e: any) {
      flash(`Ошибка сохранения: ${e.message}`);
    } finally {
      setSaving(false);
    }
  }, [zipName, flash]);

  const saveActive = useCallback(() => {
    if (activePath && buf && buf.text !== buf.saved) void saveFile(activePath, buf.text);
  }, [activePath, buf, saveFile]);

  // Ctrl/Cmd+S
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      saveActive();
    }
  }, [saveActive]);

  // ---- переход к коду ---------------------------------------------------------
  const applyJump = useCallback(() => {
    const j = pendingJump.current;
    const view = cmRef.current?.view;
    if (!j || !view) return;
    pendingJump.current = null;
    const doc = view.state.doc;
    let anchor = 0; let head = 0;
    if (j.line && j.line >= 1 && j.line <= doc.lines) {
      const l = doc.line(j.line);
      anchor = j.col ? Math.min(l.from + j.col - 1, l.to) : l.from;
      head = l.to; // выделяем до конца строки — видно, куда попали
    } else if (j.needle) {
      try {
        const re = new RegExp(selectorPattern(j.needle) + '\\s*\\{', 'i');
        const m = re.exec(doc.toString());
        if (m) { anchor = m.index; head = m.index + m[0].length; }
      } catch { /* ignore */ }
    }
    view.dispatch({
      selection: { anchor, head },
      effects: EditorView.scrollIntoView(anchor, { y: 'center' }),
    });
    view.focus();
  }, []);

  const openAt = useCallback((path: string, jump: Jump) => {
    pendingJump.current = jump;
    if (activePath === path) {
      setTimeout(applyJump, 30);
    } else {
      void openFile(path);
    }
  }, [activePath, applyJump, openFile]);

  // применяем отложенный переход после смены файла (CodeMirror уже получил value)
  useEffect(() => {
    if (!pendingJump.current) return;
    const t = setTimeout(applyJump, 80);
    return () => clearTimeout(t);
  }, [activePath, applyJump]);

  // ---- пикер блоков -----------------------------------------------------------
  const clearHover = useCallback(() => {
    const h = hoverPrev.current;
    if (h) {
      h.el.style.outline = h.outline;
      h.el.style.outlineOffset = h.offset;
      hoverPrev.current = null;
    }
  }, []);

  const doPick = useCallback((rawEl: Element, jump = true) => {
    const doc = frameRef.current?.contentDocument;
    if (!doc) return;
    const el = (rawEl.closest('[data-src-line]') || rawEl) as HTMLElement;
    pickedElRef.current = el;
    const line = parseInt(el.getAttribute('data-src-line') || '', 10) || null;
    const col = parseInt(el.getAttribute('data-src-col') || '', 10) || null;
    if (line) lastPickPos.current = { line, col: col || 1 };

    // хлебные крошки: от корня до элемента
    const crumbs: Crumb[] = []; const els: Element[] = [];
    let cur: Element | null = el;
    while (cur && cur.tagName.toLowerCase() !== 'html') {
      crumbs.unshift(crumbOf(cur)); els.unshift(cur);
      cur = cur.parentElement;
    }
    crumbEls.current = els;
    kidEls.current = Array.from(el.children);

    const { rules, refs } = collectRules(el, doc);
    ruleRefs.current = refs;
    const rect = el.getBoundingClientRect();
    setRuleEdits({});
    setInlineEdit(el.getAttribute('style') || '');
    setPicked({
      ...crumbOf(el), line, col,
      w: Math.round(rect.width), h: Math.round(rect.height),
      crumbs, kids: kidEls.current.map(crumbOf), rules,
      inline: el.getAttribute('style') || '',
    });
    if (line && jump) openAt(entryFile, { line, col: col || undefined });
  }, [entryFile, openAt]);

  const detachPicker = useCallback(() => {
    pickerCleanup.current?.();
    pickerCleanup.current = null;
    clearHover();
  }, [clearHover]);

  const attachPicker = useCallback(() => {
    detachPicker();
    const doc = frameRef.current?.contentDocument;
    if (!doc || !doc.body) return;
    const over = (e: Event) => {
      const t = e.target as HTMLElement;
      if (!t || !t.style) return; // текстовые узлы/не-HTML (svg без style и т.п.)
      clearHover();
      hoverPrev.current = { el: t, outline: t.style.outline, offset: t.style.outlineOffset };
      t.style.outline = '2px solid #7c6fff';
      t.style.outlineOffset = '-2px';
    };
    const click = (e: Event) => {
      e.preventDefault(); e.stopPropagation();
      clearHover();
      doPick(e.target as Element);
      setPickerOn(false); // как в devtools: выбрал — пикер выключился
    };
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') setPickerOn(false); };
    doc.addEventListener('mouseover', over, true);
    doc.addEventListener('click', click, true);
    doc.addEventListener('keydown', key, true);
    doc.body.style.cursor = 'crosshair';
    pickerCleanup.current = () => {
      doc.removeEventListener('mouseover', over, true);
      doc.removeEventListener('click', click, true);
      doc.removeEventListener('keydown', key, true);
      try { doc.body.style.cursor = ''; } catch { /* iframe мог перезагрузиться */ }
    };
  }, [detachPicker, clearHover, doPick]);

  useEffect(() => {
    if (pickerOn) attachPicker(); else detachPicker();
    return detachPicker;
  }, [pickerOn, attachPicker, detachPicker]);

  // после перезагрузки iframe: перевесить пикер, восстановить выбор по строке
  const onFrameLoad = useCallback(() => {
    if (pickerOn) attachPicker();
    const pos = lastPickPos.current;
    const doc = frameRef.current?.contentDocument;
    if (pos && doc) {
      const el = doc.querySelector(`[data-src-line="${pos.line}"][data-src-col="${pos.col}"]`)
        || doc.querySelector(`[data-src-line="${pos.line}"]`);
      if (el) {
        doPick(el, false); // тихий ре-пик: обновить ссылки/правила без прыжка в код
      } else {
        setPicked(null);
        pickedElRef.current = null;
      }
    }
  }, [pickerOn, attachPicker, doPick]);

  // ---- редактирование стилей ----------------------------------------------------
  const applyRuleLive = useCallback((id: number, decls: string) => {
    setRuleEdits((m) => ({ ...m, [id]: decls }));
    const r = ruleRefs.current.get(id);
    if (r) { try { r.style.cssText = decls; } catch { /* невалидный css — ждём дальше */ } }
  }, []);

  const saveRuleToFile = useCallback(async (rv: RuleView) => {
    const path = rv.innerPath ?? entryFile;
    const decls = ruleEdits[rv.id] ?? rv.decls;
    let text: string;
    try { text = buffers[path]?.text ?? await fetchFile(path); }
    catch (e: any) { flash(`Не прочитал ${path}: ${e.message}`); return; }
    const patched = patchCssRule(text, rv.selector, decls);
    if (patched === null) {
      // не нашли ровно одно вхождение — открываем файл на селекторе
      setBuffers((b) => (b[path] ? b : { ...b, [path]: { text, saved: text } }));
      openAt(path, { needle: rv.selector });
      flash('Не нашёл однозначное правило в файле — правь в коде (курсор на селекторе)');
      return;
    }
    setBuffers((b) => ({ ...b, [path]: { text: patched, saved: b[path]?.saved ?? text } }));
    await saveFile(path, patched);
  }, [entryFile, ruleEdits, buffers, fetchFile, flash, openAt, saveFile]);

  const applyInlineLive = useCallback((v: string) => {
    setInlineEdit(v);
    const el = pickedElRef.current as HTMLElement | null;
    if (el) { try { el.setAttribute('style', v); } catch { /* ignore */ } }
  }, []);

  const saveInlineToFile = useCallback(async () => {
    if (!picked?.line || !picked.col) { flash('Нет привязки к строке — правь в коде'); return; }
    const path = entryFile;
    let text: string;
    try { text = buffers[path]?.text ?? await fetchFile(path); }
    catch (e: any) { flash(`Не прочитал ${path}: ${e.message}`); return; }
    const patched = patchInlineStyle(text, picked.line, picked.col, picked.tag, inlineEdit);
    if (patched === null) {
      openAt(path, { line: picked.line, col: picked.col });
      flash('Не смог вписать style автоматически — правь в коде');
      return;
    }
    setBuffers((b) => ({ ...b, [path]: { text: patched, saved: b[path]?.saved ?? text } }));
    await saveFile(path, patched);
  }, [picked, entryFile, buffers, inlineEdit, fetchFile, flash, openAt, saveFile]);

  const pickByEl = useCallback((el: Element | undefined) => { if (el) doPick(el); }, [doPick]);

  // ---- ресайз сплита -----------------------------------------------------------
  const startSplit = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    const root = rootRef.current;
    if (!root) return;
    const move = (ev: MouseEvent) => {
      const r = root.getBoundingClientRect();
      setSplitPct(Math.min(75, Math.max(20, ((ev.clientX - r.left) / r.width) * 100)));
    };
    const up = () => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
    };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  }, []);

  const extensions = useMemo(
    () => [...langFor(activePath), EditorView.lineWrapping, RU_PHRASES],
    [activePath]);

  const showSearch = useCallback(() => {
    const view = cmRef.current?.view;
    if (view) { openSearchPanel(view); view.focus(); }
  }, []);

  // ---- UI ----------------------------------------------------------------------
  const border = '1px solid var(--border, #2a2a2a)';
  return (
    <div ref={rootRef} onKeyDown={onKeyDown}
         style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
      {/* тулбар */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, flexWrap: 'wrap' }}>
        <button className="btn" onClick={() => setPickerOn((v) => !v)}
                style={{ fontSize: 12, background: pickerOn ? 'var(--accent)' : undefined, color: pickerOn ? '#fff' : undefined }}
                title="Клик по блоку на ленде откроет его код (Esc — отмена)">
          <Icon name="crosshair" size={13} /> {pickerOn ? 'Кликни блок на ленде…' : 'Выбрать блок'}
        </button>
        <select className="form-input" value={activePath}
                onChange={(e) => void openFile(e.target.value)}
                style={{ fontSize: 12, padding: '3px 6px', width: 'auto', maxWidth: 300, fontFamily: 'monospace' }}>
          {!activePath && <option value="">— файл —</option>}
          {files.map((f) => (
            <option key={f} value={f}>{buffers[f] && buffers[f].text !== buffers[f].saved ? '● ' : ''}{f}</option>
          ))}
        </select>
        <button className="btn" onClick={showSearch} disabled={!buf} style={{ fontSize: 12 }}
                title="Поиск и замена в коде (Ctrl+F)">
          <Icon name="search" size={13} /> Поиск
        </button>
        <button className="btn" onClick={saveActive} disabled={saving || !buf || buf.text === buf.saved}
                style={{ fontSize: 12 }} title="Ctrl+S">
          {saving ? 'Сохраняю…' : buf && buf.text !== buf.saved ? '● Сохранить' : 'Сохранено'}
        </button>
        {dirtyPaths.length > 0 && (
          <span className="dim small" title={dirtyPaths.join('\n')}>несохранённых: {dirtyPaths.length}</span>
        )}
        <div style={{ flex: 1 }} />
        {msg && <span className="small" style={{ color: msg.startsWith('✓') ? '#4ade80' : '#f59e0b' }}>{msg}</span>}
        <span className="dim small" title="Повторная адаптация пересобирает файлы ленда из исходника и перезапишет ручные правки кода"><Icon name="alert" size={12} /> адаптация перетирает правки</span>
      </div>

      {/* превью | код+стили */}
      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {/* превью */}
        <div style={{ width: `${splitPct}%`, minWidth: 0, border, borderRadius: 8, overflow: 'hidden', background: '#fff' }}>
          <iframe
            ref={frameRef}
            key={`${zipName}-${ver}`}
            src={previewUrl}
            onLoad={onFrameLoad}
            style={{ width: '100%', height: '100%', border: 'none', display: 'block' }}
            title="lander-editor-preview"
          />
        </div>
        {/* ручка */}
        <div onMouseDown={startSplit} title="Тяни, чтобы менять пропорции"
             style={{ width: 10, cursor: 'ew-resize', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <div style={{ width: 3, height: 42, borderRadius: 3, background: 'var(--accent)', opacity: 0.6 }} />
        </div>
        {/* правая колонка: код + панель стилей */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ flex: `1 1 ${100 - (picked ? panelH : 0)}%`, minHeight: 0, border, borderRadius: 8, overflow: 'hidden' }}>
            {buf ? (
              <CodeMirror
                ref={cmRef}
                value={buf.text}
                height="100%"
                style={{ height: '100%', fontSize: 12.5 }}
                theme={siteTheme === 'dark' ? oneDark : 'light'}
                extensions={extensions}
                onChange={(v) => setBuffers((b) => ({ ...b, [activePath]: { ...b[activePath], text: v } }))}
              />
            ) : (
              <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <span className="dim small">Открой файл или кликни блок на ленде (<Icon name="crosshair" size={12} />)</span>
              </div>
            )}
          </div>

          {/* панель структуры и стилей выбранного блока */}
          {picked && (
            <div style={{ flex: `0 0 ${panelH}%`, minHeight: 120, border, borderRadius: 8, overflow: 'hidden', display: 'flex', flexDirection: 'column', background: 'var(--bg-elevated, #141414)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 10px', borderBottom: border, flexShrink: 0 }}>
                <span style={{ fontFamily: 'monospace', fontSize: 12, color: 'var(--accent, #7c6fff)' }}>
                  {crumbLabel(picked)}
                </span>
                <span className="dim small">{picked.w}×{picked.h}px{picked.line ? ` · строка ${picked.line}` : ''}</span>
                {picked.line && (
                  <button className="btn" style={{ fontSize: 11 }}
                          onClick={() => openAt(entryFile, { line: picked.line!, col: picked.col || undefined })}>
                    → код
                  </button>
                )}
                <div style={{ flex: 1 }} />
                <button className="btn" style={{ fontSize: 11 }} title="Высота панели"
                        onClick={() => setPanelH((h) => (h >= 60 ? 42 : h + 18))}>⇕</button>
                <button className="btn" style={{ fontSize: 11 }} onClick={() => { setPicked(null); pickedElRef.current = null; lastPickPos.current = null; }}>✕</button>
              </div>

              <div style={{ flex: 1, overflowY: 'auto', padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 8 }}>
                {/* структура: крошки + дети */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center', flexShrink: 0 }}>
                  {picked.crumbs.map((c, i) => (
                    <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                      {i > 0 && <span className="dim small">›</span>}
                      <button onClick={() => pickByEl(crumbEls.current[i])}
                              style={{ fontFamily: 'monospace', fontSize: 11, padding: '1px 6px', borderRadius: 4, cursor: 'pointer',
                                       border, background: i === picked.crumbs.length - 1 ? 'var(--accent)' : 'transparent',
                                       color: i === picked.crumbs.length - 1 ? '#fff' : 'var(--text-muted)' }}>
                        {crumbLabel(c)}
                      </button>
                    </span>
                  ))}
                </div>
                {picked.kids.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center', flexShrink: 0 }}>
                    <span className="dim small">внутри:</span>
                    {picked.kids.slice(0, 14).map((c, i) => (
                      <button key={i} onClick={() => pickByEl(kidEls.current[i])}
                              style={{ fontFamily: 'monospace', fontSize: 11, padding: '1px 6px', borderRadius: 4, cursor: 'pointer', border, background: 'transparent', color: 'var(--text-muted)' }}>
                        {crumbLabel(c)}
                      </button>
                    ))}
                    {picked.kids.length > 14 && <span className="dim small">+{picked.kids.length - 14}</span>}
                  </div>
                )}

                {/* инлайн-стиль */}
                <StyleCard
                  title="element.style (инлайн)"
                  source={picked.line ? `${entryFile}:${picked.line}` : entryFile}
                  value={inlineEdit}
                  onChange={applyInlineLive}
                  onSave={() => void saveInlineToFile()}
                  onJump={picked.line ? () => openAt(entryFile, { line: picked.line!, col: picked.col || undefined }) : undefined}
                  placeholder="color: red; margin: 0 …"
                />

                {/* правила из css */}
                {picked.rules.map((rv) => (
                  <StyleCard
                    key={rv.id}
                    title={rv.selector}
                    media={rv.media}
                    source={rv.innerPath ?? `${entryFile} (в <style>)`}
                    value={ruleEdits[rv.id] ?? rv.decls}
                    onChange={(v) => applyRuleLive(rv.id, v)}
                    onSave={() => void saveRuleToFile(rv)}
                    onJump={() => openAt(rv.innerPath ?? entryFile, { needle: rv.selector })}
                  />
                ))}
                {picked.rules.length === 0 && (
                  <span className="dim small">CSS-правил для блока не найдено (только инлайн/наследуемые).</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Карточка одного css-правила: live-редактирование + запись в файл + переход к коду.
function StyleCard({ title, media, source, value, onChange, onSave, onJump, placeholder }: {
  title: string; media?: string; source: string; value: string;
  onChange: (v: string) => void; onSave: () => void; onJump?: () => void; placeholder?: string;
}) {
  const border = '1px solid var(--border, #2a2a2a)';
  const lines = Math.min(8, Math.max(2, value.split(';').filter((s) => s.trim()).length + 1));
  return (
    // flexShrink: 0 — карточки лежат в скролл-колонке, без этого сжимаются друг в друга
    <div style={{ border, borderRadius: 6, overflow: 'hidden', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '3px 8px', background: 'var(--bg, #0d0e12)', borderBottom: border }}>
        <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#38bdf8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={title}>
          {title}
        </span>
        {media && <span className="dim small" style={{ fontSize: 10 }} title={`@media ${media}`}>@{media.length > 24 ? media.slice(0, 24) + '…' : media}</span>}
        <div style={{ flex: 1 }} />
        <span className="dim small" style={{ fontSize: 10, whiteSpace: 'nowrap' }}>{source}</span>
        {onJump && <button className="btn" style={{ fontSize: 10, padding: '1px 6px' }} onClick={onJump} title="Показать в коде">→ код</button>}
        <button className="btn" style={{ fontSize: 10, padding: '1px 6px' }} onClick={onSave} title="Записать изменения в файл и сохранить"><Icon name="save" size={11} /></button>
      </div>
      <textarea
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        rows={lines}
        spellCheck={false}
        style={{ display: 'block', width: '100%', resize: 'vertical', border: 'none', outline: 'none',
                 background: 'transparent', color: 'var(--text, #ddd)', fontFamily: 'monospace', fontSize: 11.5,
                 padding: '6px 8px', lineHeight: 1.5 }}
      />
    </div>
  );
}
