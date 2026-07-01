import { useEffect, useMemo, useState } from 'react';
import { api, Geo, Vertical, ProcessResult, ScanResponse } from '../lib/api';
import { Dropzone } from '../components/Dropzone';
import { LogViewer } from '../components/LogViewer';
import { ResultCard } from '../components/ResultCard';
import { geoOptions, SearchableSelect, verticalOptions } from '../components/SearchableSelect';
import { AssetsInlinePanel } from '../components/AssetsInlinePanel';

interface AdaptValues {
  geo_id: string;
  vertical_id: string;
  product_old: string;
  product_new: string;
  price_new: string;
  price_old: string;
  // Что найти в HTML (исходные значения из сканера, можно поправить)
  src_price_new_num: string;
  src_price_new_cur: string;
  src_price_old_num: string;
  src_price_old_cur: string;
  // На что заменить (целевые значения)
  price_new_num: string;
  price_new_cur: string;
  price_old_num: string;
  price_old_cur: string;
  source_price_str: string;  // исходная цена в оффере (напр. "L990")
  exclude_word: string;
  image_map: Record<string, string>;
  custom_replacements: string;
}

/** Превью файла из загруженного ZIP (бэкенд /api/scan-preview/...) */
function OfferZipThumb({
  uploadId,
  pathInsideZip,
}: {
  uploadId: string;
  pathInsideZip: string;
}) {
  const [failed, setFailed] = useState(false);
  const src = `/api/scan-preview/${encodeURIComponent(uploadId)}?path=${encodeURIComponent(pathInsideZip)}`;
  if (failed) {
    return (
      <div className="image-map-thumb-fallback" title="Превью недоступно">
        <span className="dim">?</span>
      </div>
    );
  }
  return (
    <img
      className="image-map-thumb"
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  );
}

/** Миниатюра файла из storage/assets/ (выбранная замена) */
function AssetReplaceThumb({ filename }: { filename: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setFailed(false);
  }, [filename]);

  if (!filename) {
    return (
      <div
        className="image-map-thumb-wrap image-map-thumb-wrap-placeholder"
        title="Выбери файл из списка"
      />
    );
  }

  const src = `/api/assets-file/${encodeURIComponent(filename)}`;
  if (failed) {
    return (
      <div className="image-map-thumb-wrap" title={filename}>
        <div className="image-map-thumb-fallback">
          <span className="dim small">?</span>
        </div>
      </div>
    );
  }

  return (
    <div className="image-map-thumb-wrap" title={filename}>
      <img
        className="image-map-thumb"
        src={src}
        alt=""
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

function defaultValuesFromScan(res: ScanResponse): AdaptValues {
  const d = res.detection;
  const product = d.product || d.product_candidates[0]?.word || '';
  // Разбиваем найденную цену на число и валюту (исходные значения — что найти)
  const srcNewNum = d.price_new_str ? d.price_new_str.replace(/^\D*(\d+).*$/, '$1') : '';
  const srcNewCur = d.price_new_str ? d.price_new_str.replace(/^\d+\s*/, '').trim() : (d.cur_sym || '');
  const srcOldNum = d.price_old_str ? d.price_old_str.replace(/^\D*(\d+).*$/, '$1') : '';
  const srcOldCur = d.price_old_str ? d.price_old_str.replace(/^\d+\s*/, '').trim() : (d.cur_sym || '');
  return {
    geo_id: '',
    vertical_id: '',
    product_old: product,
    product_new: product,
    price_new: d.price_new_str || '',
    price_old: d.price_old_str || '',
    src_price_new_num: srcNewNum,
    src_price_new_cur: srcNewCur,
    src_price_old_num: srcOldNum,
    src_price_old_cur: srcOldCur,
    price_new_num: '',   // целевое число — пользователь вводит
    price_new_cur: '',   // целевая валюта — подставится при выборе ГЕО
    price_old_num: '',
    price_old_cur: '',
    source_price_str: d.price_new_str || '',  // что нашёл сканер — покажем пользователю
    exclude_word: '',
    image_map: {},
    custom_replacements: '',
  };
}

export function ScanAdaptPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [activeFileIndex, setActiveFileIndex] = useState(0);
  const [scanResult, setScanResult] = useState<ScanResponse | null>(null);
  const [values, setValues] = useState<AdaptValues | null>(null);
  const [geos, setGeos] = useState<Geo[]>([]);
  const [verticals, setVerticals] = useState<Vertical[]>([]);
  const [assets, setAssets] = useState<string[]>([]);

  const [busyScan, setBusyScan] = useState(false);
  const [busyAdapt, setBusyAdapt] = useState(false);
  const [completedResults, setCompletedResults] = useState<Array<{ source: string; result: ProcessResult }>>([]);
  const [error, setError] = useState<string>('');
  const [showAllImages, setShowAllImages] = useState(false);

  const refreshAssets = () => api.assets().then(setAssets).catch(console.error);

  useEffect(() => {
    api.geos().then(setGeos).catch(console.error);
    api.verticals().then(setVerticals).catch(console.error);
    api.assets().then(setAssets).catch(console.error);
  }, []);

  const geoOpts = useMemo(() => geoOptions(geos), [geos]);
  const vertOpts = useMemo(() => verticalOptions(verticals), [verticals]);

  const handleFiles = (next: File[]) => {
    setFiles(next);
    setScanResult(null);
    setValues(null);
    setCompletedResults([]);
    setActiveFileIndex(0);
    setError('');
  };

  const allDone =
    files.length > 0 &&
    completedResults.length === files.length &&
    !scanResult &&
    !busyScan &&
    !busyAdapt;

  const handleScan = async () => {
    if (!files.length) return;
    setBusyScan(true);
    setError('');
    setScanResult(null);
    setValues(null);
    setCompletedResults([]);
    setActiveFileIndex(0);
    try {
      const res = await api.scan(files[0]);
      setScanResult(res);
      setValues(defaultValuesFromScan(res));
      setActiveFileIndex(0);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Ошибка скана';
      setError(msg);
    } finally {
      setBusyScan(false);
    }
  };

  const handleAdapt = async () => {
    if (!files.length || !values || !scanResult) return;
    setBusyAdapt(true);
    setError('');
    try {
      const res = await api.adapt({
        upload_id: scanResult.upload_id,
        geo_id: values.geo_id,
        product_old: values.product_old,
        product_new: values.product_new,
        price_new: `${values.price_new_num} ${values.price_new_cur}`.trim(),
        price_old: `${values.price_old_num} ${values.price_old_cur}`.trim(),
        price_new_num: values.price_new_num,
        price_new_cur: values.price_new_cur,
        price_old_num: values.price_old_num,
        price_old_cur: values.price_old_cur,
        src_price_new_num: values.src_price_new_num,
        src_price_new_cur: values.src_price_new_cur,
        src_price_old_num: values.src_price_old_num,
        src_price_old_cur: values.src_price_old_cur,
        source_price_str: values.source_price_str,
        exclude_word: values.exclude_word,
        image_map: JSON.stringify(values.image_map),
        custom_replacements: values.custom_replacements,
        use_parser_v2: 'true',
      });

      setCompletedResults((prev) => [...prev, { source: files[activeFileIndex].name, result: res }]);

      if (activeFileIndex < files.length - 1) {
        const nextIdx = activeFileIndex + 1;
        setBusyScan(true);
        try {
          const scanRes = await api.scan(files[nextIdx]);
          setScanResult(scanRes);
          setValues(defaultValuesFromScan(scanRes));
          setActiveFileIndex(nextIdx);
          setShowAllImages(false);
        } catch (e: unknown) {
          const msg = e instanceof Error ? e.message : 'Ошибка скана следующего файла';
          setError(msg);
          setScanResult(null);
          setValues(null);
        } finally {
          setBusyScan(false);
        }
      } else {
        setScanResult(null);
        setValues(null);
        setShowAllImages(false);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Ошибка адаптации';
      setError(msg);
    } finally {
      setBusyAdapt(false);
    }
  };

  const reset = () => {
    setFiles([]);
    setActiveFileIndex(0);
    setScanResult(null);
    setValues(null);
    setCompletedResults([]);
    setError('');
    setShowAllImages(false);
  };

  const set = (patch: Partial<AdaptValues>) => values && setValues({ ...values, ...patch });

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  const adaptLabel = (() => {
    if (busyAdapt) return 'Адаптирую...';
    if (files.length <= 1) return 'Адаптировать';
    if (activeFileIndex < files.length - 1) {
      return `Адаптировать и следующий (${activeFileIndex + 1}/${files.length})`;
    }
    return `Адаптировать и завершить (${files.length}/${files.length})`;
  })();

  const dropzoneDisabled = busyScan || busyAdapt || scanResult !== null;

  return (
    <div className="page">
      <div className="page-header">
        <h1>Scan + Adapt</h1>
        <p className="muted">
          Адаптация офферов из Кейтаро под новое ГЕО: смена продукта, цен, скрытых полей формы, фото.
          Несколько ZIP обрабатываются по очереди: для каждого архива отдельный скан, правки и затем общий список
          результатов для скачивания.
        </p>
      </div>

      {/* ── Шаг 1: загрузка и скан ───────────────────── */}
      <div className="card">
        <div className="card-label">Шаг 1 — загрузить и просканировать первый архив</div>
        <Dropzone files={files} onFiles={handleFiles} multiple disabled={dropzoneDisabled} />

        <div className="form-actions">
          <button
            className="btn btn-primary"
            disabled={
              !files.length ||
              busyScan ||
              !!scanResult ||
              allDone
            }
            onClick={handleScan}
          >
            {busyScan
              ? 'Сканирую...'
              : allDone
                ? 'Очередь завершена'
                : scanResult
                  ? 'Просканировано'
                  : files.length > 1
                    ? `Сканировать первый из ${files.length}`
                    : 'Сканировать'}
          </button>
          {(files.length > 0 || scanResult || completedResults.length > 0) && !busyScan && !busyAdapt && (
            <button className="btn" onClick={reset}>Сброс</button>
          )}
        </div>
        {files.length > 1 && !allDone && (
          <p className="form-hint dim small" style={{ marginTop: '0.75rem' }}>
            В очереди {files.length} архивов. После адаптации каждого откроется скан следующего — параметры нужно
            проверить отдельно для каждого файла.
          </p>
        )}
        {allDone && (
          <p className="form-hint dim small" style={{ marginTop: '0.75rem' }}>
            Все архивы обработаны. Скачай результаты ниже или сбрось форму / выбери новые ZIP.
          </p>
        )}
      </div>

      {/* ── Результаты скана + форма для шага 2 ──────── */}
      {scanResult && values && (
        <>
          <div className="card">
            <div className="card-label">Что нашёл сканер</div>
            {files.length > 1 && (
              <p className="muted small" style={{ marginBottom: '0.75rem' }}>
                Файл <span className="mono">{files[activeFileIndex]?.name}</span>
                {' '}({activeFileIndex + 1} из {files.length})
              </p>
            )}
            <div className="scan-summary">
              <div className="scan-summary-row">
                <span className="dim small">Продукт:</span>
                <span className="mono">{scanResult.detection.product || '—'}</span>
                {scanResult.detection.product_candidates.length > 0 && (
                  <span className="dim small">
                    (кандидаты: {scanResult.detection.product_candidates.slice(0, 3).map(c => `${c.word}×${c.count}`).join(', ')})
                  </span>
                )}
              </div>
              <div className="scan-summary-row">
                <span className="dim small">Цены:</span>
                <span className="mono">
                  {scanResult.detection.price_new_str || '—'} / {scanResult.detection.price_old_str || '—'}
                </span>
                <span className="dim small">валюта: {scanResult.detection.cur_sym || '—'}</span>
              </div>
              <div className="scan-summary-row">
                <span className="dim small">Найдено в коде:</span>
                <span className="mono small">
                  страны: {scanResult.detection.detected_country.data_country.join(', ') || '—'} ·
                  языки: {scanResult.detection.detected_country.data_language.join(', ') || '—'} ·
                  exclude_word: {scanResult.detection.detected_country.exclude_word || '—'}
                </span>
              </div>
              <div className="scan-summary-row">
                <span className="dim small">Фото:</span>
                <span className="mono small">
                  {scanResult.detection.prod_images.length > 0
                    ? scanResult.detection.prod_images.join(', ')
                    : '—'}
                </span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-label">Шаг 2 — параметры адаптации</div>

            <div className="form-grid">
              <div className="form-row">
                <label className="form-label">
                  Целевое ГЕО
                  <SearchableSelect
                    options={geoOpts}
                    value={values.geo_id}
                    onChange={(id) => {
                      const g = geos.find((x) => x.id === id);
                      const cur = g?.currency || '';
                      set({
                        geo_id: id,
                        price_new_cur: cur || values.price_new_cur,
                        price_old_cur: cur || values.price_old_cur,
                      });
                    }}
                    emptyOptionLabel="— выбери ГЕО —"
                    placeholder="Код (IN), страна или валюта…"
                  />
                </label>
              </div>

              <div className="form-row">
                <label className="form-label">
                  Продукт сейчас (что меняем)
                  <input
                    className="form-input mono"
                    type="text"
                    value={values.product_old}
                    onChange={(e) => set({ product_old: e.target.value })}
                  />
                </label>

                <label className="form-label">
                  Новое название
                  <input
                    className="form-input mono"
                    type="text"
                    value={values.product_new}
                    onChange={(e) => set({ product_new: e.target.value })}
                  />
                </label>
              </div>

              {/* Исходная цена — что нашёл сканер / можно поправить вручную */}
              {(!scanResult.detection.price_new_str) && (
                <div className="form-row">
                  <label className="form-label">
                    Исходная цена в оффере
                    <input
                      className="form-input"
                      type="text"
                      value={values.source_price_str}
                      onChange={(e) => set({ source_price_str: e.target.value })}
                      placeholder="напр. L990 или 890 MXN"
                    />
                    <span className="form-hint dim small">
                      Сканер не нашёл цену автоматически. Введи как цена написана в исходном оффере — адаптер найдёт её в любом написании.
                    </span>
                  </label>
                </div>
              )}

              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 1fr 24px 1fr 1fr', gap: '0.4rem', alignItems: 'center' }}>
                  <div />
                  <span className="dim small" style={{ textAlign: 'center' }}>Число в оффере</span>
                  <span className="dim small" style={{ textAlign: 'center' }}>Заменить на</span>
                  <div />
                  <span className="dim small" style={{ textAlign: 'center' }}>Валюта в оффере</span>
                  <span className="dim small" style={{ textAlign: 'center' }}>Заменить на</span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 1fr 24px 1fr 1fr', gap: '0.4rem', alignItems: 'center' }}>
                  <span className="form-label" style={{ marginBottom: 0, fontSize: 13 }}>Новая цена</span>
                  <input className="form-input mono" type="text" value={values.src_price_new_num}
                    onChange={(e) => set({ src_price_new_num: e.target.value })} placeholder="2490" title="Число как в оффере" />
                  <input className="form-input mono" type="text" value={values.price_new_num}
                    onChange={(e) => set({ price_new_num: e.target.value })} placeholder="78" title="Новое число" />
                  <span className="dim" style={{ textAlign: 'center' }}>|</span>
                  <input className="form-input mono" type="text" value={values.src_price_new_cur}
                    onChange={(e) => set({ src_price_new_cur: e.target.value })} placeholder="INR" title="Валюта как в оффере" />
                  <input className="form-input mono" type="text" value={values.price_new_cur}
                    onChange={(e) => set({ price_new_cur: e.target.value })}
                    placeholder={geos.find((g) => g.id === values.geo_id)?.currency || 'EUR'} title="Новая валюта" />
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 1fr 24px 1fr 1fr', gap: '0.4rem', alignItems: 'center' }}>
                  <span className="form-label" style={{ marginBottom: 0, fontSize: 13 }}>Старая цена</span>
                  <input className="form-input mono" type="text" value={values.src_price_old_num}
                    onChange={(e) => set({ src_price_old_num: e.target.value })} placeholder="4980" title="Число как в оффере" />
                  <input className="form-input mono" type="text" value={values.price_old_num}
                    onChange={(e) => set({ price_old_num: e.target.value })} placeholder="39" title="Новое число" />
                  <span className="dim" style={{ textAlign: 'center' }}>|</span>
                  <input className="form-input mono" type="text" value={values.src_price_old_cur}
                    onChange={(e) => set({ src_price_old_cur: e.target.value })} placeholder="INR" title="Валюта как в оффере" />
                  <input className="form-input mono" type="text" value={values.price_old_cur}
                    onChange={(e) => set({ price_old_cur: e.target.value })}
                    placeholder={geos.find((g) => g.id === values.geo_id)?.currency || 'EUR'} title="Новая валюта" />
                </div>
                <p className="form-hint dim small">
                  Левые поля — как цена написана в оффере. Правые поля — на что заменить.
                </p>
              </div>

              <div className="form-row form-row-align-fields">
                <label className="form-label">
                  <span>Скрытые поля формы — вертикаль</span>
                  <SearchableSelect
                    options={vertOpts}
                    value={values.vertical_id}
                    onChange={(id) => {
                      const v = verticals.find((x) => x.id === id);
                      set({
                        vertical_id: id,
                        exclude_word: v?.exclude_word ?? '',
                      });
                    }}
                    emptyOptionLabel="— не подставлять из списка —"
                    placeholder="Название вертикали или exclude_word…"
                  />
                </label>

                <label className="form-label">
                  <span>
                    Новое значение{' '}
                    <span className="mono dim" style={{ fontSize: '11px', fontWeight: 400 }} title="HTML: name=&quot;exclude_word&quot;">
                      (exclude_word)
                    </span>
                  </span>
                  <input
                    className="form-input mono"
                    type="text"
                    value={values.exclude_word}
                    onChange={(e) => set({ exclude_word: e.target.value, vertical_id: '' })}
                    placeholder={
                      scanResult.detection.detected_country.exclude_word != null
                        ? `сейчас в HTML: ${scanResult.detection.detected_country.exclude_word}`
                        : 'напр. pr '
                    }
                    title="Скрытое поле в форме: name=&quot;exclude_word&quot;"
                  />
                </label>
              </div>
              <p className="form-hint dim small" style={{ marginTop: '-4px' }}>
                Подставляется до замены фото. Пустое поле — не трогаем exclude_word. Чтобы задать своё значение,
                введи его вручную или выбери вертикаль. В HTML должно быть найдено текущее значение — иначе правило не
                добавится.
              </p>

              <label className="form-label" style={{ marginTop: '2px' }}>
                Доп. замены в коде (необязательно)
                <textarea
                  className="form-input mono"
                  value={values.custom_replacements}
                  onChange={(e) => set({ custom_replacements: e.target.value })}
                  placeholder={`Panama => Paraguay\nBogota => Montevideo\nCarlos => Diego`}
                  rows={5}
                />
                <span className="form-hint dim small">
                  По одной замене в строке: <span className="mono">что найти =&gt; на что заменить</span>.
                  Применяется после стандартной адаптации и подходит для страны, городов и имён.
                </span>
              </label>

              <AssetsInlinePanel onAssetsChanged={refreshAssets} />

              {scanResult.detection.all_images.length > 0 && (
                <div className="form-row">
                  <div className="form-label" style={{ flex: 1 }}>
                    <div className="image-map-header">
                      <span>Замена фото</span>
                      <button
                        type="button"
                        className="btn-link small"
                        onClick={() => setShowAllImages((v) => !v)}
                      >
                        {showAllImages ? 'Скрыть мелкие' : `Показать все (${scanResult.detection.all_images.length})`}
                      </button>
                    </div>

                    {assets.length === 0 && (
                      <div className="warn-banner small">
                        В storage/assets/ пока нет файлов — загрузи фото в блоке «Фото для замены» выше, затем выбери их здесь.
                      </div>
                    )}

                    <div className="image-map-grid">
                      {(showAllImages
                        ? scanResult.detection.all_images
                        : scanResult.detection.all_images.filter((i) => i.is_product)
                      ).map((img) => (
                        <div key={img.path} className="image-map-row">
                          <div className="image-map-thumb-wrap">
                            <OfferZipThumb uploadId={scanResult.upload_id} pathInsideZip={img.path} />
                          </div>
                          <div className="image-map-old">
                            <span className="mono small" title={img.path}>{img.name}</span>
                            <span className="dim small">{formatSize(img.size)}</span>
                          </div>
                          <span className="dim">→</span>
                          <div className="image-map-replace">
                            <select
                              className="form-input mono small image-map-select"
                              value={values.image_map[img.name] || ''}
                              onChange={(e) => set({
                                image_map: {
                                  ...values.image_map,
                                  [img.name]: e.target.value
                                }
                              })}
                            >
                              <option value="">— не менять —</option>
                              {assets.map((a) => (
                                <option key={a} value={a}>{a}</option>
                              ))}
                            </select>
                            <AssetReplaceThumb filename={values.image_map[img.name] || ''} />
                          </div>
                        </div>
                      ))}

                      {!showAllImages && scanResult.detection.all_images.filter(i => i.is_product).length === 0 && (
                        <div className="dim small">
                          Сканер не нашёл явных фото продукта. Жми &quot;Показать все&quot; чтобы выбрать вручную.
                        </div>
                      )}
                    </div>

                    <span className="form-hint dim small">
                      Старое имя файла в архиве → новое имя из загруженных выше. В результате в ZIP остаётся исходное имя файла, подставляется содержимое из storage/assets/.
                    </span>
                  </div>
                </div>
              )}
            </div>

            <div className="form-actions">
              <button
                className="btn btn-primary"
                disabled={!values.geo_id || busyAdapt || busyScan}
                onClick={handleAdapt}
              >
                {adaptLabel}
              </button>

            </div>
          </div>
        </>
      )}

      {error && (
        <div className="card error-card">
          <strong>Ошибка:</strong> {error}
        </div>
      )}

      {completedResults.length > 0 && (
        <div className="card">
          <div className="card-label">
            Результаты ({completedResults.length}{files.length > 1 ? ` из ${files.length}` : ''})
          </div>
          <p className="dim small" style={{ marginBottom: '1rem' }}>
            Все готовые архивы — ниже по одному. При очереди из нескольких ZIP каждый проходил отдельную проверку параметров.
          </p>
        </div>
      )}

      {completedResults.length > 0 && (
        <div className="card" style={{ borderLeft: '3px solid var(--c-warn, #f59e0b)' }}>
          <p className="small" style={{ color: 'var(--c-warn, #f59e0b)', margin: 0 }}>
            ⚠️ Перед заливкой в Кейтаро откройте оффер в браузере и проверьте:
            картинку продукта, цены (новую и старую), название продукта, страну в форме.
          </p>
        </div>
      )}

      {completedResults.map(({ source, result }) => (
        <div key={`${source}-${result.result_name ?? 'no-result'}`}>
          <div className="card-label">Файл: {source}</div>
          <ResultCard
            resultName={result.result_name}
            resultUrl={result.result_url}
            success={result.success}
          />
          <LogViewer lines={result.log} title={`Лог обработки: ${source}`} />
        </div>
      ))}
    </div>
  );
}