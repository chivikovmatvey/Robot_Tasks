import { useEffect, useMemo, useState } from 'react';
import { api, Geo, Vertical } from '../lib/api';
import { geoOptions, SearchableSelect, verticalOptions } from './SearchableSelect';

export interface InjectFormValues {
  country: string;
  language: string;
  /** Выбранная вертикаль из справочника (только UI; не отправляется в API) */
  vertical_id: string;
  exclude_word: string;
  price_new: string;
  price_old: string;
  prod_img: string;
  custom_replacements: string;
}

interface InjectFormProps {
  values: InjectFormValues;
  onChange: (values: InjectFormValues) => void;
}

export function InjectForm({ values, onChange }: InjectFormProps) {
  const [geos, setGeos] = useState<Geo[]>([]);
  const [verticals, setVerticals] = useState<Vertical[]>([]);
  const [assets, setAssets] = useState<string[]>([]);

  useEffect(() => {
    api.geos().then(setGeos).catch(console.error);
    api.verticals().then(setVerticals).catch(console.error);
    api.assets().then(setAssets).catch(console.error);
  }, []);

  const geoOpts = useMemo(() => geoOptions(geos), [geos]);
  const vertOpts = useMemo(() => verticalOptions(verticals), [verticals]);

  const set = (patch: Partial<InjectFormValues>) => onChange({ ...values, ...patch });

  const handleGeoChange = (geoId: string) => {
    const geo = geos.find((g) => g.id === geoId);
    set({
      country: geoId,
      language: geo?.lang ?? '',
    });
  };

  return (
    <div className="form-grid">
      <div className="form-row">
        <label className="form-label">
          ГЕО (страна)
          <SearchableSelect
            options={geoOpts}
            value={values.country}
            onChange={handleGeoChange}
            emptyOptionLabel="— выбери ГЕО —"
            placeholder="Код (IN), страна или валюта…"
          />
        </label>

        <label className="form-label">
          Язык
          <input
            className="form-input mono"
            type="text"
            value={values.language}
            onChange={(e) => set({ language: e.target.value.toUpperCase() })}
            placeholder="напр. ES"
          />
        </label>
      </div>

      <div className="form-row">
        <label className="form-label">
          Вертикаль (для exclude_word)
          <SearchableSelect
            options={vertOpts}
            value={values.vertical_id}
            onChange={(id) => {
              const v = verticals.find((vv) => vv.id === id);
              set({
                vertical_id: id,
                exclude_word: v?.exclude_word ?? '',
              });
            }}
            emptyOptionLabel="— выбери или введи exclude_word вручную —"
            placeholder="Название или часть exclude_word…"
          />
        </label>

        <label className="form-label">
          exclude_word
          <input
            className="form-input mono"
            type="text"
            value={values.exclude_word}
            onChange={(e) => set({ exclude_word: e.target.value, vertical_id: '' })}
            placeholder="напр. pt "
          />
        </label>
      </div>

      <div className="form-row">
        <label className="form-label">
          Новая цена
          <input
            className="form-input"
            type="text"
            value={values.price_new}
            onChange={(e) => set({ price_new: e.target.value })}
            placeholder="напр. 599 BOB"
          />
        </label>

        <label className="form-label">
          Старая цена
          <input
            className="form-input"
            type="text"
            value={values.price_old}
            onChange={(e) => set({ price_old: e.target.value })}
            placeholder="напр. 1198 BOB"
          />
        </label>
      </div>

      <label className="form-label">
        Имя файла фото продукта
        <div className="form-row-inline">
          <input
            className="form-input mono"
            type="text"
            value={values.prod_img}
            onChange={(e) => set({ prod_img: e.target.value })}
            placeholder="product.webp"
            list="assets-list"
          />
          <datalist id="assets-list">
            {assets.map((a) => <option key={a} value={a} />)}
          </datalist>
        </div>
        <span className="form-hint dim small">
          {assets.length > 0
            ? `В storage/assets/ сейчас ${assets.length} ${assets.length === 1 ? 'файл' : 'файлов'} (автокомплит). Добавить фото: страница Scan + Adapt, блок «Фото для замены».`
            : 'Можно ввести любое имя — фото попадёт в HTML. Загрузить файлы в storage: Scan + Adapt → блок «Фото для замены».'}
        </span>
      </label>

      <label className="form-label">
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
          Удобно для ручной замены названий страны, городов и имён.
        </span>
      </label>
    </div>
  );
}
