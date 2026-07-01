"""Low-level string replacement primitives for HTML."""

import re

FILE_ATTRS = frozenset({
    'src', 'srcset', 'data-product-image', 'data-src', 'data-lazy-src',
    'href', 'poster', 'data-bg', 'data-background',
})


def replace_outside_attrs(raw_html: str, find_str: str, replace_str: str) -> tuple[str, int]:
    """
    Заменяет find_str на replace_str ТОЛЬКО вне HTML-атрибутов.
    Пропускает содержимое ="..." и ='...'.
    Не трогает значения атрибутов (src, data-*, class и т.д.).
    """
    if not find_str or find_str not in raw_html:
        return raw_html, 0

    result = []
    i = 0
    n = len(raw_html)
    find_len = len(find_str)
    count = 0

    while i < n:
        if raw_html[i] == '=' and i + 1 < n and raw_html[i+1] in ('"', "'"):
            quote = raw_html[i+1]
            j = raw_html.find(quote, i + 2)
            if j == -1:
                result.append(raw_html[i:])
                break
            result.append(raw_html[i:j+1])
            i = j + 1
            continue

        if raw_html[i:i+find_len] == find_str:
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
