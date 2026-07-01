"""PHP-block protection: replace <?php...?> with placeholders before parsing."""


def protect(text: str) -> tuple[str, dict[str, str]]:
    """Вырезает PHP-блоки, возвращает (очищенный_текст, словарь_плейсхолдеров)."""
    placeholders: dict[str, str] = {}
    counter = 0
    result = []
    pos = 0
    n = len(text)

    while pos < n:
        s = text.find('<?', pos)
        if s == -1:
            result.append(text[pos:])
            break
        if text[s:s+3] == '<?=' or text[s:s+5] == '<?php':
            e = text.find('?>', s + 2)
            key = f'__PHPV2_{counter}__'
            if e == -1:
                result.append(text[pos:s])
                placeholders[key] = text[s:]
                result.append(key)
                pos = n
            else:
                result.append(text[pos:s])
                placeholders[key] = text[s:e+2]
                result.append(key)
                pos = e + 2
            counter += 1
        else:
            result.append(text[pos:s+2])
            pos = s + 2

    return ''.join(result), placeholders


def restore(text: str, placeholders: dict[str, str]) -> str:
    for key, original in placeholders.items():
        text = text.replace(key, original)
    return text
