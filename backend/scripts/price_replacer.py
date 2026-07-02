"""
price_replacer.py — умная замена цен в HTML лендингов.

Решает проблемы слепого str.replace:
  - Понимает форматирование числа: 24.390 / 24,390 / 24 390 → все это 24390
  - Не дублирует символ валюты если он уже стоит рядом
  - Заменяет Elementor split-price (<span>49980</span> CRC)
  - Безопасен к PHP-блокам (protect/restore из adapt.py)
"""

import re
from typing import Optional


# ══════════════════════════════════════════════════════════════
# БАЗА ФОРМАТИРОВАНИЯ ПО ГЕО
# ══════════════════════════════════════════════════════════════

GEO_CURRENCY_FORMAT: dict[str, dict] = {
    # Латинская Америка
    "MX": {"thousands": ".", "decimal": ",", "symbol": "MXN", "pos": "after",  "space": True},
    "CO": {"thousands": ".", "decimal": ",", "symbol": "COP", "pos": "after",  "space": True},
    "PE": {"thousands": ".", "decimal": ",", "symbol": "PEN", "pos": "after",  "space": True},
    "AR": {"thousands": ".", "decimal": ",", "symbol": "$",   "pos": "after",  "space": True},
    "CL": {"thousands": ".", "decimal": ",", "symbol": "CLP", "pos": "after",  "space": True},
    "BO": {"thousands": ".", "decimal": ",", "symbol": "BOB", "pos": "after",  "space": True},
    "EC": {"thousands": ".", "decimal": ",", "symbol": "$",   "pos": "after",  "space": True},
    "PY": {"thousands": ".", "decimal": ",", "symbol": "PYG", "pos": "after",  "space": True},
    "UY": {"thousands": ".", "decimal": ",", "symbol": "UYU", "pos": "after",  "space": True},
    "VE": {"thousands": ".", "decimal": ",", "symbol": "VES", "pos": "after",  "space": True},
    "GT": {"thousands": ",", "decimal": ".", "symbol": "GTQ", "pos": "after",  "space": True},
    "HN": {"thousands": ",", "decimal": ".", "symbol": "HNL", "pos": "after",  "space": True},
    "SV": {"thousands": ",", "decimal": ".", "symbol": "$",   "pos": "after",  "space": True},
    "NI": {"thousands": ",", "decimal": ".", "symbol": "NIO", "pos": "after",  "space": True},
    "CR": {"thousands": ".", "decimal": ",", "symbol": "CRC", "pos": "after",  "space": True},
    "PA": {"thousands": ",", "decimal": ".", "symbol": "$",   "pos": "after",  "space": True},
    "DO": {"thousands": ",", "decimal": ".", "symbol": "DOP", "pos": "after",  "space": True},
    "CU": {"thousands": ".", "decimal": ",", "symbol": "CUP", "pos": "after",  "space": True},

    # Европа
    "PL": {"thousands": " ", "decimal": ",", "symbol": "zł",  "pos": "after",  "space": True},
    "CZ": {"thousands": " ", "decimal": ",", "symbol": "Kč",  "pos": "after",  "space": True},
    "HU": {"thousands": " ", "decimal": ",", "symbol": "Ft",  "pos": "after",  "space": True},
    "RO": {"thousands": ".", "decimal": ",", "symbol": "lei", "pos": "after",  "space": True},
    "BG": {"thousands": " ", "decimal": ",", "symbol": "лв.", "pos": "after",  "space": True},
    "UA": {"thousands": " ", "decimal": ",", "symbol": "грн","pos": "after",  "space": True},
    "RS": {"thousands": ".", "decimal": ",", "symbol": "RSD", "pos": "after",  "space": True},
    "HR": {"thousands": ".", "decimal": ",", "symbol": "EUR", "pos": "after",  "space": True},
    "SK": {"thousands": " ", "decimal": ",", "symbol": "EUR", "pos": "after",  "space": True},
    "SI": {"thousands": ".", "decimal": ",", "symbol": "EUR", "pos": "after",  "space": True},
    "BA": {"thousands": ".", "decimal": ",", "symbol": "BAM", "pos": "after",  "space": True},
    "MK": {"thousands": ".", "decimal": ",", "symbol": "MKD", "pos": "after",  "space": True},
    "AL": {"thousands": ".", "decimal": ",", "symbol": "ALL", "pos": "after",  "space": True},
    "DE": {"thousands": ".", "decimal": ",", "symbol": "€",   "pos": "after",  "space": True},
    "FR": {"thousands": " ", "decimal": ",", "symbol": "€",   "pos": "after",  "space": True},
    "IT": {"thousands": ".", "decimal": ",", "symbol": "€",   "pos": "after",  "space": True},
    "ES": {"thousands": ".", "decimal": ",", "symbol": "€",   "pos": "after",  "space": True},
    "PT": {"thousands": ".", "decimal": ",", "symbol": "€",   "pos": "after",  "space": True},
    "GR": {"thousands": ".", "decimal": ",", "symbol": "€",   "pos": "after",  "space": True},
    "AT": {"thousands": ".", "decimal": ",", "symbol": "€",   "pos": "after",  "space": True},
    "CH": {"thousands": "'", "decimal": ".", "symbol": "CHF", "pos": "after",  "space": True},
    "NO": {"thousands": " ", "decimal": ",", "symbol": "NOK", "pos": "after",  "space": True},
    "SE": {"thousands": " ", "decimal": ",", "symbol": "SEK", "pos": "after",  "space": True},
    "DK": {"thousands": ".", "decimal": ",", "symbol": "DKK", "pos": "after",  "space": True},
    "GB": {"thousands": ",", "decimal": ".", "symbol": "£",   "pos": "before", "space": False},
    "MD": {"thousands": ".", "decimal": ",", "symbol": "MDL", "pos": "after",  "space": True},
    "BY": {"thousands": " ", "decimal": ",", "symbol": "BYN", "pos": "after",  "space": True},
    "GE": {"thousands": " ", "decimal": ",", "symbol": "GEL", "pos": "after",  "space": True},
    "AM": {"thousands": " ", "decimal": ",", "symbol": "AMD", "pos": "after",  "space": True},
    "AZ": {"thousands": " ", "decimal": ",", "symbol": "AZN", "pos": "after",  "space": True},

    # Азия
    "IN": {"thousands": ",", "decimal": ".", "symbol": "₹",   "pos": "before", "space": False},
    "PK": {"thousands": ",", "decimal": ".", "symbol": "PKR", "pos": "after",  "space": True},
    "BD": {"thousands": ",", "decimal": ".", "symbol": "BDT", "pos": "after",  "space": True},
    "LK": {"thousands": ",", "decimal": ".", "symbol": "LKR", "pos": "after",  "space": True},
    "TH": {"thousands": ",", "decimal": ".", "symbol": "฿",   "pos": "before", "space": False},
    "ID": {"thousands": ".", "decimal": ",", "symbol": "Rp",  "pos": "before", "space": False},
    "MY": {"thousands": ",", "decimal": ".", "symbol": "RM",  "pos": "before", "space": False},
    "PH": {"thousands": ",", "decimal": ".", "symbol": "₱",   "pos": "before", "space": False},
    "VN": {"thousands": ".", "decimal": ",", "symbol": "₫",   "pos": "after",  "space": False},
    "KH": {"thousands": ",", "decimal": ".", "symbol": "KHR", "pos": "after",  "space": True},
    "TR": {"thousands": ".", "decimal": ",", "symbol": "₺",   "pos": "after",  "space": False},

    # СНГ
    "RU": {"thousands": " ", "decimal": ",", "symbol": "₽",   "pos": "after",  "space": True},
    "KZ": {"thousands": " ", "decimal": ",", "symbol": "₸",   "pos": "after",  "space": True},
    "UZ": {"thousands": " ", "decimal": ",", "symbol": "UZS", "pos": "after",  "space": True},
    "KG": {"thousands": " ", "decimal": ",", "symbol": "KGS", "pos": "after",  "space": True},
    "TJ": {"thousands": " ", "decimal": ",", "symbol": "TJS", "pos": "after",  "space": True},
    "TM": {"thousands": " ", "decimal": ",", "symbol": "TMT", "pos": "after",  "space": True},

    # Ближний Восток / Африка
    "SA": {"thousands": ",", "decimal": ".", "symbol": "SAR", "pos": "after",  "space": True},
    "AE": {"thousands": ",", "decimal": ".", "symbol": "AED", "pos": "after",  "space": True},
    "EG": {"thousands": ",", "decimal": ".", "symbol": "EGP", "pos": "after",  "space": True},
    "IQ": {"thousands": ",", "decimal": ".", "symbol": "IQD", "pos": "after",  "space": True},
    "MA": {"thousands": ".", "decimal": ",", "symbol": "MAD", "pos": "after",  "space": True},
    "DZ": {"thousands": ".", "decimal": ",", "symbol": "DZD", "pos": "after",  "space": True},
    "IL": {"thousands": ",", "decimal": ".", "symbol": "₪",   "pos": "before", "space": False},
    "NG": {"thousands": ",", "decimal": ".", "symbol": "₦",   "pos": "before", "space": False},
    "KE": {"thousands": ",", "decimal": ".", "symbol": "KES", "pos": "before", "space": False},
    "GH": {"thousands": ",", "decimal": ".", "symbol": "GHS", "pos": "before", "space": False},
    "ZA": {"thousands": " ", "decimal": ".", "symbol": "R",   "pos": "before", "space": False},
    "TZ": {"thousands": ",", "decimal": ".", "symbol": "TZS", "pos": "after",  "space": True},
    "UG": {"thousands": ",", "decimal": ".", "symbol": "UGX", "pos": "after",  "space": True},
    "SN": {"thousands": ".", "decimal": ",", "symbol": "XOF", "pos": "after",  "space": True},
    "CM": {"thousands": ".", "decimal": ",", "symbol": "XAF", "pos": "after",  "space": True},

    # США и Канада
    "US": {"thousands": ",", "decimal": ".", "symbol": "$",   "pos": "before", "space": False},
    "CA": {"thousands": ",", "decimal": ".", "symbol": "$",   "pos": "before", "space": False},
}


def get_geo_format(geo_id: str) -> Optional[dict]:
    """Возвращает форматирование для ГЕО или None."""
    return GEO_CURRENCY_FORMAT.get(geo_id.upper())


def format_price(amount: int, geo_id: str, custom_symbol: Optional[str] = None) -> str:
    """
    Форматирует число в строку цены по правилам ГЕО.

    format_price(24390, "CO")  → "24.390 COP"
    format_price(12500, "AR")  → "12.500 $"
    format_price(2499, "IN")   → "₹2499"
    format_price(99, "PL")     → "99 zł"
    """
    fmt = get_geo_format(geo_id)
    if not fmt:
        return f"{amount} {custom_symbol or geo_id}"

    symbol = custom_symbol or fmt["symbol"]
    sep    = fmt["thousands"]

    # Форматируем число с разделителем тысяч
    if sep:
        s = str(amount)
        groups = []
        while len(s) > 3:
            groups.insert(0, s[-3:])
            s = s[:-3]
        groups.insert(0, s)
        formatted_num = sep.join(groups)
    else:
        formatted_num = str(amount)

    space = " " if fmt["space"] else ""

    if fmt["pos"] == "before":
        return f"{symbol}{space}{formatted_num}"
    else:
        return f"{formatted_num}{space}{symbol}"


# ══════════════════════════════════════════════════════════════
# НОРМАЛИЗАЦИЯ ЧИСЛА ИЗ СТРОКИ ЦЕНЫ
# ══════════════════════════════════════════════════════════════

def parse_price_amount(price_str: str) -> Optional[int]:
    """
    Извлекает числовую часть из строки цены.

    "24.390 COP" → 24390
    "12,500 $"   → 12500
    "₹2,499"     → 2499
    "49980 CRC"  → 49980
    """
    # Убираем символы валюты и буквенные коды, оставляем цифры и разделители
    s = re.sub(r'[^\d.,\s]', ' ', price_str).strip()
    if not s:
        return None

    digit_groups = re.findall(r'\d+', s)
    if not digit_groups:
        return None

    if len(digit_groups) == 1:
        return int(digit_groups[0])

    # Если последняя группа == 3 цифры — это разделитель тысяч
    if len(digit_groups[-1]) == 3:
        return int(''.join(digit_groups))
    else:
        # Последняя группа — дробная часть, берём всё до неё
        return int(''.join(digit_groups[:-1]))


# ══════════════════════════════════════════════════════════════
# REGEX ДЛЯ ПОИСКА ЧИСЛА
# ══════════════════════════════════════════════════════════════

_CURRENCY_TOKENS_RE = (
    r'₹|₽|₺|₴|₸|₦|₪|₱|฿|₫|฿|Rp|RM|zł|zl|Kč|Kc|Ft|lei|лв\.?|грн\.?|CHF|'
    # Генерик-код валюты (CRC, GTQ, MXN…) — ТОЛЬКО верхний регистр, даже под
    # re.IGNORECASE: иначе px/vh/deg/rem из CSS считались «валютой» и число
    # перед ними заменялось как цена (ломало вёрстку).
    r'(?-i:[A-Z]{2,4})'
)

# Символы которые могут стоять ДО числа
_SYM_BEFORE_RE = r'(?:\$|€|£|₹|₽|₺|₴|₸|₦|₪|₱|฿|₫|Rp|RM)'


def _make_price_regex(amount: int) -> re.Pattern:
    """
    Строит regex для поиска числа `amount` в любом форматировании.
    Например 24390 матчит: $24.390 / 24.390 / 24,390 COP / 24 390 / 24390
    """
    s = str(amount)
    groups = []
    while len(s) > 3:
        groups.insert(0, s[-3:])
        s = s[:-3]
    groups.insert(0, s)

    if len(groups) == 1:
        num_re = re.escape(groups[0])
    else:
        num_re = re.escape(groups[0]) + r'[.,\s]?' + r'[.,\s]?'.join(
            re.escape(g) for g in groups[1:]
        )

    full_pattern = (
        # Не цена, если сразу перед числом цифра, точка/запятая (часть другого
        # числа: 1,50 / 24.390) или минус (CSS: translate(-50%...), margin:-50px).
        r'(?<![\d.,-])'
        # Пробел до числа поглощаем ТОЛЬКО вместе с символом валюты ($ 50),
        # иначе он терялся при замене («Solo 50» → «Solo229»).
        r'(?:(' + _SYM_BEFORE_RE + r')\s?)?'
        r'(' + num_re + r')'
        r'\s?'
        r'(' + _CURRENCY_TOKENS_RE + r')?'
        # Не цена, если дальше буква/цифра/% — единицы измерения в CSS/JS:
        # 50% (keyframes, border-radius), 50px, 50s, 50deg, 50vh и т.п.
        r'(?![\w%])'
    )
    return re.compile(full_pattern, re.IGNORECASE)


# ══════════════════════════════════════════════════════════════
# УМНАЯ ЗАМЕНА
# ══════════════════════════════════════════════════════════════

def smart_replace_price(text: str, old_amount: int, new_price_str: str,
                        geo_id: Optional[str] = None) -> tuple[str, int]:
    """
    Заменяет все вхождения числа `old_amount` (в любом форматировании)
    на `new_price_str`, без дублирования валютного символа.

    Логика защиты от дублирования:
    - Если вхождение содержит валютный код ЦЕЛЕВОГО гео (new_price_str) — пропускаем.
    - Если вхождение содержит $-символ и целевой символ тоже $ — заменяем только число.
    """
    if not old_amount or not new_price_str:
        return text, 0

    pattern = _make_price_regex(old_amount)

    # Вычисляем целевой символ из new_price_str
    # Убираем число и остаток — символ
    new_sym_match = re.search(r'([^\d.,\s]+)', new_price_str)
    new_sym = new_sym_match.group(1).strip() if new_sym_match else ''
    new_sym_upper = new_sym.upper()

    count = 0

    def replacer(m: re.Match) -> str:
        nonlocal count
        full    = m.group(0)
        s_bef   = m.group(1) or ''  # символ ДО числа
        s_aft   = m.group(3) or ''  # символ ПОСЛЕ числа
        cur_aft = s_aft.upper()

        # Если рядом стоит НЕЛАТИНСКАЯ целевая валюта (€, zł, грн...) — пропускаем
        # (они безопасно заменяются отдельным ВАЛЮТА-правилом)
        if new_sym and not new_sym.isascii():
            if s_bef == new_sym or s_aft == new_sym:
                return full  # уже правильная валюта

        # Если рядом стоит буквенный код ЦЕЛЕВОЙ валюты — пропускаем
        if new_sym_upper and new_sym_upper.isalpha() and len(new_sym_upper) >= 2:
            if cur_aft == new_sym_upper or s_bef.upper() == new_sym_upper:
                return full

        count += 1
        return new_price_str

    new_text = pattern.sub(replacer, text)
    return new_text, count


def smart_replace_split_price(text: str, old_amount: int, new_amount: int) -> tuple[str, int]:
    """
    Elementor и похожие: число стоит голым в теге, рядом валюта.

    <span>49980 CRC </span>  →  <span>24990 CRC</span>
    <span>49980</span>       →  <span>24990</span>
    <span>24.390</span>      →  <span>159.000</span>

    Заменяет только само число, сохраняет разделитель тысяч и валюту.
    """
    if old_amount == new_amount:
        return text, 0

    s = str(old_amount)
    groups_o = []
    while len(s) > 3:
        groups_o.insert(0, s[-3:])
        s = s[:-3]
    groups_o.insert(0, s)

    ns = str(new_amount)
    groups_n = []
    while len(ns) > 3:
        groups_n.insert(0, ns[-3:])
        ns = ns[:-3]
    groups_n.insert(0, ns)

    num_re = re.escape(groups_o[0]) + r'[.,\s]?' + r'[.,\s]?'.join(
        re.escape(g) for g in groups_o[1:]
    )

    # Паттерн: > [число] [опц.валюта] <
    # Группы: 1=>  2=число  3=валюта(или None)  4=<
    pattern = re.compile(
        r'(>)\s*(' + num_re + r')\s*(' + _CURRENCY_TOKENS_RE + r')?\s*(<)',
        re.IGNORECASE
    )

    count = 0

    def replacer(m: re.Match) -> str:
        nonlocal count
        before      = m.group(1)        # '>'
        old_num_str = m.group(2)        # '49980' или '24.390'
        currency    = m.group(3) or ''  # 'CRC' или None
        after       = m.group(4)        # '<'

        sep_match = re.search(r'\d([.,\s])\d{3}', old_num_str)
        sep = sep_match.group(1) if sep_match else ''

        new_num_str = sep.join(groups_n) if (sep and len(groups_n) > 1) else str(new_amount)

        cur_part = (' ' + currency) if currency else ''
        count += 1
        return before + new_num_str + cur_part + after

    new_text = pattern.sub(replacer, text)
    return new_text, count


# ══════════════════════════════════════════════════════════════
# ПУБЛИЧНЫЙ API
# ══════════════════════════════════════════════════════════════

def apply_smart_prices(text: str, rules: list[dict],
                       geo_id: Optional[str] = None) -> tuple[str, int]:
    """
    Применяет ценовые правила из конфига через умную замену.
    Используется вместо слепого str.replace в adapt.py для ценовых правил.

    Правила с label ЦЕНА_* и WIDGET_ЦЕНА_* обрабатываются здесь.
    Остальные правила (ПРОДУКТ, ВАЛЮТА, СТРАНА_DATA...) обрабатываются
    обычным apply_replacements в adapt.py.
    """
    total = 0

    for r in rules:
        label   = r.get('label', '')
        old_str = r.get('find', '')
        new_str = r.get('replace', '')

        # WIDGET_ЦЕНА_* обрабатываются через обычный str.replace в apply_replacements
        # Здесь только ЦЕНА_* (голые ценовые строки в тексте/тегах)
        if not label.startswith('ЦЕНА_'):
            continue
        if not old_str or not new_str or old_str == new_str:
            continue

        old_amount = parse_price_amount(old_str)
        if not old_amount:
            continue

        new_text, n = smart_replace_price(text, old_amount, new_str, geo_id)
        if n:
            total += n
            text = new_text

    # Дополнительно: замена split-цен (Elementor) для числовых частей
    # Запускаем только если smart_replace_price не нашёл нужный паттерн
    for r in rules:
        label   = r.get('label', '')
        old_str = r.get('find', '')
        new_str = r.get('replace', '')
        if not label.startswith('ЦЕНА_'):
            continue

        old_amount = parse_price_amount(old_str)
        new_amount = parse_price_amount(new_str)
        if not old_amount or not new_amount or old_amount == new_amount:
            continue

        # Проверяем есть ли ещё в тексте старое число — если smart уже заменил, skip
        check_pattern = _make_price_regex(old_amount)
        if not check_pattern.search(text):
            continue  # уже заменено

        new_text, n = smart_replace_split_price(text, old_amount, new_amount)
        if n:
            total += n
            text = new_text

    return text, total
