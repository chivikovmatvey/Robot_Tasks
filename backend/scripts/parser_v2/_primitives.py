"""Low-level string replacement primitives for HTML."""

import re

FILE_ATTRS = frozenset({
    'src', 'srcset', 'data-product-image', 'data-src', 'data-lazy-src',
    'href', 'poster', 'data-bg', 'data-background',
})


# Границы для замены ГОЛОГО числа цены (number_mode): сосед-буква/цифра/%
# означает, что число — часть другого токена, а не цена:
#   50% (скидка/keyframes)  #4caf50 (hex-цвет)  {50:...} / vn[50] (ключи JS)
#   1.50 / 24,50 (часть другого числа)  -50 (CSS-отступ)
NUM_BAD_PREV = frozenset(
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_%.,-')
NUM_BAD_NEXT = frozenset(
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_%')


# Символы, которые «приклеивают» слово к соседнему токену: если продукт окружён
# ими, это часть другого слова (vitaminas ⊃ vita), а не название продукта.
WORD_CHARS = frozenset(
    '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_')


def apply_case(sample: str, replace_str: str) -> str:
    """Переносит регистр найденного текста на замену.

    'DIAFAST' → 'NUEVO', 'diafast' → 'nuevo', 'Diafast'/смешанный → как задано
    (канонический вид из параметров адаптации).
    """
    letters = [c for c in sample if c.isalpha()]
    if len(letters) >= 2:
        if all(c.isupper() for c in letters):
            return replace_str.upper()
        if all(c.islower() for c in letters):
            return replace_str.lower()
    return replace_str


def word_pattern(find_str: str):
    """Регекс для поиска названия продукта БЕЗ учёта регистра.

    - границы слова по WORD_CHARS: «Vita» не совпадёт внутри «vitaminas»;
    - пробелы в названии матчат любой пробельный ряд («Focus  Clear»).
    Без этих границ регистронезависимый поиск ломал бы обычные слова текста.
    """
    parts = [re.escape(p) for p in find_str.split() if p]
    if not parts:
        return None
    body = r'\s+'.join(parts)
    return re.compile(
        r'(?<![0-9A-Za-z_])' + body + r'(?![0-9A-Za-z_])', re.IGNORECASE)


def replace_text_ci(text: str, find_str: str, replace_str: str) -> tuple[str, int]:
    """Регистронезависимая замена названия продукта во ВСЁМ тексте (js/json/txt —
    там нет HTML-структуры). Регистр найденного переносится на замену."""
    pat = word_pattern(find_str)
    if not pat:
        return text, 0
    count = 0

    def _sub(m):
        nonlocal count
        count += 1
        return apply_case(m.group(0), replace_str)

    return pat.sub(_sub, text), count


def replace_outside_attrs(raw_html: str, find_str: str, replace_str: str,
                          *, number_mode: bool = False,
                          ci: bool = False) -> tuple[str, int]:
    """
    Заменяет find_str на replace_str ТОЛЬКО вне HTML-атрибутов.
    Пропускает содержимое ="..." и ='...', HTML-комментарии и содержимое
    <script>/<style>: там код и вёрстка, а не видимый текст (замена цены
    «50»→«229» превращала translate(-50%,…) в -229% и ломала вёрстку,
    а «50% DE DESCUENTO» становилось «229% DE DESCUENTO»).
    number_mode=True — find_str это голое число цены: заменяем только при
    «ценовых» границах (см. NUM_BAD_PREV/NEXT).
    ci=True — регистронезависимо, с границами слова и переносом регистра на
    замену (для названия продукта: DIAFAST/Diafast/diafast — все варианты).
    """
    if not find_str:
        return raw_html, 0
    pat = word_pattern(find_str) if ci else None
    if ci:
        if not pat or not pat.search(raw_html):
            return raw_html, 0
    elif find_str not in raw_html:
        return raw_html, 0

    lower = raw_html.lower()
    first_lo = find_str[0].lower()
    result = []
    i = 0
    n = len(raw_html)
    find_len = len(find_str)
    count = 0

    while i < n:
        ch = raw_html[i]
        if ch == '<':
            # <!-- комментарий --> целиком
            if raw_html.startswith('<!--', i):
                j = raw_html.find('-->', i)
                j = n if j == -1 else j + 3
                result.append(raw_html[i:j])
                i = j
                continue
            # <script>/<style> вместе с содержимым — не трогаем
            skipped = False
            for tag in ('script', 'style'):
                tl = len(tag) + 1
                if lower.startswith('<' + tag, i) and \
                        (i + tl >= n or not raw_html[i + tl].isalnum()):
                    close = lower.find('</' + tag, i)
                    if close == -1:
                        j = n
                    else:
                        gt = raw_html.find('>', close)
                        j = n if gt == -1 else gt + 1
                    result.append(raw_html[i:j])
                    i = j
                    skipped = True
                    break
            if skipped:
                continue
            result.append(ch)
            i += 1
            continue

        if ch == '=' and i + 1 < n and raw_html[i+1] in ('"', "'"):
            quote = raw_html[i+1]
            j = raw_html.find(quote, i + 2)
            if j == -1:
                result.append(raw_html[i:])
                break
            result.append(raw_html[i:j+1])
            i = j + 1
            continue

        if ci:
            # Регистронезависимо: пробуем регекс только там, где совпал первый
            # символ (полный match на каждой позиции был бы дорогим).
            m = pat.match(raw_html, i) if lower[i] == first_lo else None
            if m:
                result.append(apply_case(m.group(0), replace_str))
                count += 1
                i = m.end()
                continue
        elif raw_html[i:i+find_len] == find_str:
            if number_mode:
                prev = raw_html[i-1] if i > 0 else ''
                nxt = raw_html[i+find_len] if i + find_len < n else ''
                if prev in NUM_BAD_PREV or nxt in NUM_BAD_NEXT:
                    result.append(ch)
                    i += 1
                    continue
            result.append(replace_str)
            count += 1
            i += find_len
            continue

        result.append(raw_html[i])
        i += 1

    return ''.join(result), count


def replace_in_file_attrs(raw_html: str, find_str: str, replace_str: str) -> tuple[str, int]:
    """Заменяет find_str ТОЛЬКО внутри файловых атрибутов (src, srcset, data-src, ...)."""
    count = 0
    for attr in FILE_ATTRS:
        pattern = (
            r'(' + re.escape(attr) + r'\s*=\s*["\'])'
            r'([^"\']*?' + re.escape(find_str) + r'[^"\']*?)'
            r'(["\'])'
        )

        def _replacer(m, _f=find_str, _r=replace_str):
            nonlocal count
            val = m.group(2)
            # Заменяем СЕГМЕНТ имени файла целиком (вместе с префиксом до
            # границы пути) — идемпотентно: повторная адаптация поверх уже
            # адаптированного текста (VSL/работа поверх output) не должна
            # наслаивать префиксы (кейс neuro_x → neuro_neuro_x при простом
            # val.replace). Границы сегмента: / , пробел, ?, #.
            seg = re.compile(r'[^\s,/?#]*' + re.escape(_f) + r'[^\s,?#]*')
            # 1) отбрасываем каталоги перед сегментом с _f — новый файл в корне
            #    ("img/prod3.png" → "prod3.png"; работает и внутри srcset)
            tmp = re.sub(r'[^\s,]*/(?=[^\s,/?#]*' + re.escape(_f) + r')', '', val)
            # 2) сегмент имени (с любыми префиксами/суффиксами до ?#) → _r
            new_val = seg.sub(_r, tmp)
            count += 1
            return m.group(1) + new_val + m.group(3)

        raw_html = re.sub(pattern, _replacer, raw_html)

    return raw_html, count


def replace_in_named_attr(raw_html: str, attr_name: str,
                          find_str: str, replace_str: str,
                          *, ci: bool = False) -> tuple[str, int]:
    """Заменяет find_str на replace_str ТОЛЬКО внутри конкретного атрибута attr_name.

    ci=True — без учёта регистра (значение атрибута может быть в любом виде:
    data-product-name="DIAFAST").
    """
    if not find_str:
        return raw_html, 0
    inner = word_pattern(find_str) if ci else None
    if ci:
        if not inner or not inner.search(raw_html):
            return raw_html, 0
    elif find_str not in raw_html:
        return raw_html, 0

    count = 0
    pattern = (
        # (?<![-\w:]) — имя атрибута целиком: 'alt' не должен ловиться внутри
        # 'data-salt', 'title' — внутри 'data-title'.
        r'(?<![-\w:])'
        r'(' + re.escape(attr_name) + r'\s*=\s*["\'])'
        r'([^"\']*?' + (inner.pattern if ci else re.escape(find_str)) + r'[^"\']*?)'
        r'(["\'])'
    )

    def _replacer(m, _f=find_str, _r=replace_str):
        nonlocal count
        count += 1
        val = m.group(2)
        val = inner.sub(lambda im: apply_case(im.group(0), _r), val) if ci \
            else val.replace(_f, _r)
        return m.group(1) + val + m.group(3)

    raw_html = re.sub(pattern, _replacer, raw_html,
                      flags=re.IGNORECASE if ci else 0)
    return raw_html, count
