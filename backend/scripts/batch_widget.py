"""
batch_widget.py — пакетная обработка лендингов: вставка НОВОГО виджета.

Что делает для каждого zip:
  1. Читает основной HTML (index.php / index.html)
  2. Распознаёт страну, язык, вертикаль, продукт (как inject)
  3. Если в HTML уже есть НОВЫЙ виджет (<!--New-widget-start-->) — обновляет его параметры
  4. Если есть только СТАРЫЙ виджет (universal-widget-combined без new- маркеров) —
     закомментирует его и добавит новый рядом
  5. Если виджетов нет — добавит новый перед form_mask или </body>
  6. Возвращает строку HTML и метаданные (что нашли, что подставили)

НЕ трогает остальные файлы в архиве, не упаковывает обратно — отдаём только HTML.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

# ── Шаблон НОВОГО виджета ───────────────────────────────────
NEW_WIDGET_TEMPLATE = """<!--New-widget-start-->
<div
    data-click-data='<?= $clickJson ?>'
    data-lead-endpoint="./"
    data-thx-page="main"
    data-exclude-word="{exclude_word}"
    data-pixid="{{pixid}}"
    data-gua="{{gua}}"
    data-ymc="{{ymc}}"
    data-no-analytics
    data-country="{country}"
    data-language="{language}"
    data-product-name="{product_name}"
    data-callback-discount-headline="{discount}"
    id="universal-widget-combined"
></div>
<script type="module" src="universal_widget_combined-EhohBfjG.js"></script>
<!--New-widget-end-->"""


# ── Маркеры ─────────────────────────────────────────────────
NEW_WIDGET_START = '<!--New-widget-start-->'
NEW_WIDGET_END   = '<!--New-widget-end-->'

# Регулярка старого виджета (без new-маркеров).
# Устойчива к > внутри атрибутов (data-click-data="<?= $clickJson ?>" содержит >),
# поддерживает многострочные теги.
OLD_WIDGET_RE = re.compile(
    r'<div\b'
    r'(?:[^>"\']|"[^"]*"|\'[^\']*\')*?'      # атрибуты (с > внутри кавычек ОК)
    r'\bid\s*=\s*["\']universal-widget-combined["\']'
    r'(?:[^>"\']|"[^"]*"|\'[^\']*\')*?'
    r'>\s*</div>\s*'
    r'<script\b[^>]*\bsrc\s*=\s*["\'][^"\']+["\'][^>]*>\s*</script>',
    re.DOTALL | re.IGNORECASE,
)

# Маркер маски формы (перед ним вставляем виджет если ничего нет)
FORM_MASK_MARKER = re.compile(
    r'<!--\s*Маска формы заказа\s+START\s*-->|<script[^>]*id=["\']form_mask["\']',
    re.IGNORECASE,
)


# ── Распознавание параметров ────────────────────────────────
COUNTRIES = {
    'AR': 'Аргентина', 'BO': 'Боливия', 'BR': 'Бразилия', 'CL': 'Чили',
    'CO': 'Колумбия', 'CR': 'Коста-Рика', 'DO': 'Доминиканская Республика',
    'EC': 'Эквадор', 'GT': 'Гватемала', 'HN': 'Гондурас', 'MX': 'Мексика',
    'NI': 'Никарагуа', 'PA': 'Панама', 'PE': 'Перу', 'PR': 'Пуэрто Рико',
    'PY': 'Парагвай', 'SV': 'Сальвадор', 'UY': 'Уругвай', 'VE': 'Венесуэла',
}

# Маппинг "вертикальный код в имени группы оффера" -> exclude_word
VERTICAL_TO_EXCLUDE = {
    'PR':  'pr ',  'PROSTATITIS': 'pr ',
    'PT':  'pt ',  'POTENCY':     'pt ',
    'JO':  'jo ',  'JOINT':       'jo ',
    'HY':  'hy ',  'HYPERTENSION':'hy ',
    'PA':  'pa ',  'PARASITES':   'pa ',
    'BE':  'be ',  'BEAUTY':      'be ',  'WEIGHT': 'be ',
    'DI':  'di ',  'DIABETES':    'di ',  'DIAB':   'di ',
    'VI':  'vi ',  'VISION':      'vi ',  'EH':     'vi ',
    'EN':  'en ',  'ENERGY':      'en ',
    'HA':  'ha ',  'HAIR':        'ha ',
    'SF':  'sf ',  'SLIM':        'sf ',
    'BR':  'br ',  'VARIX':       'br ',
}

# Распознавание языка из паттерна "[land es -]" / "[pl es -]" в названии
LANG_RE = re.compile(r'\[(?:land|pl)\s+([a-z]{2})\b', re.IGNORECASE)


@dataclass
class ProcessResult:
    """Результат обработки одного zip."""
    file_id: str                                  # 16988 — для имени выходного файла
    source_name: str                              # 16988_offer_archive.zip
    success: bool = False
    status: str = ''                              # 'inserted' | 'updated' | 'replaced_old' | 'no_html' | 'error'
    error: Optional[str] = None
    html: Optional[str] = None                    # обработанный HTML
    detected: dict = field(default_factory=dict)  # что распознали
    log: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────
# Утилиты распознавания
# ──────────────────────────────────────────────────────────────
def extract_id_from_name(filename: str) -> str:
    """
    16988_offer_archive.zip -> '16988'
    8d16dd3b__16988_offer_archive.zip -> '16988' (UID-префикс от save_upload отбрасываем)
    Если ID не найден — возвращает stem.
    """
    name = Path(filename).name

    # Убираем UID-префикс вида 'aabbccdd__' если он стоит вначале
    m_prefix = re.match(r'^[a-f0-9]{6,12}__(.+)$', name)
    if m_prefix:
        name = m_prefix.group(1)

    # Берём первое число (минимум 3 цифры — иначе можно случайно поймать что попало)
    m = re.match(r'^(\d{3,})', name)
    if m:
        return m.group(1)
    return Path(filename).stem


def find_main_html(zf: zipfile.ZipFile) -> Optional[str]:
    """Ищет главный HTML/PHP в архиве."""
    candidates = []
    for name in zf.namelist():
        low = name.lower()
        if low.endswith(('index.php', 'index.html', 'index.htm')):
            depth = name.count('/')
            candidates.append((depth, len(name), name))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def detect_country_from_html(html: str) -> Optional[str]:
    """Ищет существующее значение страны в HTML."""
    # data-country="MX"
    m = re.search(r'data-country=["\']([A-Za-z]{2})["\']', html)
    if m:
        return m.group(1).upper()
    # <input name="country" value="MX">
    m = re.search(r'name=["\']country["\'][^>]*value=["\']([A-Za-z]{2})["\']', html)
    if m:
        return m.group(1).upper()
    m = re.search(r'value=["\']([A-Za-z]{2})["\'][^>]*name=["\']country["\']', html)
    if m:
        return m.group(1).upper()
    return None


def detect_language_from_html(html: str, source_name: str = '') -> Optional[str]:
    """Сначала из имени файла-маркера [land es -], потом из HTML."""
    m = LANG_RE.search(source_name)
    if m:
        return m.group(1).lower()
    m = re.search(r'data-language=["\']([a-z]{2})["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r'name=["\']language["\'][^>]*value=["\']([a-z]{2})["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r'<html[^>]*lang=["\']([a-z]{2})["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    return None


def detect_exclude_word_from_html(html: str) -> Optional[str]:
    """Ищет существующий exclude_word в HTML."""
    m = re.search(r'data-exclude-word=["\']([^"\']*)["\']', html)
    if m:
        return m.group(1)
    m = re.search(r'name=["\']exclude_word["\'][^>]*value=["\']([^"\']*)["\']', html)
    if m:
        return m.group(1)
    return None


def detect_product_from_html(html: str) -> Optional[str]:
    """Ищет существующее имя продукта."""
    m = re.search(r'data-product-name=["\']([^"\']*)["\']', html)
    if m:
        return m.group(1)
    return None


def detect_from_excel_name(name: str) -> dict:
    """
    Парсит имя файла/группы оффера:
      'PR Bururan CO'              -> {vertical: 'PR', product: 'Bururan', country: 'CO'}
      '[PROSTATITIS-CO]'           -> {vertical_full: 'PROSTATITIS', country: 'CO'}
      '[POTENCY-PE-PT_0134]'       -> {vertical_full: 'POTENCY', country: 'PE', vertical: 'PT'}
      '[land es -]'                -> {language: 'es'}
    """
    out = {}
    # [VERTICAL-COUNTRY] или [VERTICAL-COUNTRY-XX]
    m = re.search(r'\[([A-Z]+)-([A-Z]{2})(?:-([A-Z]+))?', name)
    if m:
        v_full, country, v_short = m.groups()
        out['vertical_full'] = v_full
        out['country'] = country
        if v_short:
            # XX_0134 — возможный код вертикали
            v_short_clean = v_short.split('_')[0]
            if v_short_clean in VERTICAL_TO_EXCLUDE:
                out['vertical'] = v_short_clean
        if v_full in VERTICAL_TO_EXCLUDE and 'vertical' not in out:
            # POTENCY/PROSTATITIS даёт exclude_word
            pass
    # Язык из [land es -] / [pl es -]
    m = LANG_RE.search(name)
    if m:
        out['language'] = m.group(1).lower()
    return out


# ──────────────────────────────────────────────────────────────
# Главная функция вставки виджета
# ──────────────────────────────────────────────────────────────
def comment_old_widget(html: str) -> tuple[str, bool]:
    """
    Если в HTML есть СТАРЫЙ виджет (без new-маркеров) — оборачиваем в комментарий.
    Если виджет уже внутри <!--New-widget-start--> — не трогаем.
    Возвращает (новый_html, было_ли_закомментировано).
    """
    # Не трогаем если уже внутри new-блока
    new_blocks = []
    for m in re.finditer(re.escape(NEW_WIDGET_START) + r'.*?' + re.escape(NEW_WIDGET_END),
                         html, re.DOTALL):
        new_blocks.append((m.start(), m.end()))

    def in_new_block(pos: int) -> bool:
        return any(s <= pos < e for s, e in new_blocks)

    matches = list(OLD_WIDGET_RE.finditer(html))
    matches = [m for m in matches if not in_new_block(m.start())]

    if not matches:
        return html, False

    # Комментируем КАЖДОЕ найденное вхождение
    # Идём с конца чтобы не сбивать индексы
    for m in reversed(matches):
        original = m.group(0)
        commented = f"<!-- OLD_WIDGET_START\n{original}\nOLD_WIDGET_END -->"
        html = html[:m.start()] + commented + html[m.end():]

    return html, True


def has_new_widget(html: str) -> bool:
    return NEW_WIDGET_START in html


def replace_new_widget(html: str, new_block: str) -> str:
    """Заменяет существующий <!--New-widget-start-->...end на new_block."""
    pattern = re.escape(NEW_WIDGET_START) + r'.*?' + re.escape(NEW_WIDGET_END)
    return re.sub(pattern, new_block, html, count=1, flags=re.DOTALL)


def insert_new_widget(html: str, new_block: str) -> tuple[str, str]:
    """
    Вставляет new_block в подходящее место.
    Возвращает (новый_html, описание_места).
    """
    # 1. Перед маской формы (по инструкции — там и должен быть)
    m = FORM_MASK_MARKER.search(html)
    if m:
        pos = m.start()
        return html[:pos] + new_block + '\n\n' + html[pos:], 'before form_mask'

    # 2. Перед </body>
    m = re.search(r'</body>', html, re.IGNORECASE)
    if m:
        pos = m.start()
        return html[:pos] + new_block + '\n\n' + html[pos:], 'before </body>'

    # 3. В самый конец
    return html + '\n' + new_block + '\n', 'end of file'


def build_widget_block(country: str, language: str, exclude_word: str,
                       product_name: str, discount: str = '50%') -> str:
    """Формирует HTML нового виджета по шаблону."""
    return NEW_WIDGET_TEMPLATE.format(
        country=country.lower(),
        language=language.lower(),
        exclude_word=exclude_word,
        product_name=product_name.strip() + ' ',  # инструкция: имя с пробелом в конце
        discount=discount,
    )


def process_zip_widget(zip_path: str | Path,
                       overrides: Optional[dict] = None,
                       discount: str = '50%') -> ProcessResult:
    """
    Обрабатывает один zip → возвращает результат с HTML.

    overrides — словарь, перебивающий автодетект:
      {'country': 'BO', 'language': 'es', 'exclude_word': 'pr ',
       'product_name': 'Bururan'}
    """
    zip_path = Path(zip_path)
    file_id = extract_id_from_name(zip_path.name)
    overrides = overrides or {}
    res = ProcessResult(file_id=file_id, source_name=zip_path.name)

    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            html_path = find_main_html(zf)
            if not html_path:
                res.status = 'no_html'
                res.error = 'Не найден index.php / index.html'
                res.log.append(res.error)
                return res

            with zf.open(html_path) as f:
                raw = f.read()
            try:
                html = raw.decode('utf-8')
            except UnicodeDecodeError:
                html = raw.decode('utf-8', errors='replace')

        res.log.append(f"HTML: {html_path} ({len(html)} bytes)")

        # ── Распознаём параметры ──────────────────────────
        from_html_country = detect_country_from_html(html)
        from_html_lang    = detect_language_from_html(html, zip_path.name)
        from_html_exclude = detect_exclude_word_from_html(html)
        from_html_product = detect_product_from_html(html)
        from_name         = detect_from_excel_name(zip_path.name)

        # Приоритет: overrides > html > имя файла
        country = (overrides.get('country')
                   or from_html_country
                   or from_name.get('country')
                   or '')

        language = (overrides.get('language')
                    or from_html_lang
                    or from_name.get('language')
                    or '')

        exclude_word = overrides.get('exclude_word') or from_html_exclude
        if not exclude_word and from_name.get('vertical'):
            exclude_word = VERTICAL_TO_EXCLUDE.get(from_name['vertical'], '')
        if not exclude_word and from_name.get('vertical_full'):
            exclude_word = VERTICAL_TO_EXCLUDE.get(from_name['vertical_full'], '')

        product_name = overrides.get('product_name') or from_html_product or ''

        # Fallback — если product_name не нашли в data-атрибутах виджета,
        # используем scanner.detect_product (ищет повторяющиеся имена в тексте)
        if not product_name:
            try:
                from scripts import scanner
                detected_product = scanner.detect_product(html)
                if detected_product:
                    product_name = detected_product
                    res.log.append(f"Продукт распознан scanner: {product_name}")
            except Exception as e:
                res.log.append(f"scanner.detect_product упал: {e}")

        res.detected = {
            'country':       country,
            'language':      language,
            'exclude_word':  exclude_word,
            'product_name':  product_name,
            'from_html':     {
                'country':      from_html_country,
                'language':     from_html_lang,
                'exclude_word': from_html_exclude,
                'product':      from_html_product,
            },
            'from_name':     from_name,
        }

        # ── Валидация ────────────────────────────────────
        missing = []
        if not country:      missing.append('country')
        if not language:     missing.append('language')
        if not exclude_word: missing.append('exclude_word')
        if not product_name: missing.append('product_name')

        if missing:
            res.error = f"Не удалось определить: {', '.join(missing)}"
            res.log.append(res.error)
            res.status = 'error'
            res.html = html  # возвращаем как есть
            return res

        # exclude_word обязательно с пробелом в конце
        if not exclude_word.endswith(' '):
            exclude_word = exclude_word.rstrip() + ' '

        # ── Сборка нового блока ──────────────────────────
        new_block = build_widget_block(
            country=country,
            language=language,
            exclude_word=exclude_word,
            product_name=product_name,
            discount=discount,
        )

        # ── Решение что делать ───────────────────────────
        if has_new_widget(html):
            html = replace_new_widget(html, new_block)
            res.status = 'updated'
            res.log.append("Существующий новый виджет обновлён")
        else:
            html, commented = comment_old_widget(html)
            if commented:
                res.log.append("Старый виджет закомментирован (OLD_WIDGET_START/END)")
            html, place = insert_new_widget(html, new_block)
            res.log.append(f"Новый виджет вставлен: {place}")
            res.status = 'replaced_old' if commented else 'inserted'

        res.html = html
        res.success = True
        return res

    except zipfile.BadZipFile:
        res.error = 'Битый ZIP-архив'
        res.log.append(res.error)
        res.status = 'error'
        return res
    except Exception as e:
        res.error = f"{type(e).__name__}: {e}"
        res.log.append(res.error)
        res.status = 'error'
        return res


def result_to_dict(res: ProcessResult) -> dict:
    """Сериализация для JSON-ответа."""
    d = asdict(res)
    # html не отдаём в общем dict — он большой и пойдёт отдельным download
    d.pop('html', None)
    return d