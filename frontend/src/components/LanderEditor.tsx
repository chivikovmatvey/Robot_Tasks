import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import CodeMirror, { type ReactCodeMirrorRef } from '@uiw/react-codemirror';
import { EditorView } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { SearchQuery, setSearchQuery, findNext, findPrevious, replaceNext, replaceAll, search } from '@codemirror/search';
import { api } from '../lib/api';
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

// blink — CSS из сохранённых Chrome-ом страниц (mhtml), правится как css
const TEXT_EXTS = new Set(['php', 'html', 'htm', 'css', 'js', 'json', 'txt', 'xml', 'blink']);

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
    case 'css': case 'blink': return [cssLang()];
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

/** CSSOM отдаёт url(...) переписанными на /api/preview/... — перед сохранением
 *  в файл возвращаем пути внутри архива (относительно папки css-файла). */
function unrewriteUrls(decls: string, cssPath: string | null): string {
  return decls.replace(/url\(\s*(['"]?)([^)'"]+)\1\s*\)/gi, (m, _q, u) => {
    const inner = innerPathOf(u);
    if (!inner) return m; // не наш api-путь (data:, http, относительный) — не трогаем
    const dir = cssPath && cssPath.includes('/') ? cssPath.slice(0, cssPath.lastIndexOf('/')) : '';
    let rel = inner;
    if (dir && inner.startsWith(dir + '/')) rel = inner.slice(dir.length + 1);
    else if (dir) rel = dir.split('/').map(() => '..').join('/') + '/' + inner;
    return `url("${rel}")`;
  });
}

/** Дописывает override-правило: css-файл — в конец (каскад побеждает),
 *  html/php — в блок <style data-ws-edits> (создаётся перед </head>). */
function appendOverrideRule(text: string, path: string, rule: string): string {
  if (extOf(path) === 'css' || extOf(path) === 'blink') {
    return text.replace(/\s*$/, '') + `\n\n/* ws-edit */\n${rule}\n`;
  }
  const wsIdx = text.indexOf('<style data-ws-edits>');
  if (wsIdx !== -1) {
    const close = text.indexOf('</style>', wsIdx);
    if (close !== -1) return text.slice(0, close) + rule + '\n' + text.slice(close);
  }
  const block = `<style data-ws-edits>\n${rule}\n</style>`;
  const m = /<\/head>/i.exec(text) || /<\/body>/i.exec(text);
  return m ? text.slice(0, m.index) + block + '\n' + text.slice(m.index)
           : text + '\n' + block;
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

export function LanderEditor({ zipName, contentVersion = 0 }: {
  zipName: string;
  /** Бампается снаружи, когда архив ленда изменили НЕ из редактора (перевод,
   *  адаптация, нейро-правка, откат версии) — редактор перечитывает файлы с
   *  диска, иначе показывал бы свои старые буферы (кейс: после перевода в
   *  коде оставался дореводный текст). */
  contentVersion?: number;
}) {
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
  // contentVersion в зависимостях: после перевода/адаптации состав файлов мог
  // измениться (переименования html→php, новые ассеты).
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
  }, [zipName, flash, contentVersion]);

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

  // ---- перечитывание с диска ------------------------------------------------
  // Буферы в ref — чтобы reloadFromDisk не пересоздавался на каждый набранный
  // символ (иначе эффект перезагрузки срабатывал бы во время печати).
  const buffersRef = useRef(buffers);
  buffersRef.current = buffers;

  /** Перечитывает открытые файлы из архива. Несохранённые правки НЕ затирает —
   *  такие буферы остаются как есть, о чём сообщаем. */
  const reloadFromDisk = useCallback(async (note?: string) => {
    const paths = Object.keys(buffersRef.current);
    const dirty: string[] = [];
    const fresh: Record<string, { text: string; saved: string }> = {};
    const gone: string[] = [];
    for (const p of paths) {
      const b = buffersRef.current[p];
      if (b.text !== b.saved) { dirty.push(p); continue; }
      try {
        const text = await fetchFile(p);
        fresh[p] = { text, saved: text };
      } catch {
        gone.push(p);   // файл исчез (переадаптация/переименование)
      }
    }
    setBuffers((b) => {
      const next = { ...b, ...fresh };
      for (const p of gone) delete next[p];
      return next;
    });
    setVer((v) => v + 1);   // перерисовать превью
    if (dirty.length) flash(`Обновлено с диска; несохранённые правки сохранены только у: ${dirty.join(', ')}`);
    else flash(note || '✓ Файлы перечитаны из архива');
  }, [fetchFile, flash]);

  // Архив изменили снаружи (перевод / адаптация / нейро / откат версии) —
  // подтягиваем актуальный код, иначе в редакторе висел бы старый текст.
  const seenContentVer = useRef(contentVersion);
  useEffect(() => {
    if (contentVersion === seenContentVer.current) return;
    seenContentVer.current = contentVersion;
    void reloadFromDisk('✓ Ленд изменился — код обновлён');
  }, [contentVersion, reloadFromDisk]);

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

  // Ctrl/Cmd+S — сохранить, Ctrl/Cmd+F — наша панель поиска (не браузерная).
  const onKeyDown = useCallback((e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
      e.preventDefault();
      saveActive();
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
      e.preventDefault();
      showSearchRef.current?.();
    }
  }, [saveActive]);
  // showSearch объявляется ниже — через ref, чтобы не плодить зависимости.
  const showSearchRef = useRef<(() => void) | null>(null);

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
    const decls = unrewriteUrls(ruleEdits[rv.id] ?? rv.decls, rv.innerPath);
    let text: string;
    try { text = buffers[path]?.text ?? await fetchFile(path); }
    catch (e: any) { flash(`Не прочитал ${path}: ${e.message}`); return; }
    let patched = patchCssRule(text, rv.selector, decls);
    if (patched === null) {
      // Селектор в файле не нашёлся ровно один раз (минифицированный css,
      // дубли в @media) — РАНЬШЕ правка отклонялась и «терялась» после
      // перезагрузки. Теперь дописываем override-правило в конец: каскад
      // с той же специфичностью побеждает, правка сохраняется всегда.
      const rule = (rv.media ? `@media ${rv.media} { ` : '')
        + `${rv.selector} { ${decls} }` + (rv.media ? ' }' : '');
      patched = appendOverrideRule(text, path, rule);
      flash('Правило неоднозначно в файле — дописан override в конец (каскад)');
    }
    setBuffers((b) => ({ ...b, [path]: { text: patched, saved: b[path]?.saved ?? text } }));
    await saveFile(path, patched);
  }, [entryFile, ruleEdits, buffers, fetchFile, flash, saveFile]);

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
  // Pointer capture: мышь над iframe (другой документ) не отдаёт mouseup окну —
  // ручка «прилипала» к курсору. Пока тянем, iframe не принимает события.
  const [splitting, setSplitting] = useState(false);
  const startSplit = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const root = rootRef.current;
    const handle = e.currentTarget as HTMLElement;
    if (!root) return;
    try { handle.setPointerCapture(e.pointerId); } catch { /* ignore */ }
    setSplitting(true);
    let raf = 0;
    const move = (ev: PointerEvent) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const r = root.getBoundingClientRect();
        setSplitPct(Math.min(75, Math.max(20, ((ev.clientX - r.left) / r.width) * 100)));
      });
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
      if (raf) cancelAnimationFrame(raf);
      setSplitting(false);
      document.body.style.userSelect = '';
    };
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', up);
    handle.addEventListener('pointercancel', up);
    document.body.style.userSelect = 'none';
  }, []);

  const extensions = useMemo(
    // search() нужен для setSearchQuery/подсветки совпадений нашей панели
    () => [...langFor(activePath), EditorView.lineWrapping, RU_PHRASES, search()],
    [activePath]);

  // ---- поиск (собственная панель в стиле VS Code) ------------------------------
  const [searchOpen, setSearchOpen] = useState(false);
  const [findText, setFindText] = useState('');
  const [replText, setReplText] = useState('');
  const [showRepl, setShowRepl] = useState(false);
  const [caseSens, setCaseSens] = useState(false);
  const [wholeWord, setWholeWord] = useState(false);
  const [useRegexp, setUseRegexp] = useState(false);
  const [matchInfo, setMatchInfo] = useState<{ count: number; current: number }>({ count: 0, current: 0 });
  const searchInputRef = useRef<HTMLInputElement>(null);

  const buildQuery = useCallback(() => new SearchQuery({
    search: findText, replace: replText,
    caseSensitive: caseSens, wholeWord, regexp: useRegexp,
  }), [findText, replText, caseSens, wholeWord, useRegexp]);

  // Публикуем запрос в CodeMirror (подсветка всех совпадений) + счётчик.
  useEffect(() => {
    const view = cmRef.current?.view;
    if (!view) return;
    const q = buildQuery();
    view.dispatch({ effects: setSearchQuery.of(q) });
    if (!searchOpen || !findText) { setMatchInfo({ count: 0, current: 0 }); return; }
    try {
      let count = 0, current = 0;
      const selFrom = view.state.selection.main.from;
      const cur = q.getCursor(view.state.doc);
      while (true) {
        const n = cur.next();
        if (n.done) break;
        count++;
        if (n.value.from <= selFrom) current = count;
        if (count > 9999) break; // предохранитель на огромных файлах
      }
      setMatchInfo({ count, current });
    } catch { setMatchInfo({ count: 0, current: 0 }); }
    // buf?.text в зависимостях: правки текста обновляют счётчик
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchOpen, findText, caseSens, wholeWord, useRegexp, activePath, buf?.text]);

  const doFind = useCallback((dir: 'next' | 'prev') => {
    const view = cmRef.current?.view;
    if (!view || !findText) return;
    view.dispatch({ effects: setSearchQuery.of(buildQuery()) });
    (dir === 'next' ? findNext : findPrevious)(view);
    // после перехода пересчитать «текущий»
    setTimeout(() => {
      const v = cmRef.current?.view;
      if (!v) return;
      try {
        const q = buildQuery();
        let count = 0, current = 0;
        const selFrom = v.state.selection.main.from;
        const cur = q.getCursor(v.state.doc);
        while (true) {
          const n = cur.next();
          if (n.done) break;
          count++;
          if (n.value.from <= selFrom) current = count;
        }
        setMatchInfo({ count, current });
      } catch { /* ignore */ }
    }, 30);
  }, [findText, buildQuery]);

  const doReplace = useCallback((all: boolean) => {
    const view = cmRef.current?.view;
    if (!view || !findText) return;
    view.dispatch({ effects: setSearchQuery.of(buildQuery()) });
    (all ? replaceAll : replaceNext)(view);
    // текст изменился — синхронизируем буфер (onChange CodeMirror это сделает сам)
  }, [findText, buildQuery]);

  const showSearch = useCallback(() => {
    setSearchOpen(true);
    setTimeout(() => searchInputRef.current?.focus(), 30);
  }, []);
  showSearchRef.current = showSearch;

  // ---- переименование файла ----------------------------------------------------
  const renameActive = useCallback(async () => {
    if (!activePath) return;
    const oldName = activePath.split('/').pop() || activePath;
    const name = prompt('Новое имя файла (ссылки в коде обновятся):', oldName);
    if (!name || name.trim() === oldName) return;
    try {
      const r = await api.previewRenameFile(zipName, activePath, name.trim());
      // буферы: старый путь убрать, список перечитать, открыть новый путь
      setBuffers((b) => {
        const nb = { ...b };
        delete nb[activePath];
        return nb;
      });
      const list = await fetch(`/api/preview/${encodeURIComponent(zipName)}/files`).then((x) => x.json());
      setFiles(list.map((f: { path: string }) => f.path).filter((p: string) => TEXT_EXTS.has(extOf(p))));
      setActivePath('');
      setVer((v) => v + 1);
      void openFile(r.path);
      flash(`✓ Переименовано: ${oldName} → ${name.trim()}${r.refs_updated ? ` (ссылок обновлено: ${r.refs_updated})` : ''}`);
    } catch (e: any) {
      flash(`Переименование: ${e.message}`);
    }
  }, [activePath, zipName, openFile, flash]);

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
        <button className="btn" onClick={renameActive} disabled={!activePath} style={{ fontSize: 12 }}
                title="Переименовать текущий файл (ссылки в коде обновятся)">
          <Icon name="edit" size={13} />
        </button>
        <button className="btn" onClick={showSearch} disabled={!buf} style={{ fontSize: 12 }}
                title="Поиск и замена в коде (Ctrl+F)">
          <Icon name="search" size={13} /> Поиск
        </button>
        <button className="btn" onClick={() => void reloadFromDisk()} style={{ fontSize: 12 }}
                title="Перечитать файлы из архива (несохранённые правки останутся)">
          <Icon name="refresh" size={13} />
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
            style={{ width: '100%', height: '100%', border: 'none', display: 'block', pointerEvents: splitting ? 'none' : 'auto' }}
            title="lander-editor-preview"
          />
        </div>
        {/* ручка */}
        <div onPointerDown={startSplit} title="Тяни, чтобы менять пропорции"
             style={{ width: 10, cursor: 'ew-resize', touchAction: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
          <div style={{ width: 3, height: 42, borderRadius: 3, background: 'var(--accent)', opacity: splitting ? 1 : 0.6 }} />
        </div>
        {/* правая колонка: код + панель стилей */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ position: 'relative', flex: `1 1 ${100 - (picked ? panelH : 0)}%`, minHeight: 0, border, borderRadius: 8, overflow: 'hidden' }}>
            {/* Панель поиска в стиле VS Code: плавает сверху-справа над кодом,
                живёт, пока её явно не закроют (✕ или Esc) — не сбрасывается
                ни после замены, ни после сохранения, ни при смене файла. */}
            {searchOpen && (
              <div style={{ position: 'absolute', top: 6, right: 14, zIndex: 20,
                            background: 'var(--bg-elevated, #141414)', border, borderRadius: 8,
                            boxShadow: '0 6px 24px rgba(0,0,0,0.45)', padding: 6,
                            display: 'flex', flexDirection: 'column', gap: 4, width: 380 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                  <button className="btn" onClick={() => setShowRepl((v) => !v)} title="Показать замену"
                          style={{ fontSize: 10, padding: '2px 5px' }}>{showRepl ? '▾' : '▸'}</button>
                  <input
                    ref={searchInputRef}
                    className="form-input"
                    value={findText}
                    placeholder="Найти"
                    onChange={(e) => setFindText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); doFind(e.shiftKey ? 'prev' : 'next'); }
                      if (e.key === 'Escape') { e.preventDefault(); setSearchOpen(false); cmRef.current?.view?.focus(); }
                    }}
                    style={{ flex: 1, fontSize: 12, padding: '3px 8px', fontFamily: 'monospace' }}
                  />
                  <span className="dim" style={{ fontSize: 11, fontFamily: 'monospace', whiteSpace: 'nowrap', minWidth: 52, textAlign: 'center' }}>
                    {findText ? (matchInfo.count ? `${matchInfo.current || 1}/${matchInfo.count}` : 'нет') : ''}
                  </span>
                  {([
                    ['Aa', caseSens, () => setCaseSens((v) => !v), 'С учётом регистра'],
                    ['|ab|', wholeWord, () => setWholeWord((v) => !v), 'Слово целиком'],
                    ['.*', useRegexp, () => setUseRegexp((v) => !v), 'Регулярное выражение'],
                  ] as const).map(([lbl, on, toggle, title]) => (
                    <button key={lbl} onClick={toggle} title={title}
                            style={{ fontSize: 10, fontFamily: 'monospace', padding: '3px 5px', borderRadius: 4, cursor: 'pointer',
                                     border: `1px solid ${on ? 'var(--accent)' : 'var(--border, #2a2a2a)'}`,
                                     background: on ? 'var(--accent-soft, rgba(124,111,255,0.2))' : 'transparent',
                                     color: on ? 'var(--accent)' : 'var(--text-muted)' }}>
                      {lbl}
                    </button>
                  ))}
                  <button className="btn" style={{ fontSize: 11, padding: '2px 6px' }} title="Предыдущее (Shift+Enter)"
                          onClick={() => doFind('prev')} disabled={!findText}>↑</button>
                  <button className="btn" style={{ fontSize: 11, padding: '2px 6px' }} title="Следующее (Enter)"
                          onClick={() => doFind('next')} disabled={!findText}>↓</button>
                  <button className="btn" style={{ fontSize: 11, padding: '2px 6px' }} title="Закрыть (Esc)"
                          onClick={() => { setSearchOpen(false); cmRef.current?.view?.focus(); }}>✕</button>
                </div>
                {showRepl && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 4, paddingLeft: 26 }}>
                    <input
                      className="form-input"
                      value={replText}
                      placeholder="Заменить на"
                      onChange={(e) => setReplText(e.target.value)}
                      onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); doReplace(false); } }}
                      style={{ flex: 1, fontSize: 12, padding: '3px 8px', fontFamily: 'monospace' }}
                    />
                    <button className="btn" style={{ fontSize: 11 }} onClick={() => doReplace(false)}
                            disabled={!findText} title="Заменить текущее">Заменить</button>
                    <button className="btn" style={{ fontSize: 11 }} onClick={() => doReplace(true)}
                            disabled={!findText} title="Заменить все совпадения">Все</button>
                  </div>
                )}
              </div>
            )}
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
      {/* Шапка в ДВЕ строки: длинный селектор и длинный путь к файлу больше не
          распирают ряд (раньше оба были nowrap без minWidth:0 — кнопки уезжали
          за правый край карточки, а карточку обрезал overflow:hidden). */}
      <div style={{ padding: '3px 8px', background: 'var(--bg, #0d0e12)', borderBottom: border,
                    display: 'flex', flexDirection: 'column', gap: 2 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <span style={{ flex: '1 1 auto', minWidth: 0, fontFamily: 'monospace', fontSize: 11, color: '#38bdf8',
                         overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={title}>
            {title}
          </span>
          {media && (
            <span className="dim small" style={{ flexShrink: 0, fontSize: 10 }} title={`@media ${media}`}>
              @{media.length > 18 ? media.slice(0, 18) + '…' : media}
            </span>
          )}
          {onJump && (
            <button className="btn" style={{ flexShrink: 0, fontSize: 10, padding: '1px 6px' }}
                    onClick={onJump} title="Показать в коде">→ код</button>
          )}
          <button className="btn" style={{ flexShrink: 0, fontSize: 10, padding: '1px 6px', whiteSpace: 'nowrap' }}
                  onClick={onSave} title="Записать изменения в файл и сохранить">
            <Icon name="save" size={11} /> Сохранить
          </button>
        </div>
        <span className="dim small" style={{ fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
              title={source}>{source}</span>
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
