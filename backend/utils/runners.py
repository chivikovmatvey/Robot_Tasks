"""
Веб-обёртки для CLI-скриптов из scripts/.
Каждая функция:
  - принимает параметры словарём (без input())
  - возвращает (out_path, lines)
  - не трогает внутреннюю логику скриптов

Для скриптов которые имеют диалоговые функции мы вызываем их низкоуровневые
функции напрямую (process_zip, build_config, и т.п.).
"""
import os
import re
import json
import shutil
import zipfile
from pathlib import Path
from contextlib import contextmanager

from utils.capture import run_with_capture, CaptureResult


# Корень backend-папки — отсюда отсчитываем storage/, scripts/ и т.д.
BACKEND_ROOT = Path(__file__).parent.parent.resolve()
STORAGE = BACKEND_ROOT / 'storage'


@contextmanager
def workdir(path: Path):
    """
    Скрипты из scripts/ пишут в Path('output') относительно cwd.
    Подменяем cwd на storage/ — тогда output попадает в storage/output.
    Потом мы переносим/переименовываем как нужно.
    """
    old = os.getcwd()
    path.mkdir(parents=True, exist_ok=True)
    os.chdir(path)
    try:
        yield path
    finally:
        os.chdir(old)


def move_to_outputs(produced_path: str | Path) -> Path:
    """
    Скрипты пишут в './output/...' относительно cwd (которая = storage/).
    Нам нужно корректно его найти и перенести в storage/outputs/.
    """
    p = Path(produced_path)

    # Если путь относительный — пробуем разные варианты разрешения
    candidates = [p]
    if not p.is_absolute():
        candidates.append(STORAGE / p)
        candidates.append(STORAGE / 'output' / p.name)

    real_path = None
    for c in candidates:
        if c.exists():
            real_path = c.resolve()
            break

    if real_path is None:
        # Последняя попытка — поиск по имени в storage/output
        fallback = STORAGE / 'output' / p.name
        if fallback.exists():
            real_path = fallback
        else:
            raise FileNotFoundError(f"Cannot find produced file: {produced_path}")

    target_dir = STORAGE / 'outputs'
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / real_path.name

    if real_path.resolve() == target.resolve():
        return target

    if target.exists():
        target.unlink()
    shutil.move(str(real_path), str(target))
    return target


def parse_custom_replacements(raw: str) -> list[dict]:
    """
    Парсит текст вида:
      old => new
    в список словарей {'find': old, 'replace': new}.
    """
    pairs = []
    for line in (raw or '').splitlines():
        s = line.strip()
        if not s:
            continue
        if '=>' not in s:
            continue
        old, new = s.split('=>', 1)
        old = old.strip()
        new = new.strip()
        if old and old != new:
            pairs.append({'find': old, 'replace': new})
    return pairs


# ══════════════════════════════════════════════════════════════
# INJECT
# ══════════════════════════════════════════════════════════════
def run_inject(zip_path: str, params: dict) -> tuple[str | None, CaptureResult]:
    """
    Параметры:
      country: 'BG'
      language: 'BG'
      exclude_word: 'pt '
      price_new: '49 лв.'
      price_old: '98 лв.'
      prod_img: 'tov.webp'
    """
    from scripts import inject

    inject_params = {
        'country':      params.get('country', ''),
        'language':     params.get('language', ''),
        'exclude_word': params.get('exclude_word', ''),
        'price_new':    params.get('price_new', ''),
        'price_old':    params.get('price_old', ''),
        'prod_img':     params.get('prod_img', 'product.webp'),
        'custom_replacements': parse_custom_replacements(params.get('custom_replacements', '')),
    }

    def _run():
        with workdir(STORAGE):
            return inject.process_zip(str(Path(zip_path).resolve()), inject_params)

    out_path, capture = run_with_capture(_run)
    if out_path:
        out_path = str(move_to_outputs(out_path))
    return out_path, capture


# ══════════════════════════════════════════════════════════════
# CLEAN
# ══════════════════════════════════════════════════════════════
def run_clean(zip_path: str) -> tuple[str | None, CaptureResult]:
    """Clean не требует параметров — берёт правила из clean_rules.json."""
    from scripts import clean

    rules = clean.load_rules()

    def _process():
        with workdir(STORAGE):
            out_path, total = clean.process_zip(str(Path(zip_path).resolve()), rules)
            from scripts.clean import ok
            ok(f"Files changed:   {total['files']}")
            ok(f"Files removed:   {len(total['removed'])}")
            if total['blocks']:           ok(f"Blocks removed:  {', '.join(total['blocks'])}")
            if total['scripts']:          ok(f"Scripts removed: {', '.join(total['scripts'])}")
            if total['lines']:            ok(f"Lines removed:   {', '.join(total['lines'])}")
            if total.get('stripped_attrs'):
                ok(f"Attrs stripped:  {', '.join(total['stripped_attrs'])}")
            if total.get('session_injected'):
                ok(f"Session injected: {', '.join(total['session_injected'])}")
            return out_path

    out_path, capture = run_with_capture(_process)
    if out_path:
        out_path = str(move_to_outputs(out_path))
    return out_path, capture


# ══════════════════════════════════════════════════════════════
# CLEAN + INJECT (объединённый пайплайн)
# ══════════════════════════════════════════════════════════════
def run_clean_inject(zip_path: str, inject_params: dict) -> tuple[str | None, CaptureResult]:
    """Сначала clean, потом inject на результате clean."""
    from scripts import clean, inject

    rules = clean.load_rules()

    def _process():
        with workdir(STORAGE):
            from scripts.clean import section, ok
            section("STAGE 1 — CLEAN")
            cleaned_path, total = clean.process_zip(str(Path(zip_path).resolve()), rules)
            ok(f"Files changed:   {total['files']}")
            ok(f"Files removed:   {len(total['removed'])}")
            if total.get('stripped_attrs'):
                ok(f"Attrs stripped:  {', '.join(total['stripped_attrs'])}")

            from scripts.inject import section as inject_section
            inject_section("STAGE 2 — INJECT")
            out_path = inject.process_zip(str(Path(cleaned_path).resolve()), {
                'country':      inject_params.get('country', ''),
                'language':     inject_params.get('language', ''),
                'exclude_word': inject_params.get('exclude_word', ''),
                'price_new':    inject_params.get('price_new', ''),
                'price_old':    inject_params.get('price_old', ''),
                'prod_img':     inject_params.get('prod_img', 'product.webp'),
                'custom_replacements': parse_custom_replacements(inject_params.get('custom_replacements', '')),
            })
            return out_path

    out_path, capture = run_with_capture(_process)
    if out_path:
        out_path = str(move_to_outputs(out_path))
    return out_path, capture


# ══════════════════════════════════════════════════════════════
# ANCHORS
# ══════════════════════════════════════════════════════════════
def run_anchors(zip_path: str) -> tuple[str | None, CaptureResult]:
    """fix_anchors — не интерактивный, просто прогоняет."""
    from scripts import fix_anchors

    def _run():
        with workdir(STORAGE):
            out_path, total = fix_anchors.process_zip(str(Path(zip_path).resolve()))
            from scripts.fix_anchors import ok
            ok(f"Files changed: {total['files']}")
            ok(f"Links fixed:   {total['links']}")
            return out_path

    out_path, capture = run_with_capture(_run)
    if out_path:
        out_path = str(move_to_outputs(out_path))
    return out_path, capture


# ══════════════════════════════════════════════════════════════
# SCAN — двухшаговый flow
# ══════════════════════════════════════════════════════════════
def _enrich_images(zip_path: str, prod_images_from_scanner: list[str]) -> list[dict]:
    """
    Возвращает список картинок с информацией:
      - path: путь внутри zip
      - name: только имя файла
      - size: размер в байтах
      - is_product: True если scanner определил как фото продукта,
                    или эвристически по размеру/имени
    Сортирует: продуктовые фото первыми.
    """
    prod_set = set(prod_images_from_scanner or [])
    images = []
    image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}

    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            ext = Path(info.filename).suffix.lower()
            if ext not in image_exts:
                continue
            name = Path(info.filename).name

            # Эвристики что это фото продукта:
            # - явно из prod_images сканера
            # - имя содержит характерные слова: product, tovar, tov, bottle, pack, main
            # - ИЛИ размер > 30KB (мелкие иконки/декор обычно меньше)
            name_lower = name.lower()
            is_product = (
                name in prod_set or
                info.filename in prod_set or
                any(kw in name_lower for kw in ('product', 'tovar', 'tov', 'bottle', 'pack', 'main', 'hero')) or
                info.file_size > 30_000
            )

            # Иконки и декор отбрасываем явно
            if any(kw in name_lower for kw in ('icon', 'logo', 'star', 'arrow', 'check', 'sprite', 'bg', 'pattern')):
                if info.file_size < 50_000:
                    is_product = False

            images.append({
                'path': info.filename,
                'name': name,
                'size': info.file_size,
                'is_product': is_product,
            })

    # Продуктовые сначала, потом по размеру (большие = вероятнее основные фото)
    images.sort(key=lambda i: (not i['is_product'], -i['size']))
    return images


def run_scan_only(zip_path: str) -> dict:
    """
    Шаг 1: только сканирование без диалога.
    Возвращает всё что нашёл — кандидаты продукта, цены, фото, гео.
    Frontend показывает форму с этими данными как defaults.
    """
    from scripts import scanner

    text = scanner.read_zip_text(zip_path)
    cur_sym, price_new_str, price_old_str = scanner.detect_prices(text)

    product_candidates = scanner.detect_product_candidates(text)
    detected_country_lang = scanner.detect_country_lang(text)
    prod_images = scanner.detect_images(text)

    # widget цены из data-атрибутов
    widget_prices = {}
    for attr, val in re.findall(r'data-(new|old)-price="([^"]+)"', text):
        if attr == 'new':
            widget_prices['widget_price_new'] = val.strip()
        else:
            widget_prices['widget_price_old'] = val.strip()

    # Обогащённый список всех картинок с метаинформацией
    enriched_images = _enrich_images(zip_path, prod_images)

    return {
        'product':            scanner.detect_product(text),
        'product_candidates': [{'word': w, 'count': c} for w, c in product_candidates[:10]],
        'cur_sym':            cur_sym,
        'price_new_str':      price_new_str,
        'price_old_str':      price_old_str,
        'widget_price_new':   widget_prices.get('widget_price_new'),
        'widget_price_old':   widget_prices.get('widget_price_old'),
        'detected_country':   detected_country_lang,
        'prod_images':        prod_images,
        'all_images':         enriched_images,
    }


# ══════════════════════════════════════════════════════════════
# ADAPT — шаг 2 после SCAN
# ══════════════════════════════════════════════════════════════
def run_adapt(zip_path: str, params: dict,
              extra_asset_dirs: list | None = None,
              do_clean: bool = True,
              do_inject: bool = True) -> tuple[str | None, CaptureResult]:
    """
    Применяет адаптацию по параметрам формы.

    params:
      geo_id:        'BO'
      product_old:   'Uro Active'
      product_new:   'Prostex'
      price_new:     '599 BOB'
      price_old:     '1198 BOB'
      image_map:     {'old.png': 'new.webp', ...}

    extra_asset_dirs — доп. директории с медиа-заменами (изолированные по задаче),
    ищутся перед глобальной storage/assets/.
    do_clean — сначала очистить ленд от чужих скриптов/файлов/инпутов
    (clean_rules.json). do_inject — вставить нашу обвязку (init/backfix/hidden-
    инпуты/виджет/маска/api.php), идемпотентно. Оба по умолчанию ВКЛ (регламент:
    clean → inject → адаптация).
    """
    from scripts import scanner, adapt, clean, inject
    from datetime import datetime

    def _process():
        from scripts.scanner import section, ok

        with workdir(STORAGE):
            geos = scanner.load_geos()
            geo_id = params['geo_id']
            if geo_id not in geos:
                print(f"\x1b[91mГЕО {geo_id} не найдено в geos.json\x1b[0m")
                return None
            geo = geos[geo_id]

            zip_resolved = str(Path(zip_path).resolve())
            offer_id = Path(zip_path).stem  # из ИСХОДНОГО имени (не cleaned)

            # Промежуточные архивы (clean/inject) для последующего удаления.
            tmp_files: list[str] = []

            # Очистка от чужих скриптов/файлов/инпутов ДО замен (регламент §раздел 2).
            if do_clean:
                try:
                    section("CLEAN (удаление чужих скриптов)")
                    cleaned_path, ctotal = clean.process_zip(zip_resolved, clean.load_rules())
                    tmp_files.append(cleaned_path)
                    zip_resolved = str(Path(cleaned_path).resolve())
                    ok(f"Очищено: скриптов={len(ctotal.get('scripts', []))} "
                       f"блоков={len(ctotal.get('blocks', []))} "
                       f"инпутов={len(ctotal.get('inputs', []))} "
                       f"файлов={len(ctotal.get('removed', []))}")
                except Exception as e:  # noqa: BLE001
                    print(f"\x1b[93mCLEAN пропущен (ошибка): {e}\x1b[0m")

            # Вставка НАШЕЙ обвязки (init/backfix/hidden-инпуты/виджет/маска/api.php).
            # Идемпотентно: для Keitaro-доноров с готовой обвязкой inject пропустит,
            # для сырых лендов (скачаны с сайта/архива) — вставит (регламент §раздел 5).
            if do_inject:
                try:
                    section("INJECT (вставка наших скриптов/инпутов)")
                    inject_params = {
                        'country':      geo_id,
                        'language':     geo.get('lang') or geo.get('lang_html') or geo_id,
                        'exclude_word': params.get('exclude_word', '') or '',
                        'price_new':    params.get('price_new', '') or '',
                        'price_old':    params.get('price_old', '') or '',
                        'prod_img':     (next(iter((params.get('image_map') or {}).values()), '')
                                         or 'product.webp'),
                        'custom_replacements': parse_custom_replacements(
                            params.get('custom_replacements', '')),
                    }
                    injected_path = inject.process_zip(zip_resolved, inject_params)
                    tmp_files.append(injected_path)
                    zip_resolved = str(Path(injected_path).resolve())
                    ok("Обвязка вставлена (или уже присутствовала)")
                except Exception as e:  # noqa: BLE001
                    print(f"\x1b[93mINJECT пропущен (ошибка): {e}\x1b[0m")
            text = scanner.read_zip_text(zip_resolved)
            cur_sym, price_new_str, price_old_str = scanner.detect_prices(text)
            product_candidates = scanner.detect_product_candidates(text)

            widget_prices = {}
            for attr, val in re.findall(r'data-(new|old)-price="([^"]+)"', text):
                if attr == 'new':
                    widget_prices['widget_price_new'] = val.strip()
                else:
                    widget_prices['widget_price_old'] = val.strip()

            # Если сканер не нашёл цену автоматически, но пользователь указал её вручную
            # (поле source_price_str в UI) — используем её как исходную цену для поиска в тексте
            source_price_str = params.get('source_price_str', '').strip()
            if source_price_str and not price_new_str:
                price_new_str = source_price_str

            # Если пользователь поправил исходные значения цены (src_price_*)
            src_pnn = params.get('src_price_new_num', '').strip()
            src_pnc = params.get('src_price_new_cur', '').strip()
            src_pon = params.get('src_price_old_num', '').strip()
            src_poc = params.get('src_price_old_cur', '').strip()

            if src_pnn and src_pnc:
                price_new_str = f"{src_pnn} {src_pnc}".strip()
            elif src_pnn:
                price_new_str = src_pnn
            if src_pon and src_poc:
                price_old_str = f"{src_pon} {src_poc}".strip()
            elif src_pon:
                price_old_str = src_pon
            if src_pnc:
                cur_sym = src_pnc

            found = {
                'product':            params.get('product_old') or scanner.detect_product(text),
                'product_candidates': product_candidates,
                'cur_sym':            cur_sym,
                'price_new_str':      price_new_str,
                'price_old_str':      price_old_str,
                'widget_price_new':   widget_prices.get('widget_price_new'),
                'widget_price_old':   widget_prices.get('widget_price_old'),
                'country_lang':       scanner.detect_country_lang(text),
                'prod_images':        scanner.detect_images(text),
                '_raw_text':          text,
            }

            section(f"BUILD CONFIG {geo_id}")
            config = scanner.build_config(
                found, geo_id, geo,
                params['product_new'],
                params['price_new'],
                params['price_old'],
                params.get('image_map', {}),
                parse_custom_replacements(params.get('custom_replacements', '')),
                params.get('exclude_word') or '',
                params.get('price_new_num', ''),
                params.get('price_new_cur', ''),
                params.get('price_old_num', ''),
                params.get('price_old_cur', ''),
            )

            # Сохраняем конфиг в storage/configs (cwd сейчас = storage)
            configs_dir = Path('configs')
            configs_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            config_path = configs_dir / f"{geo_id}_{offer_id}_{ts}.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
            ok(f"Config saved: {config_path.name}")

            section("APPLY ADAPTATION")
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                src_root = Path(tmp) / 'src'
                dst_root = Path(tmp) / 'dst'
                dst_root.mkdir()

                with zipfile.ZipFile(zip_resolved, 'r') as zf:
                    zf.extractall(src_root)

                stats = adapt.process_offer(src_root, dst_root, config, verbose=True,
                                            extra_asset_dirs=extra_asset_dirs)

                ok(f"Files changed:    {stats['files']}")
                ok(f"Replacements:     {stats['replacements']}")
                ok(f"Images replaced:  {stats['images']}")
                if stats['missing']:
                    from scripts.scanner import warn
                    for m in stats['missing']:
                        warn(f"Missing in assets/: {m}")

                # Упаковка в storage/output (cwd=storage), потом move в outputs
                output_dir = Path('output')
                output_dir.mkdir(parents=True, exist_ok=True)
                out_path = output_dir / f"{offer_id}__{geo_id}__{ts}.zip"

                with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for fp in dst_root.rglob('*'):
                        if fp.is_file():
                            zf.write(fp, fp.relative_to(dst_root))

                ok(f"Result: {out_path.name}")
                # Промежуточные архивы (clean/inject) больше не нужны.
                for tmp in tmp_files:
                    try:
                        Path(tmp).unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001
                        pass
                return str(out_path.resolve())

    out_path, capture = run_with_capture(_process)
    if out_path:
        out_path = str(move_to_outputs(out_path))
    return out_path, capture


# ══════════════════════════════════════════════════════════════
# Хелперы
# ══════════════════════════════════════════════════════════════
def load_geos() -> dict:
    """Грузит geos.json для дропдауна на фронте."""
    from scripts import scanner
    return scanner.load_geos()


def load_verticals() -> list[dict]:
    """Список вертикалей из inject.py."""
    from scripts.inject import VERTICALS
    return [
        {'id': k, 'label': label, 'exclude_word': excl}
        for k, (label, excl) in VERTICALS.items()
    ]

# ══════════════════════════════════════════════════════════════
# OPTIMIZE — конвертация изображений в WebP
# ══════════════════════════════════════════════════════════════

def run_scan_optimize(zip_path: str) -> dict:
    """Сканирование zip — что будет конвертировано (без обработки)."""
    from scripts import optimize
    return optimize.scan_zip(zip_path)


def run_optimize(zip_path: str) -> tuple[str | None, CaptureResult]:
    """Конвертация PNG/JPG/JPEG → WebP, обновление ссылок в HTML/CSS/PHP."""
    from scripts import optimize
    from utils.capture import CaptureResult, LogLine
    import warnings, traceback
    from datetime import datetime

    out_dir = (STORAGE / 'outputs').resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    src = Path(zip_path)
    stem = src.stem
    for marker in ('__clean__', '__injected__', '__optimized__'):
        stem = stem.split(marker)[0]

    log_lines = []
    out_path = None

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            optimize.process_zip(str(src.resolve()), str(out_dir))
    except Exception as e:
        log_lines.append(LogLine(text=f"[Error] {e}", level="error"))
        log_lines.append(LogLine(text=traceback.format_exc(), level="error"))
        return None, CaptureResult(lines=log_lines)

    # Ищем самый свежий __optimized__ файл в outputs
    all_optimized = sorted(
        out_dir.glob("*__optimized__*.zip"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )
    if all_optimized:
        out_path = str(all_optimized[0])
        log_lines.append(LogLine(text=f"Готово: {all_optimized[0].name}", level="success"))
    else:
        log_lines.append(LogLine(text="Файл не найден в outputs", level="error"))

    return out_path, CaptureResult(lines=log_lines)
