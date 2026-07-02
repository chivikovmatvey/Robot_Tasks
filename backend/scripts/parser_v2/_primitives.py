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


def replace_outside_attrs(raw_html: str, find_str: str, replace_str: str,
                          *, number_mode: bool = False) -> tuple[str, int]:
    """
    Заменяет find_str на replace_str ТОЛЬКО вне HTML-атрибутов.
    Пропускает содержимое ="..." и ='...', HTML-комментарии и содержимое
    <script>/<style>: там код и вёрстка, а не видимый текст (замена цены
    «50»→«229» превращала translate(-50%,…) в -229% и ломала вёрстку,
    а «50% DE DESCUENTO» становилось «229% DE DESCUENTO»).
    number_mode=True — find_str это голое число цены: заменяем только при
    «ценовых» границах (см. NUM_BAD_PREV/NEXT).
    """
    if not find_str or find_str not in raw_html:
        return raw_html, 0

    lower = raw_html.lower()
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

        if raw_html[i:i+find_len] == find_str:
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
            # Если атрибут содержит путь перед именем файла (img/prod3.png)
            # — заменяем весь путь целиком, не только имя
            slash_idx = val.rfind('/')
            if slash_idx != -1 and val[slash_idx + 1:] == _f:
                new_val = _r  # "img/prod3.png" → "Prostamexill.png"
            else:
                new_val = val.replace(_f, _r)
            count += 1
            return m.group(1) + new_val + m.group(3)

        raw_html = re.sub(pattern, _replacer, raw_html)

    return raw_html, count


def replace_in_named_attr(raw_html: str, attr_name: str,
                          find_str: str, replace_str: str) -> tuple[str, int]:
    """Заменяет find_str на replace_str ТОЛЬКО внутри конкретного атрибута attr_name."""
    if not find_str or find_str not in raw_html:
        return raw_html, 0

    count = 0
    pattern = (
        r'(' + re.escape(attr_name) + r'\s*=\s*["\'])'
        r'([^"\']*?' + re.escape(find_str) + r'[^"\']*?)'
        r'(["\'])'
    )

    def _replacer(m, _f=find_str, _r=replace_str):
        nonlocal count
        count += 1
        return m.group(1) + m.group(2).replace(_f, _r) + m.group(3)

    raw_html = re.sub(pattern, _replacer, raw_html)
    return raw_html, count
