import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { api, chatStream, translateStream, keitaroUploadStream, SessionFull, LanderState, SuggestParams, LogLine, CommentAttachment, LanderMedia, Replacement, ChatMessage, AiStatus, KeitaroPlan, LanderVersion } from '../lib/api';
import { Markdown } from '../components/Markdown';
import { OfferNamesHint } from '../components/OfferNamesHint';
import { TaskDetailsModal } from '../components/TaskDetailsModal';
import { Icon } from '../components/Icon';
import { VslPanel } from '../components/VslPanel';
// Редактор кода тянет CodeMirror (~700КБ) — грузим чанк только при входе в режим.
const LanderEditor = lazy(() =>
  import('../components/LanderEditor').then((m) => ({ default: m.LanderEditor })));

const ACTIVE_STATUSES = new Set(['queued', 'downloading', 'scanning', 'adapting']);

function statusColor(s: string) {
  if (s === 'ready') return '#38bdf8';
  if (s === 'adapted') return '#4ade80';
  if (s === 'error') return '#f87171';
  return '#f59e0b';
}

function splitPrice(s: string): [string, string] {
  const v = (s || '').trim();
  const m = v.match(/[\d.,]+/);
  if (!m) return ['', v];
  const num = m[0];
  const cur = (v.slice(0, m.index) + v.slice((m.index || 0) + num.length)).trim();
  return [num, cur];
}

function doubleNum(num: string): string {
  const n = parseFloat((num || '').replace(',', '.'));
  if (isNaN(n)) return '';
  const v = n * 2;
  return Number.isInteger(v) ? String(v) : String(v);
}

function LogView({ log }: { log?: LogLine[] }) {
  if (!log || !log.length) return null;
  const color = (lvl: string) =>
    lvl === 'success' ? '#4ade80' : lvl === 'error' ? '#f87171'
    : lvl === 'warning' ? '#f59e0b' : lvl === 'section' ? '#7c6fff' : '#cbd5e1';
  return (
    <pre style={{
      background: '#0d0e12', borderRadius: 6, padding: '0.6rem 0.8rem',
      fontSize: 11, lineHeight: 1.5, maxHeight: 200, overflow: 'auto', margin: 0,
    }}>
      {log.map((l, i) => <div key={i} style={{ color: color(l.level) }}>{l.text}</div>)}
    </pre>
  );
}

function LanderPanel({ sid, lander, isVsl, sessionTasks }: {
  sid: string; lander: LanderState; isVsl?: boolean;
  sessionTasks?: { uid: string; title: string }[];
}) {
  const lid = lander.lander_id;
  const [params, setParams] = useState<Record<string, any> | null>(null);
  const [loadingSuggest, setLoadingSuggest] = useState(false);
  const [adapting, setAdapting] = useState(false);
  const [group, setGroup] = useState('');        // эффективная группа/оффер ленда
  const [groupDraft, setGroupDraft] = useState('');
  const [savingGroup, setSavingGroup] = useState(false);
  const [groupPhotosNote, setGroupPhotosNote] = useState('');  // фото новой группы
  const [error, setError] = useState('');
  const [version, setVersion] = useState(0);
  const [versions, setVersions] = useState<LanderVersion[]>([]);   // история версий ленда
  const [currentVid, setCurrentVid] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [leftTab, setLeftTab] = useState<'params' | 'chat'>('params');
  const [vslTab, setVslTab] = useState<'config' | 'adapt'>('config'); // подрежим VSL-ленда
  const [showKeitaro, setShowKeitaro] = useState(false);
  const [showTranslate, setShowTranslate] = useState(false);
  // Ширина превью (null = на всю доступную область) — для проверки адаптива.
  // Если выбранная ширина больше области, ленд масштабируется, а не обрезается.
  const [previewWidth, setPreviewWidth] = useState<number | null>(null);
  // Режим правой зоны: просмотр (обычная адаптация) или редактор кода.
  const [uiMode, setUiMode] = useState<'view' | 'edit'>('view');
  const [editorMounted, setEditorMounted] = useState(false); // ленивый маунт; буферы живут при переключении
  const previewPaneRef = useRef<HTMLDivElement>(null);
  const [pane, setPane] = useState({ w: 0, h: 0 });

  // Замеряем доступную область превью.
  useEffect(() => {
    const el = previewPaneRef.current;
    if (!el) return;
    const upd = () => setPane({ w: el.clientWidth, h: el.clientHeight });
    upd();
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    return () => ro.disconnect();
  }, [lander.output_name, lander.status]);

  // Реальная ширина превью (без масштабирования) — ленд показывается 1:1,
  // область расширяется вправо, по горизонтали — скролл если шире окна.
  const effW = previewWidth ?? pane.w;

  // Высота контента ленда (iframe same-origin) — чтобы iframe растягивался на
  // весь ленд, а вертикальный скролл был у внешнего контейнера (крупная полоса).
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [contentH, setContentH] = useState<number | null>(null);
  const iframeRoRef = useRef<ResizeObserver | null>(null);
  const measure = useCallback(() => {
    try {
      const d = iframeRef.current?.contentDocument;
      if (!d) return;
      const h = Math.max(
        d.documentElement?.scrollHeight || 0, d.body?.scrollHeight || 0,
        d.documentElement?.offsetHeight || 0, d.body?.offsetHeight || 0,
      );
      if (h) setContentH(h);
    } catch { /* cross-origin — оставим авто-высоту */ }
  }, []);

  // Длинные ленды дорисовываются после onLoad (lazy-картинки, веб-шрифты, JS-
  // секции) → высота росла и низ обрезался. Подписываемся на изменения размера
  // содержимого iframe и домеряем по мере роста + несколько отложенных замеров.
  const onPreviewLoad = useCallback(() => {
    measure();
    [200, 600, 1200, 2500, 4000].forEach((ms) => setTimeout(measure, ms));
    try {
      const d = iframeRef.current?.contentDocument;
      iframeRoRef.current?.disconnect();
      if (d && 'ResizeObserver' in window) {
        const ro = new ResizeObserver(() => measure());
        if (d.body) ro.observe(d.body);
        if (d.documentElement) ro.observe(d.documentElement);
        iframeRoRef.current = ro;
      }
      // Картинки без размеров (lazy) — домеряем по их загрузке.
      d?.querySelectorAll('img')?.forEach((img) => {
        if (!(img as HTMLImageElement).complete) img.addEventListener('load', measure, { once: true });
      });
    } catch { /* cross-origin */ }
  }, [measure]);

  useEffect(() => () => iframeRoRef.current?.disconnect(), []);

  // Пересчитываем высоту при смене ширины/версии (меняется раскладка).
  useEffect(() => {
    const t1 = setTimeout(measure, 120);
    const t2 = setTimeout(measure, 700);
    return () => { clearTimeout(t1); clearTimeout(t2); };
  }, [previewWidth, version, measure]);

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = previewWidth ?? pane.w ?? 800;
    const onMove = (ev: MouseEvent) => {
      setPreviewWidth(Math.max(280, Math.round(startW + (ev.clientX - startX))));
    };
    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    document.body.style.userSelect = 'none';
  };

  // Подтягиваем подсказку, когда ленд готов и ещё не было параметров.
  useEffect(() => {
    if ((lander.status === 'ready' || lander.status === 'adapted') && !params) {
      setLoadingSuggest(true);
      api.suggestParams(sid, lid)
        .then((s: SuggestParams) => {
          const rest: any = { ...s };
          delete rest._hints;
          setGroup(s.group || '');
          setGroupDraft(s.group || '');
          const saved: any = lander.adapt_params;
          if (saved) {
            // Сохранённые параметры прошлой адаптации — поверх свежего suggest
            // (после переустановки в adapt_params остаются только ключи
            // статуса заливки — недостающие поля формы заполняет suggest).
            // Исходные цены донора (src_price_*) всегда из СВЕЖЕГО скана: они
            // описывают сам ленд, и после пересканирования/переустановки
            // старое значение (напр. ошибочное «50») тихо ломало замену цены.
            const next = { ...rest, ...saved };
            for (const k of ['src_price_new_num', 'src_price_new_cur',
                             'src_price_old_num', 'src_price_old_cur'] as const) {
              if (rest[k] !== undefined && rest[k] !== '' && rest[k] !== saved[k]) next[k] = rest[k];
            }
            setParams(next);
          } else {
            setParams(rest);
          }
        })
        .catch((e) => setError(e.message))
        .finally(() => setLoadingSuggest(false));
    }
  }, [lander.status, sid, lid]);

  // Переустановка/повторное скачивание ленда: параметры устарели — сбрасываем,
  // чтобы после нового скана форма перезаполнилась свежим suggest.
  // 'adapting' сюда не входит: во время адаптации параметры трогать нельзя.
  useEffect(() => {
    if (['queued', 'downloading', 'scanning'].includes(lander.status) && params) {
      setParams(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lander.status]);

  const set = (k: string, v: any) => setParams((p) => ({ ...(p || {}), [k]: v }));
  const setPrice = (which: 'new' | 'old', v: string) => {
    const [num, cur] = splitPrice(v);
    setParams((p) => {
      const next: Record<string, any> = { ...(p || {}), [`price_${which}`]: v, [`price_${which}_num`]: num, [`price_${which}_cur`]: cur };
      // Новая цена → старая автоматически ×2 (правило техотдела).
      if (which === 'new') {
        const dbl = doubleNum(num);
        next.price_old = dbl ? `${dbl} ${cur}`.trim() : '';
        next.price_old_num = dbl;
        next.price_old_cur = cur;
      }
      return next;
    });
  };

  const saveGroup = async (value?: string) => {
    const next = (value ?? groupDraft).trim();
    if (next === group.trim()) return;
    setSavingGroup(true); setError(''); setGroupPhotosNote('');
    try {
      const s = await api.setLanderGroup(sid, lid, next);
      setGroup(s.group || '');
      setGroupDraft(s.group || '');
      lander.offer_override = next || null;
      // Бэк заодно поискал фото продукта НОВОЙ группы на странице оффера.
      const added = (s as any).photos_added || 0;
      setGroupPhotosNote(added > 0
        ? `Найдено фото продукта новой группы: ${added} (в «Загруженные медиа»)`
        : 'Новых фото продукта для группы не найдено');
      // Подставляем пересчитанные под новую группу гео/продукт/вертикаль.
      // Цену НЕ трогаем — сохраняем то, что ввёл пользователь вручную.
      setParams((p) => ({
        ...(p || {}),
        geo_id: s.geo_id,
        product_new: s.product_new,
        exclude_word: s.exclude_word,
      }));
    } catch (e: any) {
      setError(e.message || 'Не удалось сменить группу');
    } finally {
      setSavingGroup(false);
    }
  };

  const runAdapt = async () => {
    if (!params) return;
    setAdapting(true); setError('');
    try {
      const res = await api.adaptLander(sid, lid, params);
      if (!res.success) setError(res.error || 'Адаптация не дала результата');
      // обновим состояние ленда (output) — перезагрузка произойдёт из родителя по polling,
      // но сразу подменим локально:
      lander.output_name = res.output_name || lander.output_name;
      lander.output_url = res.output_url || lander.output_url;
      lander.status = res.status;
      lander.adapt_log = res.log;
      // мёрж, не перезапись: статус заливки (keitaro_offer_id, кампания,
      // вариант/ревью AdRobot) должен пережить адаптацию и локально
      lander.adapt_params = { ...(lander.adapt_params || {}), ...params };
      setVersion((v) => v + 1);
    } catch (e: any) {
      setError(e.message || 'Ошибка адаптации');
    } finally {
      setAdapting(false);
    }
  };

  // История версий ленда (для дропдауна в шапке). Перечитываем после каждой
  // мутации (version бампается на adapt / нейро / перевод / чат / откат).
  useEffect(() => {
    if (!lander.output_name) { setVersions([]); setCurrentVid(null); return; }
    api.landerHistory(sid, lid)
      .then((r) => { setVersions(r.versions); setCurrentVid(r.current); })
      .catch(() => {});
  }, [sid, lid, lander.output_name, version]);

  const restoreVersion = async (vid: string) => {
    if (!vid || vid === currentVid) return;
    const v = versions.find((x) => x.id === vid);
    if (v && !confirm(`Откатить ленд к шагу ${v.step} «${v.label}»? Текущее состояние сохранится для возврата.`)) return;
    setRestoring(true); setError('');
    try {
      const res = await api.restoreVersion(sid, lid, vid);
      lander.output_name = res.output_name || lander.output_name;
      lander.output_url = res.output_url || lander.output_url;
      lander.status = res.status;
      setVersion((n) => n + 1);  // перечитает историю + обновит превью
    } catch (e: any) {
      setError(e.message || 'Ошибка отката');
    } finally {
      setRestoring(false);
    }
  };

  const scan = lander.scan;
  // Превью адаптированного результата, иначе — исходного ленда (session__sid__lid).
  const previewSource = lander.output_name || `session__${sid}__${lid}`;
  const isAdapted = !!lander.output_name;
  const previewUrl = (lander.status === 'ready' || isAdapted)
    ? `/api/preview/${encodeURIComponent(previewSource)}/render?path=index.php&v=${version}`
    : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', height: '100%', overflow: 'hidden' }}>
      {/* шапка ленда */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexShrink: 0 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Ленд {lid}</h2>
        <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, color: '#fff', background: statusColor(lander.status) }}>
          {lander.status}
        </span>
        {/* Статус заливки — живёт в adapt_params и переживает переадаптацию */}
        {(lander.adapt_params as any)?.keitaro_offer_id && (
          <span
            className="mono"
            title={[
              `Залит в Keitaro: ${(lander.adapt_params as any).keitaro_name || (lander.adapt_params as any).keitaro_offer_id}`,
              (lander.adapt_params as any).campaign_url ? `Тестовая кампания: ${(lander.adapt_params as any).campaign_url}` : '',
              (lander.adapt_params as any).variant_added ? 'Вариант добавлен в задачу AdRobot' : '',
              (lander.adapt_params as any).variants_moved ? `Варианты перемещены: ${(lander.adapt_params as any).variants_moved}` : '',
              (lander.adapt_params as any).review_submitted ? 'Отправлен на ревью' : '',
            ].filter(Boolean).join('\n')}
            style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, border: '1px solid var(--accent, #7c6fff)', color: 'var(--accent, #7c6fff)', whiteSpace: 'nowrap' }}
          >
            KT {(lander.adapt_params as any).keitaro_offer_id}
            {(lander.adapt_params as any).campaign_url ? ' · тест' : ''}
            {(lander.adapt_params as any).review_submitted ? ' · ревью' : ''}
          </span>
        )}
        {ACTIVE_STATUSES.has(lander.status) && <span className="dim small">⏳ выполняется…</span>}
        {/* Выбор версии ленда: по умолчанию текущая; смена = откат на выбранный шаг. */}
        {versions.length > 0 && (
          <select
            className="form-input"
            value={currentVid || ''}
            disabled={restoring}
            title="Версия ленда — выбери, чтобы откатиться на шаг (текущее состояние сохранится)"
            onChange={(e) => restoreVersion(e.target.value)}
            style={{ fontSize: 12, padding: '2px 6px', width: 'auto', maxWidth: 260 }}
          >
            {[...versions].reverse().map((v) => (
              <option key={v.id} value={v.id} disabled={!v.available}>
                Шаг {v.step}: {v.label}{v.id === currentVid ? ' (текущая)' : ''}
              </option>
            ))}
          </select>
        )}
        {restoring && <span className="dim small">↩ откатываю…</span>}
        {/* Переключатель Просмотр / Редактор кода */}
        {previewUrl && (
          <div style={{ display: 'flex', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, overflow: 'hidden' }}>
            {([['view', 'Просмотр'], ['edit', 'Редактор']] as const).map(([m, label]) => (
              <button
                key={m}
                onClick={() => {
                  setUiMode(m);
                  if (m === 'edit') setEditorMounted(true);
                  else setVersion((v) => v + 1); // вернулись из редактора — обновить превью правками
                }}
                style={{
                  fontSize: 12, padding: '3px 10px', cursor: 'pointer', border: 'none',
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  background: uiMode === m ? 'var(--accent)' : 'transparent',
                  color: uiMode === m ? '#fff' : 'var(--text-muted)',
                }}
              ><Icon name={m === 'view' ? 'eye' : 'code'} size={13} />{label}</button>
            ))}
          </div>
        )}
        {/* Привязка к задаче: в объединённой сессии (несколько задач) — селект;
            без привязки вариант/ревью не знают, в какую задачу идти. */}
        {sessionTasks && sessionTasks.length > 1 ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }} title="Задача этого ленда — вариант/ревью уйдут в неё">
            <Icon name="clipboard" size={12} />
            <select
              className="form-input"
              value={lander.task_uid || ''}
              onChange={async (e) => {
                const uid = e.target.value;
                if (!uid) return;
                try {
                  const r = await api.setLanderTask(sid, lid, uid);
                  lander.task_uid = r.task_uid;
                  lander.task_title = r.task_title;
                  setVersion((v) => v + 1);
                } catch (err: any) {
                  setError(err.message || 'Не удалось привязать задачу');
                }
              }}
              style={{
                fontSize: 12, padding: '2px 6px', width: 'auto', maxWidth: 260,
                borderColor: lander.task_uid ? undefined : 'var(--warning, #e8a857)',
              }}
            >
              <option value="" disabled>— выбери задачу ленда —</option>
              {sessionTasks.map((t) => (
                <option key={t.uid} value={t.uid}>{t.title || t.uid}</option>
              ))}
            </select>
            {!lander.task_uid && (
              <span className="small" style={{ color: 'var(--warning, #e8a857)' }}>не привязан</span>
            )}
          </span>
        ) : lander.task_title ? (
          <span className="dim small" title="Задача-источник этого ленда"><Icon name="clipboard" size={12} /> {lander.task_title}</span>
        ) : null}
        {lander.offer_name && <span className="dim small" style={{ marginLeft: 'auto' }}>Донор: <code>{lander.offer_name}</code></span>}
      </div>

      {lander.error && (
        <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, fontSize: 12, flexShrink: 0 }}>
          {lander.error}
        </div>
      )}
      {ACTIVE_STATUSES.has(lander.status) && (
        <p className="dim" style={{ flexShrink: 0 }}>Идёт подготовка (скачивание из Keitaro и scan). Обновится автоматически.</p>
      )}

      {/* scan-сводка */}
      {scan && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px,1fr))', gap: '0.5rem', flexShrink: 0 }}>
          <Info label="Продукт (scan)" value={scan.product || '—'} />
          <Info label="Гео донора" value={(scan.detected_country?.data_country || []).join(', ') || '—'} />
          <Info label="Язык" value={(scan.detected_country?.data_language || []).join(', ') || '—'} />
          <Info label="Цены донора" value={`${scan.price_new_str || '?'} / ${scan.price_old_str || '?'}`} />
          <Info label="exclude_word" value={scan.detected_country?.exclude_word || '—'} />
          <Info label="Фото продукта" value={(scan.prod_images || []).join(', ') || '—'} />
        </div>
      )}

      {loadingSuggest && <p className="dim small" style={{ flexShrink: 0 }}>Готовлю параметры…</p>}
      {error && (
        <div style={{ padding: '0.5rem 0.8rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, fontSize: 12, flexShrink: 0 }}>{error}</div>
      )}

      {/* Редактор кода: маунтится лениво, при переключении прячется (буферы живут) */}
      {editorMounted && previewUrl && (
        <div style={{ display: uiMode === 'edit' ? 'flex' : 'none', flex: 1, minHeight: 0 }}>
          <Suspense fallback={<p className="dim small">Загружаю редактор…</p>}>
            <LanderEditor key={previewSource} zipName={previewSource} />
          </Suspense>
        </div>
      )}

      {/* двухколоночная зона: левая колонка (вкладки Параметры/Чат) + превью */}
      <div style={{ display: uiMode === 'edit' ? 'none' : 'flex', gap: '1rem', flex: 1, minHeight: 0 }}>
        {/* левая колонка */}
        <div style={{ flex: '0 0 440px', display: 'flex', flexDirection: 'column', minHeight: 0, border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, overflow: 'hidden' }}>
          <div style={{ display: 'flex', borderBottom: '1px solid var(--border, #2a2a2a)', flexShrink: 0 }}>
            {(['params', 'chat'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setLeftTab(t)}
                style={{
                  flex: 1, padding: '0.5rem', border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                  background: leftTab === t ? 'var(--accent-soft, rgba(124,111,255,0.15))' : 'transparent',
                  color: leftTab === t ? 'var(--accent, #7c6fff)' : 'var(--text-muted)',
                  borderBottom: leftTab === t ? '2px solid var(--accent, #7c6fff)' : '2px solid transparent',
                }}
              >{t === 'params' ? 'Параметры' : 'Чат с ИИ'}</button>
            ))}
          </div>

          <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
            {/* Чат держим в DOM всегда (скрываем через CSS) — иначе стрим-ответ
                теряется при переключении на «Параметры». */}
            <div style={{ flex: 1, minHeight: 0, flexDirection: 'column', overflow: 'hidden', display: leftTab === 'chat' ? 'flex' : 'none' }}>
              <LanderChat
                sid={sid}
                lid={lid}
                onLanderUpdate={(upd) => { Object.assign(lander, upd); setVersion((v) => v + 1); }}
              />
            </div>
            <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '1rem', display: leftTab === 'params' ? 'block' : 'none' }}>
            {/* VSL: подрежимы «Конфиг VSL» и «Адаптация» (обычная форма) */}
            {isVsl && (lander.status === 'ready' || lander.status === 'adapted') && (
              <div style={{ display: 'flex', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, overflow: 'hidden', marginBottom: '0.8rem' }}>
                {([['config', 'Конфиг VSL'], ['adapt', 'Адаптация']] as const).map(([m, label]) => (
                  <button key={m} onClick={() => setVslTab(m)}
                          style={{
                            flex: 1, fontSize: 12, padding: '5px 10px', cursor: 'pointer', border: 'none',
                            fontWeight: 600,
                            background: vslTab === m ? 'var(--accent)' : 'transparent',
                            color: vslTab === m ? '#fff' : 'var(--text-muted)',
                          }}>{label}</button>
                ))}
              </div>
            )}
            {isVsl && !(lander.status === 'ready' || lander.status === 'adapted') ? (
              <p className="dim small">Жду скачивания ленда…</p>
            ) : isVsl && vslTab === 'config' ? (
              <div>
                <VslPanel sid={sid} lid={lid} hasOutput={!!lander.output_name}
                          onChanged={() => setVersion((v) => v + 1)} />
                <div style={{ marginTop: '0.8rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                  {lander.output_name && (
                    <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowTranslate((v) => !v)}
                            title="Переводит и тексты config.php (форма, уведомления, комментарии)">
                      Перевод
                    </button>
                  )}
                  <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowKeitaro((v) => !v)}>
                    → Keitaro
                  </button>
                </div>
                {showTranslate && lander.output_name && (
                  <TranslatePanel sid={sid} lid={lid} onApplied={() => setVersion((v) => v + 1)} />
                )}
                {showKeitaro && (
                  <KeitaroUploadPanel sid={sid} lid={lid} lander={lander} onChanged={() => setVersion((v) => v + 1)} />
                )}
              </div>
            ) : !params ? (
              <p className="dim small">Готовлю параметры…</p>
            ) : (
          <div>
            <Field label="Группа / оффер (под неё подстроится адаптация)">
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input
                  className="form-input"
                  value={groupDraft}
                  onChange={(e) => setGroupDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') saveGroup(); }}
                  placeholder="напр. 19712 Detox Now [PARASITES-CO] [pl es -]"
                  style={{ flex: 1, fontSize: 12, fontFamily: 'monospace' }}
                />
                <button
                  className="btn"
                  onClick={() => saveGroup()}
                  disabled={savingGroup || groupDraft.trim() === group.trim()}
                  style={{ fontSize: 12, flexShrink: 0 }}
                >
                  {savingGroup ? '…' : 'Применить'}
                </button>
                {lander.offer_override && (
                  <button
                    className="btn"
                    title="Сбросить к офферу задачи"
                    onClick={() => { setGroupDraft(''); saveGroup(''); }}
                    disabled={savingGroup}
                    style={{ fontSize: 12, flexShrink: 0 }}
                  >
                    ↺
                  </button>
                )}
              </div>
              <div className="dim small" style={{ marginTop: 4 }}>
                {lander.offer_override
                  ? 'Подменено вручную. ↺ — вернуть оффер задачи.'
                  : 'Из задачи. Изменение пересчитает продукт/гео/язык/цену.'}
              </div>
              {groupPhotosNote && (
                <div className="small" style={{ marginTop: 4, color: '#4ade80' }}>
                  <Icon name="image" size={12} /> {groupPhotosNote}
                </div>
              )}
            </Field>
            <div style={{ fontSize: 13, fontWeight: 600, margin: '0.75rem 0' }}>Параметры адаптации (черновик — проверь)</div>
            {isVsl && (
              <div className="dim small" style={{ margin: '-0.4rem 0 0.6rem' }}>
                VSL: адаптация применится к рабочей копии — правки config.php сохранятся.
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px,1fr))', gap: '0.6rem' }}>
              <Field label="ГЕО (geo_id)">
                <input className="form-input" value={params.geo_id || ''} onChange={(e) => set('geo_id', e.target.value)} />
              </Field>
              <Field label="Продукт-донор (искать)">
                <input className="form-input" value={params.product_old || ''} onChange={(e) => set('product_old', e.target.value)} />
              </Field>
              <Field label="Новый продукт">
                <input className="form-input" value={params.product_new || ''} onChange={(e) => set('product_new', e.target.value)} />
              </Field>
              <Field label="exclude_word (вертикаль)">
                <input className="form-input" value={params.exclude_word || ''} onChange={(e) => set('exclude_word', e.target.value)} />
              </Field>
              <Field label="Новая цена">
                <input className="form-input" value={params.price_new || ''} onChange={(e) => setPrice('new', e.target.value)} placeholder="590 MXN" />
              </Field>
              <Field label="Старая цена (×2 авто)">
                <input className="form-input" value={params.price_old || ''} onChange={(e) => setPrice('old', e.target.value)} placeholder="1180 MXN" />
              </Field>
            </div>
            <div className="dim small" style={{ marginTop: '0.6rem' }}>
              Старая цена считается автоматически как ×2 от новой.
            </div>

            {/* key с группой: после смены группы блок перечитает замены (там появились фото новой группы) */}
            <ImageMapEditor key={`${lid}:${group}`} sid={sid} lander={lander} params={params} set={set} onChanged={() => setVersion((v) => v + 1)} />

            <div style={{ marginTop: '0.8rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn-primary" onClick={runAdapt} disabled={adapting}>
                {adapting ? 'Адаптирую…' : <><Icon name="settings" size={13} /> Адаптировать</>}
              </button>
              {lander.output_name && (
                <>
                  <a className="btn" style={{ textDecoration: 'none', fontSize: 13 }} href={`/api/download/${encodeURIComponent(lander.output_name)}`}><Icon name="download" size={13} /> Скачать</a>
                  <Link className="btn" style={{ textDecoration: 'none', fontSize: 13 }} to={`/preview?zip=${encodeURIComponent(lander.output_name)}`}><Icon name="edit" size={13} /> Открыть в Preview</Link>
                  <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowTranslate((v) => !v)}>Перевод</button>
                </>
              )}
              {/* Заливка доступна и БЕЗ адаптации — уйдёт исходный архив ленда */}
              {(lander.output_name || lander.status === 'ready') && (
                <button className="btn" style={{ fontSize: 13 }} onClick={() => setShowKeitaro((v) => !v)}
                        title={lander.output_name ? 'Залить адаптированный ленд' : 'Залить ИСХОДНЫЙ ленд (без адаптации)'}>
                  → Keitaro{lander.output_name ? '' : ' (без адаптации)'}
                </button>
              )}
            </div>
            {showTranslate && lander.output_name && (
              <TranslatePanel sid={sid} lid={lid} onApplied={() => setVersion((v) => v + 1)} />
            )}
            {showKeitaro && (lander.output_name || lander.status === 'ready') && (
              <KeitaroUploadPanel sid={sid} lid={lid} lander={lander} onChanged={() => setVersion((v) => v + 1)} />
            )}
            {lander.adapt_log && lander.adapt_log.length > 0 && (
              <div style={{ marginTop: '0.8rem' }}><LogView log={lander.adapt_log} /></div>
            )}
          </div>
            )}
            </div>
          </div>
        </div>

        {/* превью результата (ширина настраивается; не обрезается — масштабируется) */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4, flexShrink: 0, flexWrap: 'wrap' }}>
            <span className="dim small">
              {previewUrl ? (isAdapted ? 'Предпросмотр результата' : 'Предпросмотр исходного ленда') : 'Предпросмотр появится после скачивания'}
            </span>
            {previewUrl && (
              <>
                <div style={{ flex: 1 }} />
                <span className="dim small" style={{ fontFamily: 'monospace' }}>
                  {previewWidth ? `${previewWidth}px` : `${pane.w || '—'}px (full)`}
                </span>
                {([360, 414, 768, 1024, 1280, null] as const).map((w, i) => (
                  <button
                    key={i}
                    onClick={() => setPreviewWidth(w)}
                    style={{
                      fontSize: 12, padding: '2px 8px', cursor: 'pointer', borderRadius: 6,
                      border: '1px solid var(--border)',
                      background: previewWidth === w ? 'var(--accent)' : 'transparent',
                      color: previewWidth === w ? '#fff' : 'var(--text-muted)',
                    }}
                  >{w ?? 'Full'}</button>
                ))}
                <input
                  type="number"
                  value={previewWidth ?? ''}
                  placeholder="px"
                  onChange={(e) => setPreviewWidth(e.target.value ? Math.max(280, +e.target.value) : null)}
                  style={{
                    width: 64, fontSize: 12, padding: '2px 6px', borderRadius: 6,
                    border: '1px solid var(--border)', background: 'var(--bg-elevated)', color: 'var(--text)',
                  }}
                />
              </>
            )}
          </div>

          {previewUrl ? (
            <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
              {/* скролл-контейнер: крупная вертикальная полоса для навигации по ленду */}
              <div
                ref={previewPaneRef}
                style={{ position: 'absolute', inset: 0, overflowX: 'auto', overflowY: 'auto', background: 'var(--bg)', border: '1px solid var(--border)', borderRadius: 8 }}
              >
                <div
                  style={{
                    width: previewWidth ? `${effW}px` : '100%',
                    height: contentH ? `${contentH}px` : '100%',
                  }}
                >
                  <iframe
                    ref={iframeRef}
                    key={version}
                    src={previewUrl}
                    onLoad={onPreviewLoad}
                    scrolling="no"
                    style={{ width: '100%', height: '100%', border: 'none', background: '#fff', display: 'block' }}
                    title={`preview-${lid}`}
                  />
                </div>
              </div>
              {/* ручка ресайза по правому краю видимой области ленда (вне скролла) */}
              <div
                onMouseDown={startResize}
                title="Тяни, чтобы менять ширину превью"
                style={{
                  position: 'absolute', top: 0,
                  left: `${Math.min(previewWidth ?? pane.w, pane.w) - 5}px`,
                  width: 10, height: '100%', cursor: 'ew-resize',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 2,
                }}
              >
                <div style={{ width: 4, height: 48, borderRadius: 4, background: 'var(--accent)', opacity: 0.7 }} />
              </div>
            </div>
          ) : (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px dashed var(--border)', borderRadius: 8 }}>
              <span className="dim small">Превью появится после скачивания ленда из Keitaro</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// Модалка предпросмотра медиа (фото / гиф / видео — по расширению).
function MediaModal({ url, name, onClose }: { url: string; name: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const ext = (name.split('.').pop() || '').toLowerCase();
  const isVideo = ['mp4', 'webm', 'mov', 'ogg'].includes(ext);
  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.8)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem',
      }}
    >
      <div onClick={(e) => e.stopPropagation()} style={{ maxWidth: '90vw', maxHeight: '90vh', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ color: '#fff', fontSize: 13, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
          <div style={{ flex: 1 }} />
          <a href={url} target="_blank" rel="noopener" className="btn" style={{ fontSize: 12, textDecoration: 'none' }}>Открыть ↗</a>
          <button className="btn" style={{ fontSize: 12 }} onClick={onClose}>Закрыть ✕</button>
        </div>
        <div style={{ background: '#000', borderRadius: 8, overflow: 'auto', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          {isVideo ? (
            <video src={url} controls autoPlay style={{ maxWidth: '88vw', maxHeight: '80vh', display: 'block' }} />
          ) : (
            <img src={url} alt={name} style={{ maxWidth: '88vw', maxHeight: '80vh', objectFit: 'contain', display: 'block' }} />
          )}
        </div>
      </div>
    </div>
  );
}

// Выбор замены с предпросмотром: кнопка показывает текущий выбор, по клику —
// всплывающий список с МИНИАТЮРАМИ (нативный <select> не умеет картинки).
// Миниатюра-опция дропдауна замен. ВАЖНО: компонент ВНЕ ReplSelect и выбор
// по onPointerDown — компонент, объявленный внутри рендера, пересоздавал DOM
// миниатюр при каждом ре-рендере (поллинг каждые 3с), и click (mousedown+
// mouseup по ОДНОМУ узлу) не успевал сработать — «список открыт, но не
// нажимается». pointerdown срабатывает мгновенно и от подмены DOM не страдает.
function ReplThumb({ name, selected, urlFor, onPick }: {
  name: string; selected: boolean; urlFor: (name: string) => string; onPick: (name: string) => void;
}) {
  return (
    <div title={name} onPointerDown={(e) => { e.preventDefault(); onPick(name); }}
         style={{ width: 64, textAlign: 'center', cursor: 'pointer', padding: 3, borderRadius: 6, border: selected ? '1px solid var(--accent, #7c6fff)' : '1px solid var(--border, #2a2a2a)' }}>
      <img src={urlFor(name)} alt={name} loading="lazy"
           style={{ width: '100%', height: 44, objectFit: 'contain', background: '#000', borderRadius: 3, display: 'block' }} />
      <div className="dim" style={{ fontSize: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 2 }}>{name}</div>
    </div>
  );
}

function ReplSelect({ value, taskOptions, assetOptions, urlFor, onChange }: {
  value: string;
  taskOptions: string[];
  assetOptions: string[];
  urlFor: (name: string) => string;
  onChange: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  // Закрытие по клику вне списка (раньше открытый список висел навсегда).
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onDown);
    return () => document.removeEventListener('pointerdown', onDown);
  }, [open]);
  const pick = (name: string) => { onChange(name); setOpen(false); };
  return (
    <div ref={rootRef} style={{ flex: 1, position: 'relative', minWidth: 0 }}>
      <button type="button" className="form-input" onClick={() => setOpen((v) => !v)}
              style={{ width: '100%', textAlign: 'left', fontSize: 11, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden' }}>
        {value
          ? <><img src={urlFor(value)} alt="" style={{ width: 20, height: 20, objectFit: 'contain', borderRadius: 3, background: '#000' }} />
              <span className="mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span></>
          : <span className="dim">— не менять (оригинал) —</span>}
        <span className="dim" style={{ marginLeft: 'auto' }}>▾</span>
      </button>
      {open && (
        <div style={{ position: 'absolute', zIndex: 500, top: '100%', left: 0, right: 0, marginTop: 2, maxHeight: 240, overflowY: 'auto', background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, padding: 6 }}>
          <div className="dim small" style={{ cursor: 'pointer', padding: '2px 4px' }}
               onPointerDown={(e) => { e.preventDefault(); pick(''); }}>— не менять (оригинал) —</div>
          {taskOptions.length > 0 && <div className="dim" style={{ fontSize: 9, textTransform: 'uppercase', margin: '4px 2px' }}>Замены задачи</div>}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{taskOptions.map((n) => <ReplThumb key={n} name={n} selected={n === value} urlFor={urlFor} onPick={pick} />)}</div>
          {assetOptions.length > 0 && <div className="dim" style={{ fontSize: 9, textTransform: 'uppercase', margin: '4px 2px' }}>Общие (assets)</div>}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{assetOptions.map((n) => <ReplThumb key={n} name={n} selected={n === value} urlFor={urlFor} onPick={pick} />)}</div>
        </div>
      )}
    </div>
  );
}

// Маленькая кнопка предпросмотра — открывает медиа в модалке.
// Редактор замены фото: фото ленда (из scan) → ассет из storage/assets.
// Плюс быстрый импорт картинок, прикреплённых в комментариях задачи.
function ImageMapEditor({ sid, lander, params, set, onChanged }: {
  sid: string; lander: LanderState; params: Record<string, any>; set: (k: string, v: any) => void; onChanged?: () => void;
}) {
  const lid = lander.lander_id;
  const [media, setMedia] = useState<LanderMedia[]>([]);
  const [assets, setAssets] = useState<string[]>([]);            // глобальные storage/assets
  const [taskRepl, setTaskRepl] = useState<Replacement[]>([]);   // изолированные по задаче
  const [commentImages, setCommentImages] = useState<{ url: string; filename: string }[]>([]);
  const [busy, setBusy] = useState('');
  const [note, setNote] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [includeUnused, setIncludeUnused] = useState(false);  // показывать и неиспользуемые
  const [preview, setPreview] = useState<{ url: string; name: string } | null>(null);
  const [editMedia, setEditMedia] = useState<LanderMedia | null>(null);  // нейро-правка

  const loadMedia = (all = includeUnused) => api.landerMedia(sid, lid, all).then(setMedia).catch(() => {});
  const loadAssets = () => api.assets().then(setAssets).catch(() => {});
  const loadRepl = (autoload = false) => api.landerReplacements(sid, lid, autoload)
    .then((r) => { setTaskRepl(r.replacements); setCommentImages(r.comment_images); })
    .catch(() => {});

  // При открытии: медиа ленда, глобальные ассеты и замены (с авто-подгрузкой фото оффера).
  useEffect(() => { loadMedia(); loadAssets(); loadRepl(true); /* eslint-disable-next-line */ }, [sid, lid]);
  useEffect(() => { loadMedia(includeUnused); /* eslint-disable-next-line */ }, [includeUnused]);

  const imageMap: Record<string, string> = params.image_map || {};
  const setMap = (name: string, val: string) => set('image_map', { ...imageMap, [name]: val });
  const clearMap = (name: string) => {
    const m = { ...imageMap }; delete m[name]; set('image_map', m);
  };

  const taskNames = new Set(taskRepl.map((r) => r.name));
  // URL для превью замены: изолированная (task) или глобальный ассет.
  const replUrl = (name: string) => taskNames.has(name)
    ? api.replacementFileUrl(sid, lid, name)
    : `/api/assets-file/${encodeURIComponent(name)}`;

  const shown = showAll ? media : media.filter((m) => m.is_product);

  // Загрузка локальных файлов с компьютера → изолированные замены задачи.
  const uploadLocal = async (fileList: FileList | null) => {
    if (!fileList || !fileList.length) return;
    setBusy('upload'); setNote('');
    try {
      const r = await api.uploadReplacements(sid, lid, fileList);
      await loadRepl();
      setNote(`Загружено в замены задачи: ${r.names.join(', ')}`);
    } catch (e: any) {
      setNote('Ошибка загрузки: ' + (e.message || ''));
    } finally {
      setBusy('');
    }
  };

  // Удалить файл из загруженных медиа (замен) задачи.
  const deleteRepl = async (name: string) => {
    if (!confirm(`Удалить «${name}» из загруженных медиа?`)) return;
    setBusy('del-' + name); setNote('');
    try {
      await api.deleteReplacement(sid, lid, name);
      await loadRepl();
      setNote(`Удалено: ${name}`);
    } catch (e: any) {
      setNote('Ошибка удаления: ' + (e.message || ''));
    } finally {
      setBusy('');
    }
  };

  // Удалить фон у замены (rembg) → новая замена nobg_*.png.
  const removeBg = async (name: string) => {
    setBusy('bg-' + name); setNote('');
    try {
      const r = await api.removeBgReplacement(sid, lid, name);
      await loadRepl();
      setNote(`Фон удалён → ${r.name}`);
    } catch (e: any) {
      setNote('Удаление фона: ' + (e.message || ''));
    } finally {
      setBusy('');
    }
  };

  // Импорт картинки из комментария → изолированные замены задачи (не в общий список).
  const importComment = async (att: { url: string; filename: string }) => {
    setBusy(att.url); setNote('');
    try {
      const r = await api.importReplacement(sid, lid, att.url, att.filename);
      await loadRepl();
      setNote(`Добавлено в замены задачи: ${r.name}`);
    } catch (e: any) {
      setNote('Ошибка импорта: ' + (e.message || ''));
    } finally {
      setBusy('');
    }
  };

  return (
    <div style={{ marginTop: '0.9rem', borderTop: '1px solid var(--border, #2a2a2a)', paddingTop: '0.8rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Замена медиа (фото / гиф / видео)</span>
        {media.length > 0 && (
          <button type="button" className="btn" style={{ fontSize: 10 }} onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'только фото продукта' : `все медиа на ленде (${media.length})`}
          </button>
        )}
        <button type="button" className="btn" style={{ fontSize: 10 }} onClick={() => setIncludeUnused((v) => !v)}
                title="По умолчанию показываются только медиа, реально используемые на ленде">
          {includeUnused ? 'скрыть лишние из архива' : '+ скрытые из архива'}
        </button>
        <label className="btn" style={{ fontSize: 10, marginLeft: 'auto', cursor: 'pointer' }}
               title="Загрузить медиа с компьютера в замены задачи">
          {busy === 'upload' ? '…' : '↑ загрузить файл'}
          <input type="file" accept="image/*,video/*,.gif" multiple style={{ display: 'none' }}
                 disabled={busy === 'upload'}
                 onChange={(e) => { uploadLocal(e.target.files); e.currentTarget.value = ''; }} />
        </label>
      </div>

      {/* Изолированные по задаче замены (фото оффера + импортированные) */}
      {taskRepl.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="dim" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
            Замены задачи (фото оффера и импортированные)
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {taskRepl.map((r) => (
              <div key={r.name} title={r.name}
                   style={{ position: 'relative', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, padding: 3, width: 64, textAlign: 'center' }}>
                {/* × удалить из загруженных медиа */}
                <button type="button" title="Удалить из медиа" disabled={!!busy}
                        onClick={() => deleteRepl(r.name)}
                        style={{ position: 'absolute', top: -6, right: -6, width: 16, height: 16, lineHeight: '14px', fontSize: 11, padding: 0, borderRadius: '50%', border: '1px solid var(--border, #2a2a2a)', background: '#1f1f1f', color: '#f87171', cursor: 'pointer' }}>
                  {busy === 'del-' + r.name ? '·' : '×'}
                </button>
                <img src={api.replacementFileUrl(sid, lid, r.name)} alt={r.name}
                     onClick={() => setPreview({ url: api.replacementFileUrl(sid, lid, r.name), name: r.name })}
                     style={{ width: '100%', height: 44, objectFit: 'cover', borderRadius: 3, display: 'block', cursor: 'pointer' }} />
                <div className="dim" style={{ fontSize: 8, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 2 }}>{r.name}</div>
                {/* удалить фон (rembg) */}
                <button type="button" className="btn" title="Удалить фон (rembg)" disabled={!!busy}
                        onClick={() => removeBg(r.name)}
                        style={{ width: '100%', fontSize: 9, marginTop: 2, padding: '1px 0' }}>
                  {busy === 'bg-' + r.name ? '…' : <><Icon name="scissors" size={9} /> фон</>}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Картинки из комментариев задачи → добавить в замены */}
      {commentImages.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div className="dim" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
            Из комментариев задачи
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {commentImages.map((a, i) => (
              <div key={i} style={{ border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, padding: 3, width: 64, textAlign: 'center' }}>
                <img src={api.attachmentUrl(a.url)} alt={a.filename} title={'Превью: ' + a.filename}
                     onClick={() => setPreview({ url: api.attachmentUrl(a.url), name: a.filename })}
                     style={{ width: '100%', height: 44, objectFit: 'cover', borderRadius: 3, display: 'block', cursor: 'pointer' }} />
                <button className="btn" style={{ fontSize: 9, width: '100%', marginTop: 2 }}
                        disabled={busy === a.url} onClick={() => importComment(a)}>
                  {busy === a.url ? '…' : '+ в замены'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
      {note && <div className="dim small" style={{ marginTop: 4, color: '#4ade80' }}>{note}</div>}

      {/* Сопоставление: медиа ленда → замена */}
      <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {shown.length === 0 && <p className="dim small" style={{ margin: 0 }}>Медиа продукта не найдено. Нажми «все медиа на ленде», чтобы выбрать вручную.</p>}
        {shown.map((m) => {
          const repl = imageMap[m.name];
          const origUrl = api.landerFileUrl(sid, lid, m.path);
          return (
            <div key={m.path} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {/* мини-превью сразу — чтобы по картинке понять, что меняется */}
              {m.kind === 'video' ? (
                <video src={origUrl} muted preload="metadata"
                       onClick={() => setPreview({ url: origUrl, name: m.name })}
                       title={'Видео на ленде: ' + m.name}
                       style={{ flexShrink: 0, width: 40, height: 40, objectFit: 'cover', borderRadius: 4, cursor: 'pointer', background: '#000' }} />
              ) : (
                <img src={origUrl} alt={m.name} loading="lazy"
                     onClick={() => setPreview({ url: origUrl, name: m.name })}
                     title={'Открыть оригинал: ' + m.name}
                     style={{ flexShrink: 0, width: 40, height: 40, objectFit: 'contain', borderRadius: 4, cursor: 'pointer', background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border, #2a2a2a)' }} />
              )}
              <span style={{ flexShrink: 0, fontSize: 8, fontWeight: 700, color: '#fff', background: m.kind === 'video' ? '#f59e0b' : '#38bdf8', borderRadius: 3, padding: '1px 4px' }}>
                {m.kind === 'video' ? 'VIDEO' : 'IMG'}
              </span>
              <span className="mono small" title={m.path} style={{ flex: '0 0 90px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11 }}>
                {m.name}
              </span>
              {m.kind === 'image' && (
                <button type="button" className="btn" style={{ fontSize: 13, whiteSpace: 'nowrap', padding: '2px 6px' }}
                        title="Нейро-правка картинки (GPT Image 2)" onClick={() => setEditMedia(m)}>
                  <Icon name="wand" size={13} />
                </button>
              )}
              <span className="dim">→</span>
              <ReplSelect
                value={repl || ''}
                taskOptions={taskRepl.map((r) => r.name)}
                assetOptions={assets}
                urlFor={replUrl}
                onChange={(name) => (name ? setMap(m.name, name) : clearMap(m.name))}
              />
              {repl ? (
                <>
                  <img src={replUrl(repl)} alt=""
                       onClick={() => setPreview({ url: replUrl(repl), name: repl })}
                       title={'Превью замены: ' + repl}
                       style={{ width: 32, height: 32, objectFit: 'cover', borderRadius: 4, cursor: 'pointer' }} />
                  <button type="button" className="btn" style={{ fontSize: 13, padding: '2px 6px' }}
                          title="Вернуть оригинал на ленде" onClick={() => clearMap(m.name)}>
                    ↩
                  </button>
                </>
              ) : (
                <span className="dim small" style={{ width: 32, textAlign: 'center' }}>—</span>
              )}
            </div>
          );
        })}
      </div>
      <div className="dim small" style={{ marginTop: 6 }}>
        Замены задачи изолированы и не засоряют общий список. «вернуть оригинал» убирает замену — после повторной адаптации вернётся исходное медиа. «превью» открывает фото/гиф/видео.
      </div>

      {preview && <MediaModal url={preview.url} name={preview.name} onClose={() => setPreview(null)} />}
      {editMedia && (
        <ImageEditModal
          sid={sid} lid={lid} media={editMedia}
          sessionImages={[
            ...taskRepl.map((r) => ({ url: api.replacementFileUrl(sid, lid, r.name), name: r.name, source: 'Замены задачи' })),
            ...commentImages.map((a) => ({ url: api.attachmentUrl(a.url), name: a.filename, source: 'AdRobot' })),
          ]}
          onClose={() => setEditMedia(null)}
          onDone={(res) => {
            // Закрепляем нейро-правку в image_map, чтобы она не пропала при
            // повторной адаптации (output пересобирается из исходника).
            if (res?.image_map_key && res?.replacement) setMap(res.image_map_key, res.replacement);
            setEditMedia(null);
            loadRepl();      // показать новую замену neuro_* в списке
            onChanged?.();
          }}
        />
      )}
    </div>
  );
}

// Модалка нейро-правки картинки ленда (GPT Image 2): промпт + качество → замена.
function ImageEditModal({ sid, lid, media, sessionImages = [], onClose, onDone }: {
  sid: string; lid: string; media: LanderMedia;
  sessionImages?: { url: string; name: string; source: string }[];
  onClose: () => void; onDone: (res?: { image_map_key?: string; replacement?: string }) => void;
}) {
  const [prompt, setPrompt] = useState('');
  const [quality, setQuality] = useState('low');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [refs, setRefs] = useState<{ file: File; preview: string }[]>([]);  // референсные фото (2-е фото и т.д.)
  const [picking, setPicking] = useState(false);  // открыт ли выбор из сессии
  const origUrl = api.landerFileUrl(sid, lid, media.path);

  // Добавить картинку из сессии (замена/AdRobot) как референс — качаем blob.
  const addFromUrl = async (url: string, name: string) => {
    try {
      const blob = await fetch(url).then((r) => r.blob());
      const file = new File([blob], name || 'ref', { type: blob.type || 'image/png' });
      setRefs((r) => [...r, { file, preview: URL.createObjectURL(blob) }]);
      setPicking(false);
    } catch {
      setError('Не удалось добавить картинку из сессии');
    }
  };

  const run = async () => {
    if (!prompt.trim()) return;
    setBusy(true); setError('');
    try {
      const res = await api.mediaEdit(sid, lid, media.path, prompt.trim(), quality, refs.map((r) => r.file));
      onDone(res);
    } catch (e: any) {
      setError(e.message || 'Ошибка генерации');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div onClick={busy ? undefined : onClose}
         style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(0,0,0,0.8)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '2rem' }}>
      <div onClick={(e) => e.stopPropagation()}
           style={{ background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border, #2a2a2a)', borderRadius: 12, padding: '1rem', width: 420, maxWidth: '90vw', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>Нейро-правка картинки</div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          <img src={origUrl} alt={media.name} style={{ width: 80, height: 80, objectFit: 'contain', background: '#000', borderRadius: 6 }} />
          <div className="dim small" style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>{media.name}</div>
        </div>
        <textarea className="form-input" value={prompt} onChange={(e) => setPrompt(e.target.value)}
                  placeholder="Что изменить? Напр.: «переведи текст на польский» или «замени продукт на тот, что на фото 2»"
                  rows={3} style={{ fontSize: 13, resize: 'vertical' }} disabled={busy} />

        {/* Доп. референсные фото: «фото 1» = картинка ленда выше, «фото 2…» — приложенные */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span className="dim small">Доп. фото (референс)</span>
            <label className="btn" style={{ fontSize: 11, cursor: 'pointer' }}>
              + загрузить
              <input type="file" accept="image/*" multiple style={{ display: 'none' }} disabled={busy}
                     onChange={(e) => {
                       const files = Array.from(e.target.files || []);
                       setRefs((r) => [...r, ...files.map((f) => ({ file: f, preview: URL.createObjectURL(f) }))]);
                       e.currentTarget.value = '';
                     }} />
            </label>
            {sessionImages.length > 0 && (
              <button type="button" className="btn" style={{ fontSize: 11 }} disabled={busy}
                      onClick={() => setPicking((v) => !v)}>
                {picking ? 'скрыть' : '+ из сессии'}
              </button>
            )}
          </div>

          {/* Выбор из загруженных в сессию / из AdRobot */}
          {picking && sessionImages.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', padding: 6, border: '1px solid var(--border)', borderRadius: 6, maxHeight: 160, overflowY: 'auto' }}>
              {sessionImages.map((si, i) => (
                <div key={i} title={`${si.name} · ${si.source}`} onClick={() => addFromUrl(si.url, si.name)}
                     style={{ cursor: 'pointer', width: 56, textAlign: 'center' }}>
                  <img src={si.url} alt={si.name}
                       style={{ width: 56, height: 48, objectFit: 'cover', borderRadius: 4, border: '1px solid var(--border)', display: 'block' }} />
                  <span className="dim" style={{ fontSize: 8 }}>{si.source}</span>
                </div>
              ))}
            </div>
          )}

          {refs.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {refs.map((f, i) => (
                <div key={i} style={{ position: 'relative' }} title={f.file.name}>
                  <img src={f.preview} alt={f.file.name}
                       style={{ width: 48, height: 48, objectFit: 'cover', borderRadius: 4, border: '1px solid var(--border)' }} />
                  <span style={{ position: 'absolute', left: 2, bottom: 2, fontSize: 8, fontWeight: 700, color: '#fff', background: 'rgba(0,0,0,0.6)', borderRadius: 3, padding: '0 3px' }}>
                    фото {i + 2}
                  </span>
                  {!busy && (
                    <button type="button" onClick={() => setRefs((r) => r.filter((_, j) => j !== i))}
                            style={{ position: 'absolute', top: -6, right: -6, width: 16, height: 16, borderRadius: 999, border: 'none', cursor: 'pointer', background: 'var(--danger, #e35b5b)', color: '#fff', fontSize: 10, lineHeight: 1 }}>×</button>
                  )}
                </div>
              ))}
            </div>
          )}
          {refs.length > 0 && (
            <div className="dim small">Картинка ленда выше = «фото 1», приложенные = «фото 2, 3…». Ссылайся на них в промпте.</div>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span className="dim small">Качество</span>
          <select className="form-input" value={quality} onChange={(e) => setQuality(e.target.value)} disabled={busy} style={{ fontSize: 12, padding: '2px 6px' }}>
            <option value="low">low (~1.8 ₽)</option>
            <option value="medium">medium (~6.85 ₽)</option>
            <option value="high">high (~27 ₽)</option>
          </select>
          <span className="dim small">результат заменит картинку на ленде</span>
        </div>
        {error && <div style={{ color: '#f87171', fontSize: 12 }}>{error}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose} disabled={busy} style={{ fontSize: 13 }}>Отмена</button>
          <button className="btn btn-primary" onClick={run} disabled={busy || !prompt.trim()} style={{ fontSize: 13 }}>
            {busy ? 'Генерирую…' : 'Сгенерировать'}
          </button>
        </div>
      </div>
    </div>
  );
}

// Скачать лендинг по ссылке на сайт (webscrapbook-подобно) → ленд в сессию.
// С опциональным прокси (обход гео-защиты), прокси сохраняются.
function SiteScrapeAdder({ sid, taskUid, onAdded }: {
  sid: string; taskUid?: string; onAdded: () => void;
}) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [proxies, setProxies] = useState<{ id: string; label: string; server: string; geo: string }[]>([]);
  const [proxyId, setProxyId] = useState('');
  const [addingProxy, setAddingProxy] = useState(false);
  const [newProxy, setNewProxy] = useState('');
  const [newLabel, setNewLabel] = useState('');
  const [newGeo, setNewGeo] = useState('');

  const loadProxies = () => api.proxies().then(setProxies).catch(() => {});
  useEffect(() => { loadProxies(); }, []);

  const scrape = async () => {
    if (!url.trim()) return;
    setBusy(true); setErr('');
    try {
      await api.landerFromSite(sid, url.trim(), taskUid, proxyId || undefined);
      setUrl(''); onAdded();
    } catch (e: any) {
      setErr(e.message || 'Не удалось скачать сайт');
    } finally {
      setBusy(false);
    }
  };

  const saveProxy = async () => {
    if (!newProxy.trim()) return;
    try {
      const p = await api.addProxy(newProxy.trim(), newLabel.trim() || undefined, newGeo.trim() || undefined);
      await loadProxies();
      setProxyId(p.id); setNewProxy(''); setNewLabel(''); setNewGeo(''); setAddingProxy(false);
    } catch (e: any) {
      setErr(e.message || 'Не сохранить прокси');
    }
  };

  const delProxy = async (id: string) => {
    await api.deleteProxy(id).catch(() => {});
    if (proxyId === id) setProxyId('');
    loadProxies();
  };

  const importDolphin = async () => {
    setErr('');
    try {
      const r = await api.importDolphinProxies();
      await loadProxies();
      setErr(`Импортировано из Dolphin: ${r.imported} (всего в библиотеке ${r.total})`);
    } catch (e: any) {
      setErr(e.message || 'Не удалось импортировать из Dolphin');
    }
  };

  const proxyLabel = (p: { label: string; server: string; geo: string }) =>
    `${p.geo ? `[${p.geo}] ` : ''}${p.label || p.server}`;

  return (
    <div style={{ marginTop: 8 }}>
      <div className="dim" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
        Скачать сайт по ссылке
      </div>
      <input className="form-input" value={url} onChange={(e) => setUrl(e.target.value)}
             onKeyDown={(e) => { if (e.key === 'Enter') scrape(); }}
             placeholder="https://site.com/landing" disabled={busy}
             style={{ fontSize: 11, width: '100%' }} />

      {/* выбор прокси (обход гео-защиты) */}
      <select className="form-input" value={proxyId} onChange={(e) => setProxyId(e.target.value)}
              disabled={busy} style={{ fontSize: 11, width: '100%', marginTop: 4 }} title="Прокси для обхода гео-защиты">
        <option value="">без прокси</option>
        {proxies.map((p) => <option key={p.id} value={p.id}>{proxyLabel(p)}</option>)}
      </select>
      <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
        <button className="btn" style={{ fontSize: 10, flex: 1 }} title="Добавить прокси вручную"
                onClick={() => setAddingProxy((v) => !v)}>{addingProxy ? 'отмена' : '+ прокси'}</button>
        <button className="btn" style={{ fontSize: 10, flex: 1 }} title="Импорт прокси из Dolphin Anty"
                onClick={importDolphin}>↻ Dolphin</button>
        {proxyId && (
          <button className="btn" style={{ fontSize: 10 }} title="Удалить выбранный прокси"
                  onClick={() => delProxy(proxyId)}>удалить</button>
        )}
      </div>

      {addingProxy && (
        <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 3, padding: 6, border: '1px solid var(--border, #2a2a2a)', borderRadius: 6 }}>
          <input className="form-input" value={newProxy} onChange={(e) => setNewProxy(e.target.value)}
                 placeholder="host:port:user:pass или user:pass@host:port" style={{ fontSize: 11 }} />
          <div style={{ display: 'flex', gap: 3 }}>
            <input className="form-input" value={newLabel} onChange={(e) => setNewLabel(e.target.value)}
                   placeholder="метка" style={{ fontSize: 11, flex: 1 }} />
            <input className="form-input" value={newGeo} onChange={(e) => setNewGeo(e.target.value)}
                   placeholder="гео" style={{ fontSize: 11, width: 50 }} />
          </div>
          <button className="btn btn-primary" style={{ fontSize: 11 }} disabled={!newProxy.trim()} onClick={saveProxy}>
            Сохранить прокси
          </button>
        </div>
      )}

      <button className="btn" onClick={scrape} disabled={busy || !url.trim()}
              style={{ fontSize: 11, width: '100%', marginTop: 4 }}>
        {busy ? 'Скачиваю сайт…' : '↓ скачать лендинг'}
      </button>
      {err && <div className="dim small" style={{ color: '#f87171', marginTop: 4 }}>{err}</div>}
    </div>
  );
}

// Архивы из комментариев привязанной задачи → добавить как ленд в сессию.
function TaskArchivesAdder({ sid, taskUids, onAdded }: {
  sid: string; taskUids: string[]; onAdded: () => void;
}) {
  const [archives, setArchives] = useState<CommentAttachment[]>([]);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');

  useEffect(() => {
    let cancelled = false;
    Promise.all(taskUids.map((u) => api.taskDetail(u).catch(() => null)))
      .then((details) => {
        if (cancelled) return;
        const arr: CommentAttachment[] = [];
        for (const d of details) {
          if (!d) continue;
          for (const a of d.attachments) {
            const usable = a.url.includes('robotmediaassets.com') || /drive\.google\.com|docs\.google\.com|disk\.yandex|yadi\.sk/i.test(a.url);
            if (a.kind === 'archive' && usable && !arr.some(x => x.url === a.url)) {
              arr.push(a);
            }
          }
        }
        setArchives(arr);
      });
    return () => { cancelled = true; };
  }, [taskUids.join(',')]);

  const add = async (a: CommentAttachment) => {
    setBusy(a.url); setErr('');
    try {
      await api.landerFromUrl(sid, a.url, a.filename);
      onAdded();
    } catch (e: any) {
      setErr(e.message || 'Ошибка');
    } finally {
      setBusy('');
    }
  };

  if (archives.length === 0) return null;
  return (
    <div style={{ marginTop: 8 }}>
      <div className="dim" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>
        Архивы из задачи
      </div>
      {archives.map((a) => (
        <button key={a.url} className="btn" style={{ fontSize: 10, width: '100%', marginTop: 4, textAlign: 'left', whiteSpace: 'normal' }}
                disabled={busy === a.url} onClick={() => add(a)} title={a.filename}>
          {busy === a.url ? '…' : '+ '}{a.filename}
        </button>
      ))}
      {err && <div className="dim small" style={{ color: '#f87171', marginTop: 4 }}>{err}</div>}
    </div>
  );
}

// Чат-агент по ленду (AITUNNEL / Kimi). Tool-calling: показывает вызовы
// инструментов и их результаты компактно, финальный текст — обычным пузырём.
function LanderChat({ sid, lid, onLanderUpdate }: {
  sid: string; lid: string; onLanderUpdate: (lander: LanderState) => void;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [streamText, setStreamText] = useState('');  // живой текст текущего ответа
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [model, setModel] = useState('');  // '' = дефолтная модель
  const [error, setError] = useState('');
  const endRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);  // для досрочной остановки

  useEffect(() => {
    api.aiStatus().then((st) => {
      setStatus(st);
      // По умолчанию: локальная модель (бесплатно), иначе Qwen (vision), иначе первая.
      if (!model) {
        const ids = (st.models || []).map((m) => m.id);
        const dflt = ids.find((id) => id.startsWith('local:'))
          || ids.find((id) => id.includes('qwen')) || ids[0] || st.model || '';
        setModel(dflt);
      }
    }).catch(() => setStatus({ configured: false, model: null, balance: null }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    api.chatHistory(sid, lid).then((r) => setMessages(r.messages)).catch(() => {});
  }, [sid, lid]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages, streamText, sending]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput(''); setError('');
    setMessages((m) => [...m, { role: 'user', content: text, ts: Date.now() / 1000 }]);
    setSending(true); setStreamText('');
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let cur = '';  // аккумулятор текста текущего ассистентского ответа
    const commit = () => {
      if (cur) { const c = cur; setMessages((m) => [...m, { role: 'assistant', content: c }]); cur = ''; setStreamText(''); }
    };
    try {
      await chatStream(sid, lid, text, (ev) => {
        if (ev.type === 'token') { cur += ev.text; setStreamText(cur); }
        else if (ev.type === 'tool_call') {
          commit();
          setMessages((m) => [...m, { role: 'assistant', content: null, tool_calls: [{ id: '', type: 'function', function: { name: ev.name, arguments: '' } }] }]);
        }
        else if (ev.type === 'tool_result') {
          setMessages((m) => [...m, { role: 'tool', name: ev.name, content: ev.content }]);
        }
        else if (ev.type === 'assistant_message') { commit(); }
        else if (ev.type === 'done') { if (ev.lander) onLanderUpdate(ev.lander); }
        else if (ev.type === 'error') { setError(ev.error); }
      }, ctrl.signal, model || undefined);
      commit();  // на случай текста без завершающего assistant_message
    } catch (e: any) {
      commit();  // сохранить уже полученный текст
      if (ctrl.signal.aborted || e?.name === 'AbortError') {
        setMessages((m) => [...m, { role: 'tool', name: 'система', content: 'Остановлено пользователем' }]);
      } else {
        setError(e.message || 'Ошибка чата');
      }
    } finally {
      abortRef.current = null;
      setSending(false); setStreamText('');
      // Обновляем баланс после каждого ответа нейросети.
      api.aiStatus().then(setStatus).catch(() => {});
    }
  };

  const stop = () => { abortRef.current?.abort(); };

  const clear = async () => {
    if (!confirm('Очистить историю чата по этому ленду?')) return;
    try { await api.chatClear(sid, lid); setMessages([]); } catch { /* ignore */ }
  };

  if (status && !status.configured) {
    return (
      <div style={{ padding: '1rem' }}>
        <p className="dim small">Чат-агент не настроен. Добавь <code>AITUNNEL_API_KEY</code> в <code>backend/.env</code> и перезапусти бэкенд.</p>
        <p className="dim small">Ключ — из личного кабинета aitunnel.ru. Модель по умолчанию: <code>kimi-k2.7-code</code>.</p>
      </div>
    );
  }

  // Только видимые пузыри: user + assistant с текстом. Вызовы/результаты — компактно.
  const visible = messages.filter((m) =>
    m.role === 'user'
    || (m.role === 'assistant' && ((m.content || '').trim() || (m.tool_calls && m.tool_calls.length)))
    || m.role === 'tool');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0.5rem 0.7rem', borderBottom: '1px solid var(--border, #2a2a2a)', flexShrink: 0 }}>
        {status?.models && status.models.length > 0 ? (
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={sending}
            title="Модель для этого чата (дешевле/дороже по токенам)"
            style={{ fontSize: 11, padding: '2px 6px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-elevated)', color: 'var(--text)', maxWidth: 220 }}
          >
            {status.models.map((mo) => <option key={mo.id} value={mo.id}>{mo.label}</option>)}
          </select>
        ) : (
          <span className="dim small">{status?.model || 'AI'}</span>
        )}
        {status?.balance != null && <span className="dim small">· баланс {status.balance.toFixed(0)} ₽</span>}
        <div style={{ flex: 1 }} />
        {messages.length > 0 && <button className="btn" style={{ fontSize: 10 }} onClick={clear}>Очистить</button>}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0.7rem', display: 'flex', flexDirection: 'column', gap: '0.5rem', minHeight: 0 }}>
        {visible.length === 0 && (
          <p className="dim small" style={{ margin: 0 }}>
            Опиши, что сделать с лендом — например: «адаптируй под целевой оффер, цену возьми из задачи» или «подтяни фото оффера и замени им банку».
          </p>
        )}
        {visible.map((m, i) => <ChatBubble key={i} m={m} />)}
        {streamText && <ChatBubble m={{ role: 'assistant', content: streamText }} />}
        {sending && !streamText && <div className="dim small">ИИ думает…</div>}
        <div ref={endRef} />
      </div>

      {error && <div style={{ padding: '0.4rem 0.7rem', color: '#f87171', fontSize: 12 }}>{error}</div>}

      <div style={{ display: 'flex', gap: 6, padding: '0.6rem', borderTop: '1px solid var(--border, #2a2a2a)', flexShrink: 0 }}>
        <textarea
          className="form-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Сообщение агенту… (Enter — отправить)"
          rows={2}
          style={{ flex: 1, resize: 'none', fontSize: 13 }}
          disabled={sending}
        />
        {sending ? (
          <button className="btn" onClick={stop} title="Остановить нейросеть"
                  style={{ alignSelf: 'flex-end', color: 'var(--danger, #e35b5b)', borderColor: 'var(--danger, #e35b5b)' }}>
            ◼ Стоп
          </button>
        ) : (
          <button className="btn btn-primary" onClick={send} disabled={!input.trim()} style={{ alignSelf: 'flex-end' }}>
            Отпр.
          </button>
        )}
      </div>
    </div>
  );
}

function ChatBubble({ m }: { m: ChatMessage }) {
  if (m.role === 'tool') {
    return (
      <details style={{ fontSize: 11, color: 'var(--text-muted)', alignSelf: 'flex-start', maxWidth: '95%' }}>
        <summary style={{ cursor: 'pointer' }}>результат: {m.name}</summary>
        <pre style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: '#0d0e12', borderRadius: 6, padding: '0.4rem 0.6rem', maxHeight: 160, overflow: 'auto' }}>
          {m.content || ''}
        </pre>
      </details>
    );
  }
  if (m.role === 'assistant') {
    const text = (m.content || '').trim();  // пробельный контент = пустой (не рисуем пузырь)
    return (
      <>
        {text && (
          <div style={{
            alignSelf: 'flex-start', maxWidth: '90%',
            background: 'var(--bg-elevated, #141414)', color: 'var(--text)',
            border: '1px solid var(--border, #2a2a2a)',
            borderRadius: 10, padding: '0.5rem 0.7rem', fontSize: 13, wordBreak: 'break-word',
          }}>
            <Markdown text={text} />
          </div>
        )}
        {m.tool_calls && m.tool_calls.length > 0 && (
          <div className="dim small" style={{ alignSelf: 'flex-start' }}>
            {m.tool_calls.map((tc, i) => <div key={i}>→ вызывает <code>{tc.function.name}</code></div>)}
          </div>
        )}
      </>
    );
  }
  return (
    <div style={{
      alignSelf: 'flex-end', maxWidth: '90%',
      background: 'var(--accent, #7c6fff)', color: '#fff',
      borderRadius: 10, padding: '0.5rem 0.7rem', fontSize: 13, wordBreak: 'break-word',
    }}>
      <span style={{ whiteSpace: 'pre-wrap' }}>{m.content}</span>
    </div>
  );
}

// Панель перевода ленда: стриминг с живым прогрессом, сразу применяется к ленду.
function TranslatePanel({ sid, lid, onApplied }: { sid: string; lid: string; onApplied: () => void }) {
  const [lang, setLang] = useState('');
  const [languages, setLanguages] = useState<{ code: string; name: string }[]>([]);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0 });
  const [items, setItems] = useState<{ original: string; translated: string }[]>([]);
  const [info, setInfo] = useState<{ lang_name: string; rtl: boolean } | null>(null);
  const [doneInfo, setDoneInfo] = useState<{ applied: number } | null>(null);
  const [error, setError] = useState('');
  const [aborted, setAborted] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => { api.translateLanguages().then(setLanguages).catch(() => {}); }, []);
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight }); }, [items]);
  useEffect(() => () => abortRef.current?.abort(), []); // размонтирование = стоп

  const run = async () => {
    setRunning(true); setError(''); setItems([]); setDoneInfo(null);
    setProgress({ done: 0, total: 0 }); setInfo(null); setAborted(false);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await translateStream(sid, lid, lang || undefined, (ev) => {
        if (ev.type === 'start') { setInfo({ lang_name: ev.lang_name, rtl: ev.rtl }); setProgress({ done: 0, total: ev.total }); }
        else if (ev.type === 'block') { setItems((prev) => [...prev, ...ev.items]); setProgress({ done: ev.done, total: ev.total }); }
        else if (ev.type === 'progress') { setProgress({ done: ev.done, total: ev.total }); if (ev.warn) setError(ev.warn); }
        else if (ev.type === 'done') { setDoneInfo({ applied: ev.applied }); onApplied(); }
        else if (ev.type === 'error') { setError(ev.error); }
      }, ctrl.signal);
    } catch (e: any) {
      if (e?.name === 'AbortError') setAborted(true); // остановлено — ленд НЕ изменён
      else setError(e.message || 'Ошибка перевода');
    } finally {
      abortRef.current = null;
      setRunning(false);
    }
  };

  const stop = () => { abortRef.current?.abort(); };

  const pct = progress.total ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div style={{ marginTop: '0.8rem', border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, padding: '0.8rem', background: 'var(--bg, #0d0e12)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Перевод ленда</span>
        <select className="form-input" value={lang} onChange={(e) => setLang(e.target.value)}
                disabled={running} style={{ fontSize: 12, width: 220, padding: '2px 6px' }}>
          <option value="">авто по гео</option>
          {languages.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
        </select>
        {running ? (
          <button className="btn" onClick={stop}
                  style={{ fontSize: 12, background: '#7f1d1d', color: '#fca5a5' }}>
            ◼ Стоп
          </button>
        ) : (
          <button className="btn btn-primary" onClick={run} style={{ fontSize: 12 }}>
            Перевести
          </button>
        )}
        {running && <span className="dim small">Перевожу… стоп = ленд не изменится</span>}
        {info?.rtl && <span className="dim small">RTL · dir=rtl</span>}
      </div>

      {error && <div style={{ padding: '0.5rem 0.7rem', background: 'rgba(245,158,11,0.12)', color: '#f59e0b', borderRadius: 6, fontSize: 12, marginBottom: 8 }}>{error}</div>}

      {(running || progress.total > 0) && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }} className="dim">
            <span>{info?.lang_name || ''}</span>
            <span>{progress.done}/{progress.total} блоков{doneInfo ? ` · применено к ${doneInfo.applied} файл(ам)` : ''}</span>
          </div>
          <div style={{ height: 6, background: 'var(--bg-elevated, #141414)', borderRadius: 4, overflow: 'hidden', marginTop: 3 }}>
            <div style={{ height: '100%', width: `${pct}%`, background: doneInfo ? '#4ade80' : 'var(--accent, #7c6fff)', transition: 'width 0.3s' }} />
          </div>
        </div>
      )}

      {items.length > 0 && (
        <div ref={listRef} style={{ maxHeight: 280, overflowY: 'auto', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6 }}>
          {items.map((d, i) => (
            <div key={i} style={{ padding: '6px 8px', borderBottom: '1px solid var(--border, #2a2a2a)', fontSize: 12 }}>
              <div style={{ color: '#f87171', textDecoration: 'line-through', opacity: 0.75, wordBreak: 'break-word' }}>{d.original}</div>
              <div style={{ color: '#4ade80', wordBreak: 'break-word', direction: info?.rtl ? 'rtl' : 'ltr' }}>{d.translated}</div>
            </div>
          ))}
        </div>
      )}

      {doneInfo && !running && (
        <div className="dim small" style={{ marginTop: 6 }}>Готово · переведено и применено к ленду. Проверь превью; вычитай текст перед заливкой.</div>
      )}
      {aborted && !running && (
        <div className="dim small" style={{ marginTop: 6, color: '#f59e0b' }}>Остановлено · перевод НЕ применён, ленд без изменений.</div>
      )}
    </div>
  );
}

// Панель заливки ленда в Keitaro: показывает план (dry-run, без Keitaro),
// затем по явному подтверждению запускает реальное создание оффера.
function KeitaroUploadPanel({ sid, lid, lander, onChanged }: {
  sid: string; lid: string; lander?: LanderState; onChanged?: () => void;
}) {
  // Уже залитый ленд: восстанавливаем экран «готово» из сохранённых параметров.
  const ap = (lander?.adapt_params || {}) as Record<string, any>;
  // Статус заливки пишется и в ЛОКАЛЬНЫЙ объект ленда: сервер сохраняет его в
  // adapt_params, но сессия на клиенте не перечитывается (поллинг остановлен,
  // лендов в работе нет) — без этого закрытие/открытие панели или переключение
  // вкладок «теряло» экран «залито» до F5.
  const persist = (patch: Record<string, any>) => {
    if (lander) lander.adapt_params = { ...(lander.adapt_params || {}), ...patch };
    onChanged?.();
  };
  const [plan, setPlan] = useState<KeitaroPlan | null>(null);
  const [type, setType] = useState<string>('');       // '' = авто
  const [network, setNetwork] = useState<string>(''); // '' = авто (с донора)
  const [adult, setAdult] = useState(false);          // [pl fi -] → [pl fi adult]
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [steps, setSteps] = useState<string[]>([]);   // живой прогресс заливки
  const [created, setCreated] = useState<KeitaroPlan | null>(null);  // оффер создан, ждём подтв. id
  const [selectedId, setSelectedId] = useState<number | ''>('');     // выбранный id для rename
  const [renaming, setRenaming] = useState(false);
  const [renamed, setRenamed] = useState<{ offer_id: number; final_name: string } | null>(
    ap.keitaro_offer_id && ap.keitaro_name
      ? { offer_id: ap.keitaro_offer_id, final_name: ap.keitaro_name } : null);
  const [campaign, setCampaign] = useState<{ campaign_url: string; campaign_name: string } | null>(
    ap.campaign_url ? { campaign_url: ap.campaign_url, campaign_name: ap.campaign_name || '' } : null);
  const [campaignBusy, setCampaignBusy] = useState(false);
  // Варианты задачи AdRobot (Add variant / Move all / Submit for review).
  // Задача определяется НА СЕРВЕРЕ по task_uid самого ленда — в объединённых
  // сессиях вариант не может попасть в чужую задачу.
  const [variantAdded, setVariantAdded] = useState<boolean>(!!ap.variant_added);
  const [variantsMoved, setVariantsMoved] = useState<string>(ap.variants_moved || '');
  const [reviewSent, setReviewSent] = useState<boolean>(!!ap.review_submitted);
  const [taskBusy, setTaskBusy] = useState('');
  const [error, setError] = useState('');

  const doAddVariant = async () => {
    setTaskBusy('variant'); setError('');
    try {
      await api.addTaskVariant(sid, lid);
      setVariantAdded(true);
      persist({ variant_added: true });
    } catch (e: any) {
      setError(e.message || 'Не удалось добавить вариант');
    } finally { setTaskBusy(''); }
  };

  const doMoveVariants = async (scope: 'private' | 'public') => {
    setTaskBusy(scope); setError('');
    try {
      await api.moveTaskVariants(sid, lid, scope);
      setVariantsMoved(scope);
      persist({ variants_moved: scope });
    } catch (e: any) {
      setError(e.message || 'Не удалось переместить варианты');
    } finally { setTaskBusy(''); }
  };

  const doSubmitReview = async () => {
    setTaskBusy('review'); setError('');
    try {
      await api.submitTaskReview(sid, lid);
      setReviewSent(true);
      persist({ review_submitted: true });
    } catch (e: any) {
      setError(e.message || 'Не удалось отправить на ревью');
    } finally { setTaskBusy(''); }
  };

  const doTestCampaign = async () => {
    setCampaignBusy(true); setError('');
    try {
      const r = await api.testCampaign(sid, lid);
      setCampaign({ campaign_url: r.campaign_url, campaign_name: r.campaign_name });
      persist({ campaign_url: r.campaign_url, campaign_name: r.campaign_name });
    } catch (e: any) {
      setError(e.message || 'Не удалось создать тестовую кампанию');
    } finally {
      setCampaignBusy(false);
    }
  };

  const loadPlan = (t: string, a = adult) => {
    setLoading(true); setError(''); setCreated(null);
    api.keitaroPlan(sid, lid, t || undefined, a)
      .then(setPlan)
      .catch((e) => setError(e.message || 'Не удалось собрать план'))
      .finally(() => setLoading(false));
  };
  useEffect(() => { loadPlan(type); /* eslint-disable-next-line */ }, [sid, lid, type, adult]);

  const doUpload = async () => {
    setUploading(true); setError(''); setSteps([]);
    try {
      await keitaroUploadStream(sid, lid, { type: type || undefined, network: network || undefined, adult }, (ev) => {
        if (ev.type === 'step' && ev.message) setSteps((s) => [...s, ev.message!]);
        else if (ev.type === 'done') {
          const r = ev.result as KeitaroPlan;
          if (r.mode === 'uploaded' && r.offer_id && r.final_name) {
            // авто-переименование прошло на бэке — сразу экран «готово»
            setRenamed({ offer_id: r.offer_id, final_name: r.final_name });
            persist({ keitaro_offer_id: r.offer_id, keitaro_name: r.final_name });
          } else {
            // fallback: id не определён однозначно — ручной выбор
            setCreated(r);
            setSelectedId(r.id_confident && r.id_best ? r.id_best : '');
          }
        }
        else if (ev.type === 'error') setError(ev.error || 'Ошибка заливки');
      });
    } catch (e: any) {
      setError(e.message || 'Ошибка заливки');
    } finally {
      setUploading(false);
    }
  };

  // Финальное имя для выбранного id (id дописывается в начало названия без id).
  const finalNameFor = (id: number | ''): string => {
    if (!created || id === '') return '';
    return `${id} ${created.name_no_id || ''}`.trim();
  };

  const confirmRename = async () => {
    if (selectedId === '' || !created) return;
    setRenaming(true); setError('');
    try {
      const r = await api.keitaroRename(sid, lid, selectedId as number, type || undefined, adult);
      setRenamed({ offer_id: r.offer_id, final_name: r.final_name });
      persist({ keitaro_offer_id: r.offer_id, keitaro_name: r.final_name });
    } catch (e: any) {
      setError(e.message || 'Ошибка переименования');
    } finally {
      setRenaming(false);
    }
  };

  const Row = ({ k, v }: { k: string; v: string }) => (
    <div style={{ display: 'flex', gap: 8, fontSize: 12 }}>
      <span className="dim" style={{ flex: '0 0 110px' }}>{k}</span>
      <span style={{ wordBreak: 'break-word' }}>{v}</span>
    </div>
  );

  return (
    <div style={{ marginTop: '0.8rem', border: '1px solid var(--border, #2a2a2a)', borderRadius: 8, padding: '0.8rem', background: 'var(--bg, #0d0e12)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Заливка в Keitaro</span>
        <span className="dim small">— план (Keitaro не трогается до кнопки)</span>
      </div>

      {loading && <p className="dim small" style={{ margin: 0 }}>Собираю план…</p>}
      {error && <div style={{ padding: '0.5rem 0.7rem', background: 'rgba(239,68,68,0.12)', color: '#f87171', borderRadius: 6, fontSize: 12, marginBottom: 8 }}>{error}</div>}

      {plan && !created && !renamed && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <Row k="Группа" v={plan.group} />
          <Row k="Продукт" v={plan.product} />
          <Row k="Гео / язык" v={`${plan.geo_id} · ${plan.lang}`} />
          <Row k="Шаблон страны" v={`ввод "${plan.country_query}" → выбор страны`} />
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
            <span className="dim" style={{ flex: '0 0 110px' }}>Тип сайта</span>
            <select className="form-input" value={type} onChange={(e) => setType(e.target.value)} style={{ fontSize: 12, padding: '2px 6px' }}>
              <option value="">авто ({plan.site_type})</option>
              <option value="land">land</option>
              <option value="pl">pl</option>
              <option value="vsl">vsl</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
            <span className="dim" style={{ flex: '0 0 110px' }}>Сеть</span>
            <input className="form-input" value={network} onChange={(e) => setNetwork(e.target.value)}
                   placeholder="авто (с донора), напр. 75" style={{ fontSize: 12, padding: '2px 6px', width: 200 }} />
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
            <span className="dim" style={{ flex: '0 0 110px' }}>Adult</span>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <input type="checkbox" checked={adult} onChange={(e) => setAdult(e.target.checked)} />
              <span className="dim small">пометка в названии: [pl fi -] → [pl fi adult]</span>
            </label>
          </div>
          <Row k="Скобка" v={plan.bracket || '—'} />
          <Row k="Название" v={plan.name_template} />
          <div className="dim small" style={{ marginTop: 4 }}>
            Скобка <code>[ВЕРТИКАЛЬ-ГЕО]</code> построена из группы. Сеть — с донора (можно задать вручную). id в название дописывается автоматически после создания; ручной выбор появится, только если id не определится однозначно.
          </div>
          <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
            <button className="btn btn-primary" onClick={doUpload} disabled={uploading} style={{ fontSize: 13 }}>
              {uploading ? 'Создаю оффер в Keitaro…' : 'Создать оффер в Keitaro'}
            </button>
          </div>
          {steps.length > 0 && (
            <div style={{ marginTop: 8, padding: '0.5rem 0.7rem', background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12, maxHeight: 160, overflowY: 'auto' }}>
              {steps.map((s, i) => (
                <div key={i} style={{ display: 'flex', gap: 6 }}>
                  <span className="dim">{i + 1}.</span>
                  <span>{s}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Шаг подтверждения id: оффер создан, переименование требует выбора id */}
      {created && !renamed && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ padding: '0.5rem 0.7rem', background: 'rgba(232,168,87,0.14)', color: 'var(--warning, #e8a857)', borderRadius: 6, fontSize: 13 }}>
            <Icon name="check" size={13} /> Оффер <b>создан</b> в Keitaro (без id в названии). Выбери, какому id дописать id-префикс.
          </div>
          {!created.id_confident && (
            <div className="dim small" style={{ color: 'var(--warning, #e8a857)' }}>
              <Icon name="alert" size={12} /> Точное совпадение названия не найдено автоматически — ВНИМАТЕЛЬНО выбери именно только что созданный оффер (обычно с наибольшим id и БЕЗ цифрового префикса в названии). НЕ переименуй чужой старый оффер.
            </div>
          )}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
            <span className="dim" style={{ flex: '0 0 110px' }}>id оффера</span>
            <select className="form-input" value={selectedId === '' ? '' : String(selectedId)}
                    onChange={(e) => setSelectedId(e.target.value ? Number(e.target.value) : '')}
                    style={{ fontSize: 12, padding: '2px 6px', flex: 1 }}>
              <option value="">— выбери id —</option>
              {(created.id_candidates || []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.id} · {c.name}{c.has_id_prefix ? ' (уже с id-префиксом!)' : ''}
                  {created.id_best === c.id ? '  ← предполагаемый' : ''}
                </option>
              ))}
            </select>
          </div>
          {selectedId !== '' && (
            <Row k="Новое название" v={finalNameFor(selectedId)} />
          )}
          <div className="dim small">
            Сеть: {created.network || '—'} · скобка: {created.bracket || '—'}
          </div>
          <div style={{ marginTop: 4, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn btn-primary" onClick={confirmRename} disabled={renaming || selectedId === ''} style={{ fontSize: 13 }}>
              {renaming ? 'Переименовываю…' : 'Подтвердить id и переименовать'}
            </button>
            <button className="btn" onClick={() => { setCreated(null); loadPlan(type); }} disabled={renaming} style={{ fontSize: 13 }}>
              Пропустить (оставить без id)
            </button>
            <a className="btn" href="https://tlgk.host/admin/#!/offers" target="_blank" rel="noopener" style={{ fontSize: 13, textDecoration: 'none' }}>Открыть офферы ↗</a>
          </div>
        </div>
      )}

      {renamed && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ padding: '0.6rem 0.8rem', background: 'rgba(74,222,128,0.12)', color: '#4ade80', borderRadius: 6, fontSize: 13 }}>
            Готово: оффер <b>id {renamed.offer_id}</b> создан и переименован.<br />
            <code style={{ color: 'var(--text)' }}>{renamed.final_name}</code>
          </div>
          {/* Тестовая кампания: test mch <имя>, группа Andrei AM → ссылка */}
          {!campaign && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button className="btn btn-primary" onClick={doTestCampaign} disabled={campaignBusy} style={{ fontSize: 13 }}>
                {campaignBusy ? 'Создаю тестовую кампанию…' : <><Icon name="flask" size={13} /> Создать тестовую кампанию</>}
              </button>
              {campaignBusy && <span className="dim small">кампании создаются ~30-60с</span>}
            </div>
          )}
          {campaign && (
            <div style={{ padding: '0.6rem 0.8rem', background: 'rgba(56,189,248,0.10)', borderRadius: 6, fontSize: 13, display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span><Icon name="flask" size={12} /> Кампания: <code>{campaign.campaign_name}</code></span>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <a className="btn btn-primary" href={campaign.campaign_url} target="_blank" rel="noopener"
                   style={{ fontSize: 13, textDecoration: 'none' }}>
                  <Icon name="external" size={13} /> Открыть тестовый сайт
                </a>
                <code className="dim small" style={{ wordBreak: 'break-all' }}>{campaign.campaign_url}</code>
              </div>
            </div>
          )}
          {/* Варианты задачи AdRobot: вариант → move all → submit for review.
              Действия идут в ЗАДАЧУ ЭТОГО ленда (task_uid резолвит сервер). */}
          <div style={{ padding: '0.6rem 0.8rem', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontSize: 12, fontWeight: 600 }}>
              Задача AdRobot{lander?.task_title ? <>: <span className="dim">{lander.task_title}</span></> : ''}
            </div>
            {!variantAdded ? (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <button className="btn btn-primary" onClick={doAddVariant} disabled={!!taskBusy} style={{ fontSize: 13 }}>
                  {taskBusy === 'variant' ? 'Добавляю вариант…' : <><Icon name="plus" size={13} /> Добавить вариант (id {renamed.offer_id})</>}
                </button>
              </div>
            ) : (
              <>
                <span className="small" style={{ color: '#4ade80' }}>✓ Вариант id {renamed.offer_id} добавлен в задачу</span>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <button className="btn" onClick={() => doMoveVariants('private')} disabled={!!taskBusy} style={{ fontSize: 12 }}>
                    {taskBusy === 'private' ? 'Перемещаю…' : <><Icon name="folder" size={13} /> Move all → приватная группа</>}
                  </button>
                  <button className="btn" onClick={() => doMoveVariants('public')} disabled={!!taskBusy} style={{ fontSize: 12 }}>
                    {taskBusy === 'public' ? 'Перемещаю…' : <><Icon name="globe" size={13} /> Move all → публичная группа</>}
                  </button>
                  {variantsMoved && (
                    <span className="small" style={{ color: '#4ade80' }}>
                      ✓ в {variantsMoved === 'private' ? 'приватной' : 'публичной'} группе
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  {!reviewSent ? (
                    <button className="btn btn-primary" onClick={doSubmitReview} disabled={!!taskBusy} style={{ fontSize: 12 }}>
                      {taskBusy === 'review' ? 'Отправляю…' : <><Icon name="send" size={13} /> Отправить задачу на ревью</>}
                    </button>
                  ) : (
                    <span className="small" style={{ color: '#4ade80' }}>✓ Задача отправлена на ревью (REVIEW)</span>
                  )}
                </div>
              </>
            )}
          </div>
          <div>
            <button className="btn" style={{ fontSize: 12 }} disabled={uploading || campaignBusy}
                    onClick={() => { setRenamed(null); setCampaign(null); loadPlan(type); }}>
              ↻ Залить заново (новый оффер)
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ background: 'var(--bg-elevated, #141414)', border: '1px solid var(--border, #2a2a2a)', borderRadius: 6, padding: '0.5rem 0.7rem' }}>
      <div className="dim" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 13, marginTop: 2, wordBreak: 'break-word' }}>{value}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <span className="dim" style={{ fontSize: 11 }}>{label}</span>
      {children}
    </label>
  );
}

export function SessionDetailPage() {
  const { sid = '' } = useParams();
  const nav = useNavigate();
  const [session, setSession] = useState<SessionFull | null>(null);
  const [active, setActive] = useState<string>('');
  const [error, setError] = useState('');
  const [addIds, setAddIds] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [adding, setAdding] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [showTaskDetails, setShowTaskDetails] = useState(false);
  const [dragId, setDragId] = useState<string | null>(null);
  const dragIdRef = useRef<string | null>(null);
  const draggingRef = useRef(false);
  const orderRef = useRef<string[]>([]);
  const timer = useRef<any>(null);

  const archive = async () => {
    if (!confirm('Переместить сессию в архив? Через 1 день она будет удалена полностью.')) return;
    setArchiving(true);
    try {
      await api.archiveSession(sid);
      nav('/sessions');
    } catch (e: any) {
      setError(e.message || 'Не удалось архивировать');
      setArchiving(false);
    }
  };

  const load = async () => {
    try {
      const s = await api.session(sid);
      setSession(s);
      setActive((cur) => cur || Object.keys(s.landers)[0] || '');
      return s;
    } catch (e: any) {
      setError(e.message || 'Сессия не найдена');
      return null;
    }
  };

  const ensurePolling = () => {
    if (timer.current) return;
    timer.current = setInterval(async () => {
      if (draggingRef.current) return;  // не перетираем оптимистичный порядок при drag
      const s = await load();
      if (s) {
        const anyActive = Object.values(s.landers).some((l) => ACTIVE_STATUSES.has(l.status));
        if (!anyActive && timer.current) { clearInterval(timer.current); timer.current = null; }
      }
    }, 3000);
  };

  useEffect(() => {
    load();
    ensurePolling();
    // ВАЖНО: обнуляем timer.current, иначе при повторном монтировании
    // (React 18 StrictMode: mount→cleanup→mount) ensurePolling() увидит «таймер
    // уже есть» и не создаст новый интервал → поллинг не работает (только F5).
    return () => { if (timer.current) { clearInterval(timer.current); timer.current = null; } };
  }, [sid]);

  const addByIds = async () => {
    const ids = Array.from(new Set(addIds.split(/[\s,;]+/).map((s) => s.trim()).filter((s) => /^\d{4,5}$/.test(s))));
    if (!ids.length) return;
    setAdding(true); setError('');
    try {
      await api.addLanders(sid, ids);
      setAddIds('');
      await load();
      ensurePolling();
    } catch (e: any) {
      setError(e.message || 'Не удалось добавить ленды');
    } finally {
      setAdding(false);
    }
  };

  const removeLander = async (lid: string) => {
    if (!confirm(`Удалить ленд ${lid} из сессии? Его файлы будут стёрты.`)) return;
    setError('');
    try {
      const s = await api.deleteLander(sid, lid);
      setSession(s);
      if (active === lid) setActive(Object.keys(s.landers)[0] || '');
    } catch (e: any) {
      setError(e.message || 'Не удалось удалить ленд');
    }
  };

  // Переустановка: стереть текущее состояние и заново скачать первоначальный ленд.
  const reinstallLander = async (lid: string) => {
    if (!confirm(`Переустановить ленд ${lid}? Текущее состояние (адаптация, правки, история) будет стёрто, и первоначальный ленд скачается из Keitaro заново.`)) return;
    setError('');
    try {
      const s = await api.reinstallLander(sid, lid);
      setSession(s);
      ensurePolling(); // скачивание пошло — следим за статусом
    } catch (e: any) {
      setError(e.message || 'Не удалось переустановить ленд');
    }
  };

  // Переименование вкладки ленда (id не меняется, только отображаемое имя).
  const renameLander = async (l: LanderState) => {
    const cur = l.display_name || l.lander_id;
    const name = prompt('Имя ленда (пусто — вернуть id):', cur);
    if (name === null || name.trim() === cur) return;
    setError('');
    try {
      const s = await api.renameLander(sid, l.lander_id, name.trim());
      setSession(s);
    } catch (e: any) {
      setError(e.message || 'Не удалось переименовать ленд');
    }
  };

  // Дубль ленда: копия архивов/параметров/журнала, встаёт после оригинала.
  const duplicateLander = async (lid: string) => {
    setError('');
    try {
      const r = await api.duplicateLander(sid, lid);
      setSession(r.session);
      setActive(r.lander_id);
    } catch (e: any) {
      setError(e.message || 'Не удалось дублировать ленд');
    }
  };

  // ── drag-and-drop порядка лендов ──
  const startDrag = (lid: string) => {
    dragIdRef.current = lid;
    draggingRef.current = true;
    setDragId(lid);
  };

  const dragOverLander = (overId: string) => {
    const from = dragIdRef.current;
    if (!from || from === overId) return;
    setSession((prev) => {
      if (!prev) return prev;
      const keys = Object.keys(prev.landers);
      const fi = keys.indexOf(from), ti = keys.indexOf(overId);
      if (fi < 0 || ti < 0) return prev;
      keys.splice(fi, 1);
      keys.splice(ti, 0, from);
      const nl: Record<string, LanderState> = {};
      keys.forEach((k) => { nl[k] = prev.landers[k]; });
      return { ...prev, landers: nl };
    });
  };

  const endDrag = () => {
    const wasDragging = draggingRef.current;
    dragIdRef.current = null;
    draggingRef.current = false;
    setDragId(null);
    if (wasDragging && orderRef.current.length) {
      api.reorderLanders(sid, orderRef.current).catch(() => load());
    }
  };

  const uploadArchives = async (fileList: FileList | null) => {
    if (!fileList || !fileList.length) return;
    setAdding(true); setError('');
    try {
      for (const f of Array.from(fileList)) {
        const ls = await api.uploadLander(sid, f);
        setActive(ls.lander_id);
      }
      await load();
    } catch (e: any) {
      setError(e.message || 'Не удалось загрузить архив');
    } finally {
      setAdding(false);
    }
  };

  if (error) return <div className="page"><p style={{ color: '#f87171' }}>{error}</p><Link to="/sessions">← к сессиям</Link></div>;
  if (!session) return <div className="page"><p className="dim">Загрузка…</p></div>;

  const landers = Object.values(session.landers);
  orderRef.current = Object.keys(session.landers);  // актуальный порядок для сохранения
  const activeLander = session.landers[active];

  // Задачи-источники для модалки деталей (объединённые → вкладки; одиночная → 1).
  const taskRefs = (session.tasks && session.tasks.length)
    ? session.tasks.map((t) => ({ uid: t.uid, title: t.title, offer: t.offer }))
    : (session.task_uid ? [{ uid: session.task_uid, title: session.task_title, offer: session.offer }] : []);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', padding: 0 }}>
      {/* шапка сессии */}
      <div style={{ padding: '0.75rem 1.25rem', borderBottom: '1px solid var(--border, #2a2a2a)', display: 'flex', alignItems: 'center', gap: '1rem', flexShrink: 0 }}>
        <Link to="/sessions" className="dim" style={{ textDecoration: 'none' }}>←</Link>
        <h1 style={{ margin: 0, fontSize: 17 }}>{session.offer || session.task_title}</h1>
        <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, color: '#fff', background: statusColor(session.status) }}>{session.status}</span>
        <span className="dim small">{landers.length} ленд(ов)</span>
        {session.tasks && session.tasks.length > 1 && (
          <span
            title={session.tasks.map((t) => t.title || t.uid).join('\n')}
            style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, color: 'var(--accent, #7c6fff)', background: 'var(--accent-soft, rgba(124,111,255,0.15))' }}
          >
            из {session.tasks.length} задач
          </span>
        )}
        <div style={{ flex: 1 }} />
        {taskRefs.length > 0 && (
          <button className="btn" style={{ fontSize: 12 }} onClick={() => setShowTaskDetails(true)}
                  title="Полная карточка задачи (для объединённых — вкладки по задачам)">
            <Icon name="clipboard" size={13} /> Детали задач{taskRefs.length > 1 ? ` (${taskRefs.length})` : 'и'}
          </button>
        )}
        <button className="btn" style={{ fontSize: 12 }} disabled={archiving} onClick={archive}>
          {archiving ? '…' : 'В архив'}
        </button>
      </div>

      {showTaskDetails && taskRefs.length > 0 && (
        <TaskDetailsModal tasks={taskRefs} onClose={() => setShowTaskDetails(false)} />
      )}

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* левая колонка: ленды + сворачиваемое «добавить» */}
        <div style={{ width: 240, flexShrink: 0, borderRight: '1px solid var(--border, #2a2a2a)', display: 'flex', flexDirection: 'column', background: 'var(--bg-elevated, #141414)' }}>
          {/* список лендов (скролл) */}
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
            {landers.map((l) => {
              const multiTask = (session.tasks?.length || 0) > 1;
              const isActive = l.lander_id === active;
              return (
                <div
                  key={l.lander_id}
                  className="lander-row"
                  draggable
                  onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; startDrag(l.lander_id); }}
                  onDragEnter={() => dragOverLander(l.lander_id)}
                  onDragOver={(e) => e.preventDefault()}
                  onDragEnd={endDrag}
                  onDrop={(e) => { e.preventDefault(); endDrag(); }}
                  style={{
                    display: 'flex', alignItems: 'stretch', width: '100%',
                    background: isActive ? 'var(--accent-soft, rgba(124,111,255,0.15))' : 'transparent',
                    borderLeft: isActive ? '2px solid var(--accent, #7c6fff)' : '2px solid transparent',
                    opacity: dragId === l.lander_id ? 0.4 : 1,
                  }}
                >
                  <span
                    className="lander-grip"
                    title="Перетащите, чтобы изменить порядок"
                    style={{
                      flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      width: 16, cursor: 'grab', color: 'var(--text-muted)', fontSize: 12, lineHeight: 1,
                    }}
                  >
                    ⠿
                  </span>
                  <button
                    onClick={() => setActive(l.lander_id)}
                    onDoubleClick={() => renameLander(l)}
                    title={l.lander_id + (l.display_name ? ` · ${l.display_name}` : '') + (l.task_title ? ` · ${l.task_title}` : '') + ' · двойной клик — переименовать'}
                    style={{
                      display: 'flex', flexDirection: 'column', gap: 2, flex: 1, minWidth: 0,
                      padding: '0.55rem 0.4rem 0.55rem 0.2rem', border: 'none', cursor: 'pointer',
                      textAlign: 'left', background: 'transparent', color: 'inherit',
                    }}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', minWidth: 0 }}>
                      <span style={{ width: 8, height: 8, borderRadius: 999, flexShrink: 0, background: statusColor(l.status) }} title={l.status} />
                      <span style={{ fontSize: 12, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.display_name || l.lander_id}</span>
                    </span>
                    {multiTask && l.task_title && (
                      <span className="dim" style={{ fontSize: 10, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%', paddingLeft: 14 }}>
                        {l.task_title}
                      </span>
                    )}
                  </button>
                  <button
                    className="lander-del"
                    onClick={() => renameLander(l)}
                    title="Переименовать ленд"
                    aria-label="Переименовать ленд"
                    style={{
                      flexShrink: 0, width: 24, border: 'none', cursor: 'pointer',
                      background: 'transparent', color: 'var(--text-muted)', fontSize: 12, lineHeight: 1,
                    }}
                  >
                    ✎
                  </button>
                  <button
                    className="lander-del"
                    onClick={() => duplicateLander(l.lander_id)}
                    title="Дублировать ленд (копия архивов, параметров и правок)"
                    aria-label="Дублировать ленд"
                    style={{
                      flexShrink: 0, width: 24, border: 'none', cursor: 'pointer',
                      background: 'transparent', color: 'var(--text-muted)', fontSize: 12, lineHeight: 1,
                    }}
                  >
                    ⧉
                  </button>
                  {/^\d{4,5}$/.test(l.lander_id) && (
                    <button
                      className="lander-del"
                      onClick={() => reinstallLander(l.lander_id)}
                      title="Переустановить ленд: стереть всё и скачать первоначальный из Keitaro"
                      aria-label="Переустановить ленд"
                      style={{
                        flexShrink: 0, width: 24, border: 'none', cursor: 'pointer',
                        background: 'transparent', color: 'var(--text-muted)', fontSize: 13, lineHeight: 1,
                      }}
                    >
                      ↻
                    </button>
                  )}
                  <button
                    className="lander-del"
                    onClick={() => removeLander(l.lander_id)}
                    title="Удалить ленд из сессии"
                    aria-label="Удалить ленд"
                    style={{
                      flexShrink: 0, width: 28, border: 'none', cursor: 'pointer',
                      background: 'transparent', color: 'var(--text-muted)', fontSize: 15, lineHeight: 1,
                    }}
                  >
                    ×
                  </button>
                </div>
              );
            })}
          </div>

          {/* добавить ленд — сворачиваемая секция */}
          <div style={{ flexShrink: 0, borderTop: '1px solid var(--border, #2a2a2a)' }}>
            <button
              onClick={() => setShowAdd((v) => !v)}
              style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', padding: '0.6rem 0.8rem', border: 'none', cursor: 'pointer', background: 'transparent', color: 'var(--text)', fontSize: 12, fontWeight: 600 }}
            >
              <span style={{ color: 'var(--text-muted)' }}>{showAdd ? '▾' : '▸'}</span>
              + Добавить ленд
            </button>
            {showAdd && (
              <div style={{ padding: '0 0.7rem 0.7rem', maxHeight: '60vh', overflowY: 'auto' }}>
                <div className="dim" style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 4 }}>Из Keitaro по ID</div>
                <input
                  className="form-input"
                  value={addIds}
                  onChange={(e) => setAddIds(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') addByIds(); }}
                  placeholder="ID (9224, 14278)"
                  style={{ fontSize: 12, width: '100%' }}
                />
                <OfferNamesHint idsText={addIds} compact />
                <button className="btn" onClick={addByIds} disabled={adding} style={{ fontSize: 11, width: '100%', marginTop: 4 }}>
                  {adding ? '…' : '↓ скачать из Keitaro'}
                </button>
                <label className="btn" style={{ fontSize: 11, width: '100%', marginTop: 8, textAlign: 'center', cursor: 'pointer', display: 'block' }}>
                  ↑ загрузить .zip
                  <input type="file" accept=".zip" multiple style={{ display: 'none' }}
                         onChange={(e) => { uploadArchives(e.target.files); e.currentTarget.value = ''; }} />
                </label>
                <SiteScrapeAdder sid={sid} taskUid={session.task_uid || undefined} onAdded={load} />
                {session.task_uid && (
                  <TaskArchivesAdder sid={sid} taskUids={(session.tasks && session.tasks.length ? session.tasks.map(t => t.uid) : [session.task_uid!])} onAdded={load} />
                )}
              </div>
            )}
          </div>
        </div>

        {/* контент активного ленда */}
        <div style={{ flex: 1, overflow: 'hidden', padding: '1rem 1.25rem' }}>
          {activeLander
            ? <LanderPanel key={activeLander.lander_id} sid={sid} lander={activeLander} isVsl={!!session.is_vsl}
                           sessionTasks={(session.tasks || []).map((t) => ({ uid: t.uid, title: t.title }))} />
            : <p className="dim">Нет лендов.</p>}
        </div>
      </div>
    </div>
  );
}
