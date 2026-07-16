"""
scanner.py — сканирует оффер и генерирует конфиг адаптации

Что ищет:
  - Название продукта
  - Цену (новую и старую) + валюту (включая zł, €, лв и др.)
  - Страну и язык (data-country, data-language, hidden inputs)
  - Фото продукта
  - Exclude word в hidden input
"""

import json
import re
import sys
import zipfile
from pathlib import Path

# Цвета — отключаем если Windows cmd (не поддерживает ANSI)
import os as _os
if _os.name != 'nt' or 'WT_SESSION' in _os.environ or 'ANSICON' in _os.environ:
    R="[91m"; G="[92m"; Y="[93m"
    B="[94m"; C="[96m"; DIM="[2m"
    RESET="[0m"; BOLD="[1m"
else:
    R=G=Y=B=C=DIM=RESET=BOLD=""

def ok(m):   print(f"  {G}✓{RESET}  {m}")
def warn(m): print(f"  {Y}!{RESET}  {m}")
def info(m): print(f"  {B}→{RESET}  {m}")
def err(m):  print(f"  {R}✗{RESET}  {m}")
def section(t): print(f"\n{C}{BOLD}{'─'*52}\n  {t}\n{'─'*52}{RESET}")


# ══════════════════════════════════════════════════════════════
# СПРАВОЧНИКИ
# ══════════════════════════════════════════════════════════════



# Символьные валюты → ISO-код
CURRENCY_SYMBOLS = {
    "zł": "PLN", "zl": "PLN",
    "Kč": "CZK", "Kc": "CZK",
    "Ft": "HUF",
    "lei": "RON",
    "лв": "BGN", "лв.": "BGN",
    "грн": "UAH", "грн.": "UAH",
    "₺": "TRY",
    "£": "GBP",
    "₹": "INR",
    "₽": "RUB",
    "€": "EUR",
    "$": "USD",
    "₴": "UAH",
}

# ISO-код → символ (обратный)
CODE_TO_SYM = {v: k for k, v in reversed(list(CURRENCY_SYMBOLS.items()))}

# Валюта → (страна, язык, название страны)
CURRENCY_GEO = {
    "MXN": ("MX", "ES", "México"),
    "BRL": ("BR", "PT", "Brasil"),
    "COP": ("CO", "ES", "Colombia"),
    "PEN": ("PE", "ES", "Perú"),
    "ARS": ("AR", "ES", "Argentina"),
    "CLP": ("CL", "ES", "Chile"),
    "BOB": ("BO", "ES", "Bolivia"),
    "HNL": ("HN", "ES", "Honduras"),
    "GTQ": ("GT", "ES", "Guatemala"),
    "NIO": ("NI", "ES", "Nicaragua"),
    "CRC": ("CR", "ES", "Costa Rica"),
    "DOP": ("DO", "ES", "República Dominicana"),
    "CUP": ("CU", "ES", "Cuba"),
    "PYG": ("PY", "ES", "Paraguay"),
    "UYU": ("UY", "ES", "Uruguay"),
    "VES": ("VE", "ES", "Venezuela"),
    "SVC": ("SV", "ES", "El Salvador"),
    "PAB": ("PA", "ES", "Panamá"),
    "RON": ("RO", "RO", "România"),
    "BGN": ("BG", "BG", "България"),
    "HUF": ("HU", "HU", "Magyarország"),
    "CZK": ("CZ", "CS", "Česká republika"),
    "PLN": ("PL", "PL", "Polska"),
    "UAH": ("UA", "UK", "Україна"),
    "KZT": ("KZ", "RU", "Қазақстан"),
    "GEL": ("GE", "KA", "საქართველო"),
    "MDL": ("MD", "RO", "Moldova"),
    "EUR": ("EU", "EN", "Europe"),
    "USD": ("US", "EN", "USA"),
    "GBP": ("GB", "EN", "United Kingdom"),
    "RUB": ("RU", "RU", "Россия"),
    "TRY": ("TR", "TR", "Türkiye"),
    "NGN": ("NG", "EN", "Nigeria"),
    "XOF": ("SN", "FR", "Sénégal"),
    "XAF": ("CM", "FR", "Cameroun"),
    "MAD": ("MA", "AR", "المغرب"),
    "DZD": ("DZ", "AR", "الجزائر"),
    "EGP": ("EG", "AR", "مصر"),
    "SAR": ("SA", "AR", "السعودية"),
    "AED": ("AE", "AR", "الإمارات"),
    "IQD": ("IQ", "AR", "العراق"),
    "KES": ("KE", "EN", "Kenya"),
    "TZS": ("TZ", "EN", "Tanzania"),
    "GHS": ("GH", "EN", "Ghana"),
    "UGX": ("UG", "EN", "Uganda"),
    "ETB": ("ET", "AM", " Ethiopia"),
    "ZAR": ("ZA", "EN", "South Africa"),
    "IDR": ("ID", "ID", "Indonesia"),
    "PHP": ("PH", "EN", "Pilipinas"),
    "VND": ("VN", "VI", "Việt Nam"),
    "THB": ("TH", "TH", "Thailand"),
    "MYR": ("MY", "MS", "Malaysia"),
    "PKR": ("PK", "UR", "Pakistan"),
    "BDT": ("BD", "BN", "Bangladesh"),
    "INR": ("IN", "HI", "India"),
    "LKR": ("LK", "SI", "Sri Lanka"),
    "KHR": ("KH", "KM", "Cambodia"),
    "AZN": ("AZ", "AZ", "Azerbaijan"),
    "AMD": ("AM", "HY", "Armenia"),
    "TJS": ("TJ", "TG", "Tajikistan"),
    "TMT": ("TM", "TK", "Turkmenistan"),
    "KGS": ("KG", "KY", "Kyrgyzstan"),
    "UZS": ("UZ", "UZ", "Uzbekistan"),
    "BYN": ("BY", "BE", "Belarus"),
    "MKD": ("MK", "MK", "Macedonia"),
    "ALL": ("AL", "SQ", "Albania"),
    "BAM": ("BA", "BS", "Bosnia"),
    "RSD": ("RS", "SR", "Serbia"),
    "ILS": ("IL", "HE", "Israel"),
    "NOK": ("NO", "NO", "Norway"),
    "SEK": ("SE", "SV", "Sweden"),
    "DKK": ("DK", "DA", "Denmark"),
    "CHF": ("CH", "DE", "Switzerland"),
}


def load_geos() -> dict:
    f = Path(__file__).parent / 'geos.json'
    data = json.loads(f.read_text(encoding='utf-8'))
    return {k: v for k, v in data.items() if not k.startswith('_')}


# ══════════════════════════════════════════════════════════════
# ЧТЕНИЕ АРХИВА
# ══════════════════════════════════════════════════════════════

def read_zip_text(zip_path: str) -> str:
    """Читает все PHP/HTML файлы из zip в одну строку."""
    text = ''
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for name in zf.namelist():
            if Path(name).suffix.lower() in {'.php', '.html', '.htm'}:
                try:
                    text += zf.read(name).decode('utf-8', errors='replace') + '\n'
                except Exception:
                    continue
    return text


def strip_php_for_price_search(text: str) -> str:
    """Убирает PHP блоки чтобы $ в переменных не считался валютой."""
    # Убираем <?php ... ?> блоки
    text = re.sub(r'<\?php.*?\?>', ' ', text, flags=re.DOTALL)
    # Убираем строки где $ это переменная PHP: $var, $_POST, $this и т.д.
    text = re.sub(r'\$[a-zA-Z_][a-zA-Z0-9_]*', ' ', text)
    return text


# ══════════════════════════════════════════════════════════════
# ДЕТЕКТОРЫ
# ══════════════════════════════════════════════════════════════

def detect_product_candidates(text: str) -> list[tuple[str, int]]:
    """
    Находит кандидатов на название продукта по частоте в тексте.
    Ищет слова/фразы с заглавной буквы которые встречаются 3+ раз.
    Возвращает список (слово, кол-во) отсортированный по убыванию.
    """
    # Убираем HTML теги, PHP, JS для чистого текста
    clean = re.sub(r'<[^>]+>', ' ', text)
    clean = re.sub(r'<\?php.*?\?>', ' ', clean, flags=re.DOTALL)
    clean = re.sub(r'\{[^}]+\}', ' ', clean)  # {subid} и прочее

    # Ищем слова/фразы с заглавной буквы (1-3 слова)
    # Исключаем служебные HTML/CSS слова
    SKIP = {
        'The', 'This', 'That', 'With', 'From', 'Your', 'Our', 'For',
        'And', 'But', 'Not', 'All', 'Are', 'Has', 'Have', 'Was',
        'JavaScript', 'CSS', 'HTML', 'PHP', 'URL', 'HTTP', 'HTTPS',
        'GET', 'POST', 'True', 'False', 'None', 'NULL',
        'Copyright', 'Privacy', 'Policy', 'Terms', 'Contact',
        'Order', 'Home', 'Menu', 'Back', 'Next', 'Send',
    }

    # Однословные капитализированные (не ALL_CAPS)
    counts: dict[str, int] = {}
    for m in re.finditer(r'\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)?)\b', clean):
        word = m.group(1).strip()
        if word not in SKIP and len(word) > 2:
            counts[word] = counts.get(word, 0) + 1

    # Фильтруем — минимум 3 вхождения
    candidates = [(w, c) for w, c in counts.items() if c >= 3]
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[:10]  # топ 10


def detect_product(text: str) -> str | None:
    """Возвращает самого частого кандидата или None."""
    candidates = detect_product_candidates(text)
    return candidates[0][0] if candidates else None


def detect_prices(text: str) -> tuple[str | None, str | None, str | None]:
    """
    Возвращает (валюта_как_в_файле, цена_новая_строка, цена_старая_строка).
    Например: ("zł", "99 zł", "198 zł")
    """
    candidates = []  # (число, строка_с_валютой, iso_код, символ_как_в_файле)

    # Чистый текст без PHP для поиска символьных валют (чтобы $ не ломал)
    clean_text = strip_php_for_price_search(text)

    # 1. data-new-price / data-old-price — самый надёжный источник (из оригинала).
    # Запоминаем отдельно, какие числа виджет считает new/old: если обе цены
    # распознаны, они имеют ПРИОРИТЕТ над min/max текстовых кандидатов.
    widget_vals: dict[str, list[tuple[int, str]]] = {"new": [], "old": []}
    for attr, val in re.findall(r'data-(new|old)-price="([^"]+)"', text):
        val = val.strip()
        # "99 zł", "590 MXN", "99€", "$590 MXN"
        # Поддерживаем формат с префиксом-символом и постфиксом-кодом: "$590 MXN"
        m = re.match(r'^(\d+)\s*(.+)$', val)
        if m:
            num, cur_raw = int(m.group(1)), m.group(2).strip()
            iso = (CURRENCY_SYMBOLS.get(cur_raw)
                   or (cur_raw.upper() if cur_raw.upper() in CURRENCY_GEO else None))
            if iso:
                candidates.append((num, val, iso, cur_raw))
                widget_vals[attr].append((num, iso))

    # 1b. Префиксный символ валюты перед числом: "L990" (HNL), "Rp50000" (IDR) и т.п.
    # Эти форматы не ловятся паттерном ^\d+ выше, обрабатываем отдельно.
    # Только в контексте data-атрибутов — чтобы не ловить "L" в SVG/CSS/тексте.
    PREFIX_CURRENCY = {
        "L":  "HNL",   # Гондурас — лемпира
        "Q":  "GTQ",   # Гватемала — кетсаль
        "Rp": "IDR",   # Индонезия — рупия
        "RM": "MYR",   # Малайзия — ринггит
        "Rs": "PKR",   # Пакистан — рупия
    }
    for attr, val in re.findall(r'data-(new|old)-price="([^"]+)"', text):
        val = val.strip()
        for prefix, iso in PREFIX_CURRENCY.items():
            m = re.match(r'^'  + re.escape(prefix) + r'(\d+)$', val)
            if m:
                num = int(m.group(1))
                candidates.append((num, val, iso, prefix))
                widget_vals[attr].append((num, iso))
                break

    # 2. Символьные валюты в clean_text (PHP переменные уже убраны).
    # Число может быть с разделителями тысяч: "$129.900" (COP), "1.290 zł",
    # "$39.990" (CLP). Группа из 3 цифр после ./пробела/, — это тысячи (НЕ
    # десятичная дробь, у которой обычно 2 цифры). Иначе из "$129.900" бралось
    # бы 129 — цена терялась/искажалась.
    NUM = r'(\d{1,3}(?:[.\s ]\d{3})+|\d{2,7})'

    def _to_int(raw: str) -> int:
        return int(re.sub(r'[.\s ]', '', raw))

    for sym, iso in CURRENCY_SYMBOLS.items():
        sym_esc = re.escape(sym)
        for m in re.finditer(NUM + r'\s*' + sym_esc, clean_text):
            candidates.append((_to_int(m.group(1)), m.group(0).strip(), iso, sym))
        for m in re.finditer(sym_esc + r'\s*' + NUM, clean_text):
            candidates.append((_to_int(m.group(1)), m.group(0).strip(), iso, sym))

    # 3. Буквенные коды: "590 MXN" / "590 mxn" (case-insensitive)
    for m in re.finditer(r'\b(\d{1,7}(?:[.,]\d{3})*)\s+([A-Za-z]{2,4})\b', text):
        raw_num = m.group(1).replace('.', '').replace(',', '')
        try:
            num = int(raw_num)
        except ValueError:
            continue
        cur = m.group(2).upper()
        if cur in CURRENCY_GEO:
            sym = m.group(2)  # сохраняем регистр как в файле (mxn / MXN)
            candidates.append((num, m.group(0).strip(), cur, sym))

    # 4. Число и валюта в РАЗНЫХ блоках: "<span>590</span><span>MXN</span>" или
    # "<b>$</b><b>590</b>" — между ними только теги/пробелы (любые типы тегов).
    # SEP_TAG требует хотя бы один тег → не дублирует уже найденные смежные пары.
    # ВАЖНО: ведущий \s* — СНАРУЖИ группы, а не внутри неё. Иначе \s* в конце
    # одной итерации и \s* в начале следующей перекрываются на одних и тех же
    # пробелах → катастрофический бэктрекинг (ReDoS): на прогонах из пробелов и
    # тегов, не оканчивающихся валютой, движок перебирал экспоненту разбиений и
    # скан подвисал на десятки секунд (вешая весь бэкенд). Один \s* на итерацию.
    # Не больше 4 тегов между числом и валютой: сплит-цена Elementor — это
    # СОСЕДНИЕ спаны. Неограниченный (…)+ перепрыгивал через целый SVG-блок и
    # спаривал «24990 CRC </span><svg>…<h2>50% REBAJADO» → мусорный кандидат 50.
    SEP_TAG = r'\s*(?:<[^<>]*>\s*){1,4}'
    # Число, за которым идёт % — скидка, а не цена (50% REBAJADO).
    NUM_BLK = r'(\d{1,3}(?:[.\s]\d{3})+|\d{2,7})(?!\s*%)'

    # 4a. буквенный код в отдельном блоке (число ⟶ код, и код ⟶ число)
    for m in re.finditer(NUM_BLK + SEP_TAG + r'([A-Za-z]{2,4})\b', text):
        cur = m.group(2).upper()
        if cur in CURRENCY_GEO:
            candidates.append((_to_int(m.group(1)), m.group(0).strip(), cur, m.group(2)))
    for m in re.finditer(r'\b([A-Za-z]{2,4})' + SEP_TAG + NUM_BLK, text):
        cur = m.group(1).upper()
        if cur in CURRENCY_GEO:
            candidates.append((_to_int(m.group(2)), m.group(0).strip(), cur, m.group(1)))

    # 4b. символ валюты в отдельном блоке (символ ⟶ число, и число ⟶ символ)
    for sym, iso in CURRENCY_SYMBOLS.items():
        sym_esc = re.escape(sym)
        for m in re.finditer(sym_esc + SEP_TAG + NUM_BLK, text):
            candidates.append((_to_int(m.group(1)), m.group(0).strip(), iso, sym))
        for m in re.finditer(NUM_BLK + SEP_TAG + sym_esc, text):
            candidates.append((_to_int(m.group(1)), m.group(0).strip(), iso, sym))

    if not candidates:
        return None, None, None

    # Выбираем самую частую валюту, но с приоритетом буквенных кодов над $
    # (поскольку $ часто бывает в data-атрибутах вместе с MXN/CLP/etc, а в верстке только без него)
    iso_counts: dict[str, int] = {}
    for _, _, iso, _ in candidates:
        iso_counts[iso] = iso_counts.get(iso, 0) + 1

    # Если кроме USD есть ещё какие-то ISO-коды — USD не выбираем
    non_usd = {k: v for k, v in iso_counts.items() if k != 'USD'}
    if non_usd:
        best_iso = max(non_usd, key=non_usd.get)
    else:
        best_iso = max(iso_counts, key=iso_counts.get)

    # Фильтруем по валюте, убираем мусор
    prices_this_cur = [(num, s, sym) for num, s, iso, sym in candidates
                       if iso == best_iso and num > 5]
    if not prices_this_cur:
        return None, None, None

    # Берём каноническое написание валюты:
    #  - буквенные коды: предпочитаем UPPERCASE (MXN > Mxn > mxn)
    #  - символьные: длиннее лучше (лв. > лв)
    #  - всегда штрафуем варианты с $-префиксом (типа "$590 MXN") — там валюта это часть data-атрибута
    all_syms = set(p[2] for p in prices_this_cur)

    def sym_priority(s: str):
        # Меньше — лучше
        has_dollar = 1 if '$' in s else 0
        is_alpha = s.isalpha() and len(s) <= 4 and s.upper() == s.lower().upper()
        # Для буквенных кодов: 0 если UPPER, 1 если Mixed/Title, 2 если lower
        if is_alpha:
            if s.isupper():    case_rank = 0
            elif s.islower():  case_rank = 2
            else:              case_rank = 1
            return (has_dollar, case_rank, -len(s))
        # Для символьных — длиннее лучше (лв. > лв)
        return (has_dollar, 0, -len(s))

    cur_sym = min(all_syms, key=sym_priority)

    # Виджет знает цены точно: если data-new-price И data-old-price дали числа
    # в выбранной валюте — берём их, а не min/max текстовых кандидатов (иначе
    # мусорное мелкое число из текста, напр. «50» из «50% OFF», становилось
    # «новой ценой»: CRC 50 / 49980 вместо 24990 / 49980).
    w_new = sorted({n for n, iso in widget_vals["new"] if iso == best_iso and n > 5})
    w_old = sorted({n for n, iso in widget_vals["old"] if iso == best_iso and n > 5})
    if w_new and w_old and w_new[0] != w_old[-1]:
        p_new = w_new[0]
        p_old = w_old[-1]
    else:
        nums = sorted(set(p[0] for p in prices_this_cur))
        p_new = min(nums)
        p_old = max(nums) if len(nums) > 1 else None
        # Старая цена на лендах = ровно 2× новой (правило техотдела). Если
        # среди кандидатов есть точное 2×новой — это старая цена, а max может
        # быть мусором из текста («в аптеке за 250 zł» из фейк-комментария
        # давал пару 59/250 вместо реальной 59/118).
        if p_old is not None and p_new * 2 in nums and p_old != p_new * 2:
            p_old = p_new * 2

    # Ищем точные строки в тексте для правил замены
    # Предпочитаем строку с cur_sym (чистый вариант, без $)
    def find_price_str(num):
        # Сначала ищем варианты с выбранным cur_sym
        for n, s, sym in prices_this_cur:
            if n == num and sym == cur_sym:
                return s
        # Иначе берём любой
        for n, s, _ in prices_this_cur:
            if n == num:
                return s
        return f"{num} {cur_sym}"

    def _clean(s):
        # Цена из разных блоков приходит с тегами ("590</span><span>MXN") —
        # чистим для отображения и извлечения числа. Замена всё равно идёт по
        # отдельным правилам ЧИСЛО/ВАЛЮТА, тегов не касаясь.
        if s is None:
            return None
        s = re.sub(r'<[^<>]*>', ' ', s)
        return re.sub(r'\s+', ' ', s).strip()

    return cur_sym, _clean(find_price_str(p_new)), _clean(find_price_str(p_old) if p_old else None)


def detect_all_price_strings(text: str, num: int) -> list[str]:
    """
    Возвращает все уникальные варианты написания цены с числом `num`,
    встречающиеся в тексте: '$590 MXN', '590 mxn', '590MXN' и т.п.
    Используется чтобы создать правила замены для всех написаний сразу.

    ВАЖНО: значения из data-new-price / data-old-price НЕ включаются сюда —
    они уже обрабатываются отдельными WIDGET_ЦЕНА_НОВ / WIDGET_ЦЕНА_СТА правилами
    в build_config. Если включить их сюда, то ЦЕНА_НОВ ("880 HNL") будет
    заменять текст тегов (<span>880 HNL</span>) через data-атрибут-строку
    ("data-new-price="599 BOB""), что ломает вёрстку.
    """
    if not num:
        return []

    clean_text = strip_php_for_price_search(text)
    found = []

    # Символьные валюты ("590 zł", "$590", "590€", "$129.900" с тысячами)
    _NUM = r'(\d{1,3}(?:[.\s]\d{3})+|\d{2,7})'
    def _ni(raw: str) -> int:
        return int(re.sub(r'[.\s]', '', raw))
    for sym in CURRENCY_SYMBOLS:
        sym_esc = re.escape(sym)
        for m in re.finditer(_NUM + r'\s*' + sym_esc, clean_text):
            if _ni(m.group(1)) == num:
                found.append(m.group(0).strip())
        for m in re.finditer(sym_esc + r'\s*' + _NUM, clean_text):
            if _ni(m.group(1)) == num:
                found.append(m.group(0).strip())

    # 3. Буквенные коды (case-insensitive) — '590 MXN', '590 mxn'
    for m in re.finditer(r'\b(\d{1,7}(?:[.,]\d{3})*)\s+([A-Za-z]{2,4})\b', text):
        raw_num = m.group(1).replace('.', '').replace(',', '')
        try:
            n = int(raw_num)
        except ValueError:
            continue
        if n == num and m.group(2).upper() in CURRENCY_GEO:
            found.append(m.group(0).strip())

    # Префиксные валюты в тексте тегов: "L990" (HNL), "Q590" (GTQ) и т.п.
    # Ищем только в контексте тегов (после > или пробела перед словом),
    # чтобы не ловить "L" в SVG/CSS/переменных.
    PREFIX_CURRENCY = {"L": "HNL", "Q": "GTQ", "Rp": "IDR", "RM": "MYR", "Rs": "PKR"}
    for prefix in PREFIX_CURRENCY:
        # Формат: L990 (без пробела между символом и числом)
        pat = r'(?:(?<=[>\s(,])|(?<="))' + re.escape(prefix) + r'(\d{2,7})(?=[^a-zA-Z\d])'
        for m in re.finditer(pat, text):
            if int(m.group(1)) == num:
                found.append(prefix + m.group(1))

    # Уникальные с сохранением порядка
    seen, uniq = set(), []
    for s in found:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def detect_country_lang(text: str) -> dict:
    """
    Возвращает все варианты написания страны/языка которые нашёл в файле.
    result = {
        'data_country': [...],   # значения data-country="..."
        'data_language': [...],  # значения data-language="..."
        'input_country': [...],  # value в hidden input name=country
        'input_language': [...], # value в hidden input name=language
        'lang_html': '...',      # lang="..." в <html>
        'exclude_word': '...',   # exclude_word в hidden input
    }
    """
    r = {
        'data_country': [],
        'data_language': [],
        'input_country': [],
        'input_language': [],
        'lang_html': None,
        'exclude_word': None,
    }

    # data-country="MX" или data-country="Polska"
    r['data_country'] = list(dict.fromkeys(
        v for v in re.findall(r'data-country=["\']([^"\' ]+)["\']', text)
        if not v.startswith('{') and not v.startswith('_')
    ))
    # data-language="ES"
    r['data_language'] = list(dict.fromkeys(
        re.findall(r'data-language=["\']([^"\']+)["\']', text)
    ))
    # <input name="country" value="PL">
    r['input_country'] = list(dict.fromkeys(
        re.findall(r'name=["\']country["\'][^>]*value=["\']([^"\']+)["\']', text) +
        re.findall(r'value=["\']([^"\']+)["\'][^>]*name=["\']country["\']', text)
    ))
    # <input name="language" value="PL">
    r['input_language'] = list(dict.fromkeys(
        re.findall(r'name=["\']language["\'][^>]*value=["\']([^"\']+)["\']', text) +
        re.findall(r'value=["\']([^"\']+)["\'][^>]*name=["\']language["\']', text)
    ))
    # lang="pl"
    m = re.search(r'<html[^>]+lang=["\']([a-z]{2})["\']', text, re.IGNORECASE)
    r['lang_html'] = m.group(1) if m else None

    # exclude_word
    m = re.search(r'name=["\']exclude_word["\'][^>]*value=["\']([^"\']*)["\']', text)
    if not m:
        m = re.search(r'value=["\']([^"\']*)["\'][^>]*name=["\']exclude_word["\']', text)
    r['exclude_word'] = m.group(1) if m else None

    return r


def detect_images(text: str) -> list[str]:
    """Находит имена файлов изображений продукта."""
    imgs = re.findall(r'["\']([^"\']*prod[^"\']*\.(?:png|jpg|jpeg|gif|webp))["\']',
                      text, re.IGNORECASE)
    return list(dict.fromkeys(Path(p).name for p in imgs))


def detect_all_images(zip_path: str) -> list[str]:
    """Возвращает все изображения из архива."""
    with zipfile.ZipFile(zip_path, 'r') as zf:
        return [Path(n).name for n in zf.namelist()
                if Path(n).suffix.lower() in {'.png','.jpg','.jpeg','.gif','.webp','.svg'}]


# ══════════════════════════════════════════════════════════════
# ГЕНЕРАЦИЯ КОНФИГА
# ══════════════════════════════════════════════════════════════

def build_config(found: dict, geo_id: str, geo: dict,
                 new_product: str, price_new_str: str, price_old_str: str,
                 image_map: dict, custom_replacements: list[dict] | None = None,
                 exclude_word_new: str = '',
                 price_new_num: str = '', price_new_cur: str = '',
                 price_old_num: str = '', price_old_cur: str = '') -> dict:
    """
    Генерирует конфиг. Строит точные правила только из того что реально нашлось.

    price_new_num/price_new_cur — разделённые компоненты целевой цены.
    Если заданы, генерируются правила ЦЕНА_НОВ_ЧИСЛО / ЦЕНА_НОВ_ВАЛЮТА.
    """
    sp      = found['product'] or 'Product'
    cur_sym = found['cur_sym']          # "zł", "MXN", "€" — как в файле
    cl      = found['country_lang']     # dict из detect_country_lang

    dc      = geo['currency']           # "BGN"
    dl      = geo['lang']               # "BG"
    dlh     = geo['lang_html']          # "bg"
    dcn     = geo['country_name']       # "България"
    # Символ целевой валюты
    dc_sym  = CODE_TO_SYM.get(dc, dc)  # "лв." для BGN

    rules = []

    # ── 1. Продукт ───────────────────────────────────────────
    rules.append({"label": "ПРОДУКТ", "find": sp, "replace": new_product})

    # ── 2a. Виджет (data-new-price / data-old-price) — ПЕРЕД обычными ценами!
    # Иначе обычная замена цены превратит "data-old-price=$1180 MXN" в
    # "data-old-price=599 BOB MXN", и виджет-правило не сработает.
    w_new = found.get('widget_price_new') or found.get('price_new_str')
    w_old = found.get('widget_price_old') or found.get('price_old_str')

    # Если заданы split-компоненты — собираем widget-значение из них
    if price_new_num or price_new_cur:
        wn_num = price_new_num or re.sub(r'\D', '', found.get('price_new_str', ''))
        wn_cur = price_new_cur or cur_sym
        widget_new_val = f"{wn_num} {wn_cur}".strip()
    else:
        widget_new_val = price_new_str
    if price_old_num or price_old_cur:
        wo_num = price_old_num or re.sub(r'\D', '', found.get('price_old_str', ''))
        wo_cur = price_old_cur or cur_sym
        widget_old_val = f"{wo_num} {wo_cur}".strip()
    else:
        widget_old_val = price_old_str

    if w_new:
        rules.append({"label": "WIDGET_ЦЕНА_НОВ",
                      "find": f'data-new-price="{w_new}"',
                      "replace": f'data-new-price="{widget_new_val}"'})
    if w_old:
        rules.append({"label": "WIDGET_ЦЕНА_СТА",
                      "find": f'data-old-price="{w_old}"',
                      "replace": f'data-old-price="{widget_old_val}"'})
    # data-widget-variant НЕ трогаем: по регламенту пустая строка = рандомный
    # вариант виджета, конкретный ставится только по просьбе баера.

    # ── 2b. Цены в верстке ──────────────────────────────────────────
    # Если переданы разделённые компоненты (число + валюта) —
    # генерируем отдельные правила для каждого, иначе — комбинированные
    raw_text = found.get('_raw_text', '')

    if price_new_num or price_new_cur:
        # Разделённые правила: число и валюта отдельно
        if found['price_new_str']:
            new_num_match = re.match(r'^\D*(\d+)', found['price_new_str'])
            new_num_int = int(new_num_match.group(1)) if new_num_match else None
            new_variants = detect_all_price_strings(raw_text, new_num_int) if (raw_text and new_num_int) else []
            if not new_variants:
                new_variants = [found['price_new_str']]
            new_variants.sort(key=len, reverse=True)
            for i, variant in enumerate(new_variants, 1):
                label = "ЦЕНА_НОВ_ЧИСЛО" if i == 1 else f"ЦЕНА_НОВ_ЧИСЛО_{i}"
                v_num_m = re.match(r'^\D*(\d+)', variant)
                old_num_str = v_num_m.group(1) if v_num_m else variant
                if price_new_num and old_num_str != price_new_num:
                    rules.append({"label": label, "find": old_num_str, "replace": price_new_num})
        if cur_sym and price_new_cur and cur_sym != price_new_cur:
            rules.append({"label": "ЦЕНА_НОВ_ВАЛЮТА", "find": cur_sym, "replace": price_new_cur})
    else:
        # Комбинированные правила (старый behaviour)
        if found['price_new_str']:
            new_num_match = re.match(r'^\D*(\d+)', found['price_new_str'])
            new_num = int(new_num_match.group(1)) if new_num_match else None
            new_variants = detect_all_price_strings(raw_text, new_num) if (raw_text and new_num) else []
            if not new_variants:
                new_variants = [found['price_new_str']]
            new_variants.sort(key=len, reverse=True)
            for i, variant in enumerate(new_variants, 1):
                label = "ЦЕНА_НОВ" if i == 1 else f"ЦЕНА_НОВ_{i}"
                rules.append({"label": label, "find": variant, "replace": price_new_str})

    if price_old_num or price_old_cur:
        if found['price_old_str']:
            old_num_match = re.match(r'^\D*(\d+)', found['price_old_str'])
            old_num_int = int(old_num_match.group(1)) if old_num_match else None
            old_variants = detect_all_price_strings(raw_text, old_num_int) if (raw_text and old_num_int) else []
            if not old_variants:
                old_variants = [found['price_old_str']]
            old_variants.sort(key=len, reverse=True)
            for i, variant in enumerate(old_variants, 1):
                label = "ЦЕНА_СТА_ЧИСЛО" if i == 1 else f"ЦЕНА_СТА_ЧИСЛО_{i}"
                v_num_m = re.match(r'^\D*(\d+)', variant)
                old_num_str = v_num_m.group(1) if v_num_m else variant
                if price_old_num and old_num_str != price_old_num:
                    rules.append({"label": label, "find": old_num_str, "replace": price_old_num})
        if cur_sym and price_old_cur and cur_sym != price_old_cur:
            rules.append({"label": "ЦЕНА_СТА_ВАЛЮТА", "find": cur_sym, "replace": price_old_cur})
    else:
        if found['price_old_str']:
            old_num_match = re.match(r'^\D*(\d+)', found['price_old_str'])
            old_num = int(old_num_match.group(1)) if old_num_match else None
            old_variants = detect_all_price_strings(raw_text, old_num) if (raw_text and old_num) else []
            if not old_variants:
                old_variants = [found['price_old_str']]
            old_variants.sort(key=len, reverse=True)
            for i, variant in enumerate(old_variants, 1):
                label = "ЦЕНА_СТА" if i == 1 else f"ЦЕНА_СТА_{i}"
                rules.append({"label": label, "find": variant, "replace": price_old_str})

    # ── 3. Валюта — только безопасные символы (не $, не буквенные коды)
    # $ нельзя заменять глобально — ломает PHP переменные
    # Буквенные коды (MXN, CLP) нельзя — слишком короткие, могут быть частью слов
    SAFE_CURRENCY_SYMS = {"zł", "zl", "Kč", "Kc", "Ft", "lei", "лв", "лв.", "грн", "грн.", "₺", "£", "₹", "₽", "€", "₴"}
    if cur_sym and cur_sym != dc_sym and cur_sym in SAFE_CURRENCY_SYMS:
        rules.append({"label": "ВАЛЮТА", "find": cur_sym, "replace": dc_sym})

    # ── 4. data-country / data-language ──────────────────────
    for val in cl['data_country']:
        rules.append({"label": "СТРАНА_DATA",
                      "find": f'data-country="{val}"',
                      "replace": f'data-country="{geo_id}"'})
    for val in cl['data_language']:
        rules.append({"label": "LANG_DATA",
                      "find": f'data-language="{val}"',
                      "replace": f'data-language="{dl}"'})

    # ── 5. lang= в <html> ─────────────────────────────────────
    if cl['lang_html']:
        rules.append({"label": "LANG_HTML",
                      "find": f'lang="{cl["lang_html"]}"',
                      "replace": f'lang="{dlh}"'})

    # ── 6. Hidden inputs: country / language ─────────────────
    for val in cl['input_country']:
        rules.append({"label": "INP_COUNTRY",
                      "find": f'name="country" value="{val}"',
                      "replace": f'name="country" value="{geo_id}"'})
        # Вариант с обратным порядком атрибутов
        rules.append({"label": "INP_COUNTRY2",
                      "find": f'value="{val}" name="country"',
                      "replace": f'value="{geo_id}" name="country"'})
    for val in cl['input_language']:
        rules.append({"label": "INP_LANG",
                      "find": f'name="language" value="{val}"',
                      "replace": f'name="language" value="{dl}"'})
        rules.append({"label": "INP_LANG2",
                      "find": f'value="{val}" name="language"',
                      "replace": f'value="{dl}" name="language"'})

    # ── 6b. Hidden exclude_word (до замены фото в коде) ─────────
    ew_old = cl.get('exclude_word')
    ew_new = exclude_word_new or ''
    if ew_old and ew_new and ew_old != ew_new:
        rules.append({"label": "EXCL_WORD",
                      "find": f'name="exclude_word" value="{ew_old}"',
                      "replace": f'name="exclude_word" value="{ew_new}"'})
        rules.append({"label": "EXCL_WORD2",
                      "find": f'value="{ew_old}" name="exclude_word"',
                      "replace": f'value="{ew_new}" name="exclude_word"'})

    # ── 7. form_mask data-country ─────────────────────────────
    # (уже покрыто правилами СТРАНА_DATA выше — они заменят везде)

    # ── 8. Фото продукта в коде ──────────────────────────────
    for old_img, new_img in image_map.items():
        new_name = Path(new_img).name
        # PROD_IMG: ищем полный путь (img/prod3.png) → заменяем на имя (Prostamexill.png)
        # Это гарантирует что img/ не останется в атрибуте src
        rules.append({"label": "PROD_IMG",
                      "find": old_img, "replace": new_name})
        # Также ищем только имя файла на случай если путь уже был без папки
        old_name = Path(old_img).name
        if old_name != old_img:
            rules.append({"label": "PROD_IMG",
                          "find": old_name, "replace": new_name})
        rules.append({"label": "PROD_DATA",
                      "find": f'data-product-image="{old_img}"',
                      "replace": f'data-product-image="{new_name}"'})
        rules.append({"label": "PROD_DATA",
                      "find": f'data-product-image="{old_name}"',
                      "replace": f'data-product-image="{new_name}"'})

    # ── 9. Ручные доп. замены (страна/города/имена) ──────────
    for i, pair in enumerate(custom_replacements or [], 1):
        find = (pair or {}).get('find', '')
        replace = (pair or {}).get('replace', '')
        if find and find != replace:
            rules.append({"label": f"CUSTOM_{i}", "find": find, "replace": replace})

    # Убираем дубли и no-op замены
    seen, deduped = set(), []
    for r in rules:
        if r['find'] and r['find'] not in seen and r['find'] != r['replace']:
            seen.add(r['find'])
            deduped.append(r)

    return {
        "_comment":      f"Авто: ({sp}) → {geo_id} ({new_product})",
        "geo_id":        geo_id,
        "product_name":  new_product,
        "_currency":     dc,
        "_price_new":    price_new_str,
        "_price_old":    price_old_str,
        "_country_code": geo_id,
        "translate_to":  "",
        "replacements":  deduped,
        "image_files":   image_map,
    }


# ══════════════════════════════════════════════════════════════
# ИНТЕРФЕЙС
# ══════════════════════════════════════════════════════════════

def ask(prompt, default=''):
    hint = f" {DIM}[{default}]{RESET}" if default else ""
    try:
        val = input(f"  {prompt}{hint}: ").strip()
    except EOFError:
        val = ''
    return val or default


def choose_geo(geos: dict) -> tuple[str, dict]:
    print(f"\n  {BOLD}ЦЕЛЕВОЕ ГЕО:{RESET}\n")
    geo_list = list(geos.keys())
    for i, code in enumerate(geo_list, 1):
        g = geos[code]
        print(f"    {DIM}[{i:2}]{RESET}  {Y}{code}{RESET}  "
              f"{DIM}{g['currency']:5} {g['country_name']}{RESET}")
    print(f"\n    {DIM}[ 0]{RESET}  Ввести вручную\n")

    choice = ask(f"Выбери ГЕО [1-{len(geo_list)}]")
    if choice == '0':
        code = ask("Код ГЕО (напр. BG)").upper()
        cur  = ask("Валюта (напр. BGN)").upper()
        lang = ask("Код языка (напр. BG)").upper()
        cname = ask("Название страны")
        return code, {'currency': cur, 'lang': lang,
                      'lang_html': lang.lower(), 'country_name': cname}
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(geo_list):
            code = geo_list[idx]
            return code, geos[code]
    except (ValueError, IndexError):
        pass
    err("Неверный выбор — выход")
    sys.exit(1)


def ask_images(all_imgs: list[str], geo_id: str, new_product: str, html_text: str = "") -> tuple:
    """Спрашивает какие изображения заменить. Возвращает (image_map, [])."""
    if not all_imgs:
        return {}, []

    print(f"\n  {BOLD}ИЗОБРАЖЕНИЯ В АРХИВЕ:{RESET}\n")
    for i, img in enumerate(all_imgs, 1):
        print(f"    {DIM}[{i:2}]{RESET}  {Y}{img}{RESET}")

    print(f"\n  Введи номера фото которые нужно заменить через запятую.")
    print(f"  {DIM}Например: 1,3  или Enter чтобы пропустить{RESET}")

    raw = ask("Номера фото для замены")
    if not raw:
        return {}, []

    # Список файлов из assets/
    assets_dir = Path(__file__).parent / 'assets'
    asset_files = sorted([
        f.name for f in assets_dir.iterdir()
        if f.is_file() and not f.name.startswith('.')
    ]) if assets_dir.exists() else []

    image_map = {}

    for part in raw.split(','):
        part = part.strip()
        try:
            idx = int(part) - 1
            if 0 <= idx < len(all_imgs):
                old_img = all_imgs[idx]

                if asset_files:
                    print(f"\n  {BOLD}ФАЙЛЫ В ASSETS:{RESET}\n")
                    for j, af in enumerate(asset_files, 1):
                        print(f"    {DIM}[{j:2}]{RESET}  {G}{af}{RESET}")
                    print(f"\n  Выбери номер из assets/ для замены {Y}{old_img}{RESET}")
                    print(f"  {DIM}или введи имя файла вручную{RESET}")
                    choice = ask(f"  [{old_img}]")
                    try:
                        aidx = int(choice) - 1
                        new_img = asset_files[aidx]
                    except (ValueError, IndexError):
                        new_img = choice if choice else old_img
                else:
                    slug = re.sub(r'[^A-Za-z0-9]', '', new_product)
                    ext  = Path(old_img).suffix
                    new_img = ask(
                        f"  Новое имя для {Y}{old_img}{RESET}",
                        f"{slug}_{geo_id}{ext}"
                    )

                image_map[old_img] = new_img
        except ValueError:
            pass

    return image_map, []


def print_found(found: dict, zip_path: str):
    section(f"🔍  СКАНИРОВАНИЕ: {Path(zip_path).name}")
    print(f"\n  {BOLD}ЧТО НАШЁЛ:{RESET}\n")

    def row(label, val, ok_color=Y):
        v = f"{ok_color}{BOLD}{val}{RESET}" if val else f"{R}не найдено{RESET}"
        print(f"    {G}▸{RESET}  {label:<18} {v}")

    # Показываем топ кандидатов продукта
    if found.get('product_candidates'):
        top = found['product_candidates'][:5]
        cands_str = '  '.join(f"{Y}{w}{RESET}{DIM}[{c}x]{RESET}" for w, c in top)
        print(f"    {G}▸{RESET}  {'Продукт (топ):':<18} {cands_str}")
    else:
        row("Продукт:", found['product'])
    row("Цена новая:",  found['price_new_str'])
    row("Цена старая:", found['price_old_str'])
    row("Валюта:",      found['cur_sym'])

    cl = found['country_lang']
    row("data-country:",   ', '.join(cl['data_country']) or None)
    row("data-language:",  ', '.join(cl['data_language']) or None)
    row("input country:",  ', '.join(cl['input_country']) or None)
    row("input language:", ', '.join(cl['input_language']) or None)
    row("lang= (html):",   cl['lang_html'])
    row("exclude_word:",   repr(cl['exclude_word']) if cl['exclude_word'] is not None else None)
    row("Фото прод:",      ', '.join(found['prod_images']) or None)


# ══════════════════════════════════════════════════════════════
# ПУБЛИЧНЫЙ МЕТОД
# ══════════════════════════════════════════════════════════════

def run_scan(zip_path: str) -> str | None:
    """Сканирует оффер, задаёт вопросы, сохраняет конфиг. Возвращает путь к конфигу."""
    geos = load_geos()

    info(f"Читаю {Path(zip_path).name}…")
    text = read_zip_text(zip_path)
    all_imgs = detect_all_images(zip_path)

    cur_sym, price_new_str, price_old_str = detect_prices(text)

    product_candidates = detect_product_candidates(text)
    # Точные строки из data-new-price / data-old-price для виджета
    widget_prices = {}
    for attr, val in re.findall(r'data-(new|old)-price="([^"]+)"', text):
        if attr == 'new':
            widget_prices['widget_price_new'] = val.strip()
        else:
            widget_prices['widget_price_old'] = val.strip()

    found = {
        'product':            detect_product(text),
        'product_candidates': product_candidates,
        'cur_sym':            cur_sym,
        'price_new_str':      price_new_str,
        'price_old_str':      price_old_str,
        'widget_price_new':   widget_prices.get('widget_price_new'),
        'widget_price_old':   widget_prices.get('widget_price_old'),
        'country_lang':       detect_country_lang(text),
        'prod_images':        detect_images(text),
        '_raw_text':          text,
    }

    print_found(found, zip_path)

    # ── Диалог ───────────────────────────────────────────────
    geo_id, geo = choose_geo(geos)

    # Продукт — показываем топ и спрашиваем подтверждение
    print(f"\n  {BOLD}ПРОДУКТ В ЛЕНДЕ:{RESET}\n")
    candidates = found['product_candidates']
    if candidates:
        for i, (word, cnt) in enumerate(candidates[:7], 1):
            marker = f"{G}▸{RESET}" if i == 1 else f" {DIM}{i}{RESET}"
            print(f"    {marker}  {Y}{word}{RESET}  {DIM}[встречается {cnt}x]{RESET}")
    else:
        print(f"    {R}Продукт не определён автоматически{RESET}")

    print()
    src_product = ask(
        "Это название продукта который надо менять?\n  Введи правильное или нажми Enter если верно",
        candidates[0][0] if candidates else ""
    )
    new_product = ask("На что менять (новое название)", src_product)

    # Если пользователь исправил исходное название — обновляем found
    found['product'] = src_product

    dc_sym = CODE_TO_SYM.get(geo['currency'], geo['currency'])
    print(f"\n  {BOLD}ЦЕНЫ ({dc_sym}):{RESET}")
    print(f"  {DIM}Вводи цену как она должна выглядеть на сайте, например: 34.500 CLP или 49 лв.{RESET}")
    price_new = ask(f"Новая цена", f"49 {dc_sym}")
    price_old = ask(f"Старая цена", f"98 {dc_sym}")
    # Если пользователь ввёл только цифру — добавляем символ валюты
    price_new_out = price_new if any(c.isalpha() or c in "₹₺£€₽₴лвзłKčFt" for c in price_new) else f"{price_new} {dc_sym}"
    price_old_out = price_old if any(c.isalpha() or c in "₹₺£€₽₴лвзłKčFt" for c in price_old) else f"{price_old} {dc_sym}"

    # ── Изображения ──────────────────────────────────────────
    image_map, extra_img_rules = ask_images(all_imgs, geo_id, new_product, text)

    # ── Генерация конфига ────────────────────────────────────
    config = build_config(found, geo_id, geo, new_product,
                          price_new_out, price_old_out, image_map)

    # Добавляем правила для полных путей (images/bottle-1.png и т.д.)
    if extra_img_rules:
        config['replacements'].extend(extra_img_rules)

    # ── Превью ───────────────────────────────────────────────
    section("📄  КОНФИГ — ЗАМЕНЫ")
    print()
    for r in config['replacements']:
        lbl = f"{DIM}[{r['label']}]{RESET} " if r.get('label') else ''
        sf = (r['find'][:42] + '…') if len(r['find']) > 42 else r['find']
        sr = (r['replace'][:42] + '…') if len(r['replace']) > 42 else r['replace']
        print(f"    {lbl}{Y}{sf}{RESET}  →  {G}{sr}{RESET}")
    if image_map:
        print(f"\n  {BOLD}ФОТО:{RESET}")
        for old, new in image_map.items():
            print(f"    {Y}{old}{RESET}  →  {G}{new}{RESET}")

    # ── Сохранение ───────────────────────────────────────────
    print()
    stem = Path(zip_path).stem.replace('_offer_archive', '').replace('_offer', '')
    default_path = f"configs/{geo_id}_{stem}.json"

    print(f"  {DIM}Нажми Enter чтобы сохранить как:{RESET} {Y}{default_path}{RESET}")
    raw = ask("Или введи другое имя файла (Enter = ОК)")
    save_path = raw if (raw and raw.endswith('.json')) else default_path

    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    Path(save_path).write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')

    ok(f"Конфиг сохранён → {G}{BOLD}{save_path}{RESET}")

    # ── Предлагаем сразу запустить адаптацию ─────────────────
    print()
    try:
        ans = input(f"  {BOLD}Запустить адаптацию сейчас? [Y/n]: {RESET}").strip().lower()
    except EOFError:
        ans = 'n'

    if ans in ('', 'y', 'yes', 'д', 'да'):
        print()
        import subprocess, sys
        subprocess.run([sys.executable, 'adapt.py', '--offer', zip_path, '--geo', save_path])
    else:
        print(f"\n  Запусти позже:\n  {Y}python adapt.py --offer {zip_path} --geo {save_path}{RESET}\n")

    return save_path