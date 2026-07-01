#!/usr/bin/env python3
"""
inject.py — вставляет нужные скрипты/инпуты в сырой лендинг

Использование:
  python inject.py --file input/site.zip
  python inject.py --file input/site1.zip input/site2.zip

Что вставляет (только если ещё нет):
  1. PHP-шапку перед <!DOCTYPE html>
  2. meta referrer + Counters + Showcases перед </head>
  3. click_data + thx_page сразу после <form
  4. language/country/exclude_word/utm/subid/offer_id перед </form>
  5. Universal widget + Маска формы перед </body>
"""

import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse, re, shutil, tempfile, zipfile
from pathlib import Path
from datetime import datetime

# Цвета — отключаем если терминал не поддерживает ANSI (Windows cmd/bat)
# Цвета — отключаем если Windows cmd (не поддерживает ANSI)
import os as _os
if _os.name != "nt" or "WT_SESSION" in _os.environ or "ANSICON" in _os.environ:
    R="[91m"; G="[92m"; Y="[93m"; B="[94m"
    C="[96m"; DIM="[2m"; RESET="[0m"; BOLD="[1m"
else:
    R=G=Y=B=C=DIM=RESET=BOLD=""
def ok(m):      print(f"  {G}✓{RESET}  {m}")
def warn(m):    print(f"  {Y}!{RESET}  {m}")
def info(m):    print(f"  {B}→{RESET}  {m}")
def err(m):     print(f"  {R}✗{RESET}  {m}")
def skip(m):    print(f"  {DIM}–  {m} (уже есть){RESET}")
def section(t): print(f"\n{C}{BOLD}{'─'*52}\n  {t}\n{'─'*52}{RESET}")
def ask(prompt, default=''):
    hint = f" {DIM}[{default}]{RESET}" if default else ""
    try:
        val = input(f"  {prompt}{hint}: ").strip()
    except EOFError:
        val = ''
    return val or default


# ══════════════════════════════════════════════════════════════
# ВЕРТИКАЛИ
# ══════════════════════════════════════════════════════════════

VERTICALS = {
    "1": ("Потенция",                     "pt "),
    "2": ("Суставы",                      "jo "),
    "3": ("Похудение",                    "wl "),
    "4": ("Диабет",                       "di "),
    "5": ("Давление",                     "hy "),
    "6": ("Зрение",                       "vi "),
    "7": ("Грибок",                       "fu "),
    "8": ("Паразиты",                     "pa "),
    "9": ("Простатит",                    "pr "),
    "10": ("Цистит",                      "cy "),
    "11": ("Омоложение",                  "be "),
    "12": ("Слух",                        "he "),
    "13": ("Варикоз",                     "va "),
    "14": ("Увелечение",                  "en "),
    "15": ("Отбеливание кожи",            "sw "),
    "16": ("Гастрит",                     "gs "),
    "17": ("Менопауза",                   "me "),
    "18": ("Пищеварение",                 "dg "),
}


# ══════════════════════════════════════════════════════════════
# ШАБЛОНЫ БЛОКОВ
# ══════════════════════════════════════════════════════════════

PHP_HEADER = """\
<?php require_once '/var/www/orders_api/src/v2/init.php'; ?>
<?php $clickJson = isset($rawClick) ? getClickData($rawClick) : '{}'; ?>
<?php
if (!isset($rawClick)){
    exit();
}
?>
"""

HEAD_SCRIPTS = """\

<meta name="referrer" content="no-referrer">
<!-- Counters first step START  -->
<script type="text/javascript" src="../../lander/mv/counters/first.min.js"></script>
  <script>
    countersFirstStep({
        window,
        subId: '{subid}',
        tpixid: '{tpixid}',
        ymc: '{ymc}',
        gua: '{gua}',
        pixid: '{pixid}',
        yaMetricaParams: {
            clickmap: true,
            trackLinks: true,
            accurateTrackBounce: true,
            webvisor: true,
        }
    })
  </script>
<!-- Counters first step END  -->

<!-- Backfix START  -->
<script
    data-click-data='<?= $clickJson ?>'
    id="backfix"
    src="{_from_file:backfix_file_path}"
    type="module"
></script>
<!-- Backfix END  -->
"""

FORM_TOP = """\
<input type="hidden" name="click_data" value="<?=$clickJson?>">
<input type="hidden" name="thx_page" value="main">
"""

def form_bottom(country: str, language: str, exclude_word: str) -> str:
    return f"""\
    <input type="hidden" name="language" value="{language}">
    <input type="hidden" name="country" value="{country}">
    <input type="hidden" name="exclude_word" value="{exclude_word}">
    <input type="hidden" name="utm_campaign" value="{{offer_id}}">
    <input type="hidden" name="subid" value="{{subid}}">
    <input type="hidden" name="offer_id" value="{{offer_id}}">
"""

def body_end(country: str, language: str,
             price_new: str, price_old: str, prod_img: str,
             exclude_word: str = '') -> str:
    country_lc  = country.lower()
    language_lc = language.lower()
    return f"""\

<!-- Universal widget combined START  -->
<div
        data-click-data="<?= $clickJson ?>"
        data-lead-endpoint="./"
        data-thx-page="main"
        data-exclude-word="{exclude_word}"
        data-pixid="{{pixid}}"
        data-gua="{{gua}}"
        data-ymc="{{ymc}}"
        data-offer-id="{{offer_id}}"
        data-subid="{{subid}}"
        data-country="{country_lc}"
        data-language="{language_lc}"
        data-widget-variant="lead-generator"
        data-old-price="{price_old}"
        data-new-price="{price_new}"
        data-product-image="{prod_img}"
        id="universal-widget-combined"
></div>
<script type="module" src="{{_from_file:widget_2in1_path}}"></script>
<!-- Universal widget combined END  -->


<!--Маска формы заказа START-->
<script
        id="form_mask"
        type="module"
        src="{{_from_file:form_mask_file_path}}"
        data-country="{country_lc}"
>
</script>
<!--Маска формы заказа END-->
"""



# ══════════════════════════════════════════════════════════════
# api.php — шаблон
# ══════════════════════════════════════════════════════════════

API_PHP = """<?php
define('PATH_KEITARO_ORDERS_API_SRC','../../../orders_api/src/');
require_once(PATH_KEITARO_ORDERS_API_SRC.'send_order.php');

$protocol = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https://' : 'http://';
$currentDomain = $_SERVER['HTTP_HOST'];

$language = isset($_POST['language']) ? $_POST['language'] : 'EN';
$country = isset($_POST['country']) ? $_POST['country'] : 'US';

$utm_source = isset($_POST['utm_source']) ? $_POST['utm_source'] : '';
$utm_medium = isset($_POST['utm_medium']) ? $_POST['utm_medium'] : '';
$utm_campaign = isset($_POST['utm_campaign']) ? $_POST['utm_campaign'] : '';
$utm_term = isset($_POST['utm_term']) ? $_POST['utm_term'] : '';
$utm_content = isset($_POST['utm_content']) ? $_POST['utm_content'] : '';

$exclude_word = isset($_POST['exclude_word']) ? $_POST['exclude_word'] : '';

$params = [
    'language' => $language,
    'country' => $country
];

if (!empty($utm_source)) $params['utm_source'] = $utm_source;
if (!empty($utm_medium)) $params['utm_medium'] = $utm_medium;
if (!empty($utm_campaign)) $params['utm_campaign'] = $utm_campaign;
if (!empty($utm_term)) $params['utm_term'] = $utm_term;
if (!empty($utm_content)) $params['utm_content'] = $utm_content;
if (!empty($exclude_word)) $params['exclude_word'] = $exclude_word;

$thxPage = $protocol . $currentDomain . '/thx-page?' . http_build_query($params);
$dimensionName = 'default';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    exit('Invalid request');
}

sendOrder($dimensionName, $thxPage, $_POST);
?>"""

# ══════════════════════════════════════════════════════════════
# ПРОВЕРКА — что уже есть в файле
# ══════════════════════════════════════════════════════════════

def check_present(html: str) -> dict:
    return {
        'php_header':    'orders_api/src/v2/init.php' in html,
        'counters':      '<!-- Counters first step' in html,
        'backfix':       '<!-- Backfix' in html,
        'click_data':    'name="click_data"' in html,
        'thx_page':      'name="thx_page"' in html,
        'inp_language':  'name="language"' in html,
        'inp_country':   'name="country"' in html,
        'inp_exclude':   'name="exclude_word"' in html,
        'widget':        ('<!-- Universal widget combined' in html or 'widget_2in1_path' in html or 'id="universal-widget-combined"' in html),
        'form_mask':     '<!--Маска формы заказа' in html,
    }


def print_check(checks: dict):
    labels = {
        'php_header':   'PHP шапка',
        'counters':     'Counters first step',
        'backfix':      'Backfix',
        'click_data':   'click_data input',
        'thx_page':     'thx_page input',
        'inp_language': 'language input',
        'inp_country':  'country input',
        'inp_exclude':  'exclude_word input',
        'widget':       'Universal widget',
        'form_mask':    'Маска формы',
    }
    print(f"\n  {BOLD}ЧТО НАШЁЛ В ФАЙЛЕ:{RESET}\n")
    for key, label in labels.items():
        mark = f"{G}✓ есть{RESET}" if checks[key] else f"{R}✗ нет{RESET}"
        print(f"    {mark}  {label}")


# ══════════════════════════════════════════════════════════════
# ВСТАВКА
# ══════════════════════════════════════════════════════════════

def inject_html(html: str, checks: dict, params: dict) -> tuple[str, list]:
    """
    Вставляет недостающие блоки. Возвращает (новый html, список что добавлено).
    """
    added = []
    country    = params['country']
    language   = params['language']
    exclude    = params['exclude_word']
    price_new  = params['price_new']
    price_old  = params['price_old']
    prod_img   = params['prod_img']
    custom_replacements = params.get('custom_replacements', [])

    # ── 0. Проверяем и добавляем базовую структуру HTML ──────
    low = html.lower()
    if '<html' not in low:
        html = '<html>\n' + html + '\n</html>'
        added.append('<html> тег')
        low = html.lower()
    elif '</html>' not in low:
        html = html + '\n</html>'
        added.append('</html> тег')
        low = html.lower()

    if '<head>' not in low and '<head ' not in low:
        body_pos = low.find('<body')
        if body_pos != -1:
            html = html[:body_pos] + '<head>\n</head>\n' + html[body_pos:]
        else:
            html_tag_end = low.find('>') + 1
            html = html[:html_tag_end] + '\n<head>\n</head>' + html[html_tag_end:]
        added.append('<head> тег')
        low = html.lower()
    elif '</head>' not in low:
        body_pos = low.find('<body')
        if body_pos != -1:
            html = html[:body_pos] + '</head>\n' + html[body_pos:]
            added.append('</head> тег')
            low = html.lower()

    if '<body' not in low:
        head_end = low.find('</head>') + len('</head>')
        html = html[:head_end] + '\n<body>' + html[head_end:]
        if '</body>' not in html.lower():
            html = html + '\n</body>'
        added.append('<body> тег')
        low = html.lower()
    elif '</body>' not in low:
        html = html + '\n</body>'
        added.append('</body> тег')
        low = html.lower()

    # ── 1. PHP шапка перед <!DOCTYPE ─────────────────────────
    if not checks['php_header']:
        doctype_pos = html.lower().find('<!doctype')
        if doctype_pos != -1:
            html = html[:doctype_pos] + PHP_HEADER + html[doctype_pos:]
            added.append('PHP шапка')

    # ── 2. meta referrer + Counters + Backfix перед </head> ─
    if not checks['counters'] or not checks['backfix']:
        close_head = html.lower().rfind('</head>')
        if close_head != -1:
            html = html[:close_head] + HEAD_SCRIPTS + html[close_head:]
            added.append('Counters + Backfix')

    # ── 3-5. Обрабатываем ВСЕ формы ─────────────────────────
    # Разбиваем документ по парам <form>...</form> и обрабатываем каждую
    def process_all_forms(html: str) -> tuple[str, list]:
        form_added = []
        result = []
        pos = 0
        html_lower = html.lower()

        while True:
            # Ищем следующий открывающий тег формы
            form_open = re.search(r'<form\b[^>]*>', html[pos:], re.IGNORECASE)
            if not form_open:
                result.append(html[pos:])
                break

            abs_open_start = pos + form_open.start()
            abs_open_end   = pos + form_open.end()

            # Ищем закрывающий тег </form> после открывающего
            close_pos = html_lower.find('</form>', abs_open_end)
            if close_pos == -1:
                result.append(html[pos:])
                break

            # Часть до формы
            result.append(html[pos:abs_open_start])

            # Открывающий тег — фиксируем action=""
            open_tag = html[abs_open_start:abs_open_end]
            open_tag = re.sub(r'action=["\'][^"\']*["\']', 'action=""', open_tag)
            if 'action=' not in open_tag:
                open_tag = open_tag.replace('<form', '<form action=""', 1)
            result.append(open_tag)

            # Тело формы
            form_body = html[abs_open_end:close_pos]

            # Добавляем click_data + thx_page в начало если нет
            if 'name="click_data"' not in form_body and 'name="thx_page"' not in form_body:
                form_body = '\n' + FORM_TOP + form_body
                if 'click_data + thx_page' not in form_added:
                    form_added.append('click_data + thx_page')

            # Добавляем language/country/exclude перед </form> если нет
            if ('name="language"' not in form_body
                    or 'name="country"' not in form_body
                    or 'name="exclude_word"' not in form_body):
                form_body = form_body + form_bottom(country, language, exclude)
                if 'language / country / exclude_word' not in form_added:
                    form_added.append('language / country / exclude_word / utm / subid')

            result.append(form_body)
            result.append('</form>')

            pos = close_pos + len('</form>')

        return ''.join(result), form_added

    new_html, form_added = process_all_forms(html)
    if new_html != html:
        added.extend(form_added)
        html = new_html

    # ── 7. name/type/required на инпуты имени и телефона ─────
    PHONE_NAMES = {'phone','tel','telephone','telefon','telefono','телефон','mobile'}
    NAME_NAMES  = {'name','fio','fullname','fname','имя','nom','nombre','nome','isim'}

    def patch_input(tag: str, fix_name: str, fix_type: str) -> str:
        """Ставит нужные name, type, required в тег инпута."""
        # name
        tag = re.sub(r'name=["\'][^"\']*["\']', f'name="{fix_name}"', tag, flags=re.IGNORECASE)
        if 'name=' not in tag:
            tag = tag.rstrip('/>').rstrip() + f' name="{fix_name}">'
        # type
        tag = re.sub(r'type=["\'][^"\']*["\']', f'type="{fix_type}"', tag, flags=re.IGNORECASE)
        if 'type=' not in tag:
            tag = tag.rstrip('/>').rstrip() + f' type="{fix_type}">'
        # required
        if 'required' not in tag:
            tag = tag.rstrip('/>').rstrip() + ' required>'
        return tag

    def fix_form_inputs(html: str) -> tuple[str, bool]:
        changed = False

        def replacer(m):
            nonlocal changed
            tag = m.group(0)
            nm = re.search(r'name=["\']([^"\']*)["\']', tag, re.IGNORECASE)
            tp = re.search(r'type=["\']([^"\']*)["\']', tag, re.IGNORECASE)
            name_val = nm.group(1).lower().strip() if nm else ''
            type_val = tp.group(1).lower().strip() if tp else ''

            if type_val == 'hidden':
                return tag
            if name_val in PHONE_NAMES or type_val == 'tel':
                new_tag = patch_input(tag, 'phone', 'tel')
            elif name_val in NAME_NAMES or (name_val and name_val not in PHONE_NAMES):
                new_tag = patch_input(tag, 'name', 'text')
            else:
                return tag

            if new_tag != tag:
                changed = True
            return new_tag

        html = re.sub(r'<input\b[^>]*/?>'  , replacer, html, flags=re.IGNORECASE)
        return html, changed

    html, inp_changed = fix_form_inputs(html)
    if inp_changed and 'input attrs fixed' not in added:
        added.append('input attrs fixed')


    # ── 6. Universal widget + Маска перед </body> ─────────────
    if not checks['widget'] or not checks['form_mask']:
        close_body = html.lower().rfind('</body>')
        if close_body != -1:
            html = html[:close_body] + body_end(country, language,
                                                price_new, price_old, prod_img,
                                                exclude) + html[close_body:]
            added.append('Universal widget + Маска формы')



    # ── 7b. Заполняем пустые price-спаны статичными ценами ──────
    # Офферы где цены заполнял JS (countrieslist.js и подобные):
    # спаны имеют классы типа price_main / price_old / js_new_price / js_old_price
    # и стоят пустыми после удаления JS-скрипта.
    # Ищем по подстрокам в class= — не точное совпадение, чтобы покрыть
    # price__new-value price_main, new_price js_new_price price_main и т.п.
    PRICE_NEW_MARKERS = ('price_main', 'js_new_price', 'price__new-value', 'new_price')
    PRICE_OLD_MARKERS = ('price_old',  'js_old_price', 'price__old-value', 'old_price')

    def fill_empty_price_span(html: str, markers: tuple, value: str) -> tuple[str, int]:
        """Заполняет пустые <span class="...MARKER..."></span> нужным значением."""
        count = 0
        def replacer(m: re.Match) -> str:
            nonlocal count
            tag_classes = m.group(1)
            content     = m.group(2)
            # Только если content пустой или только пробелы
            if content.strip():
                return m.group(0)
            # Только если class содержит один из маркеров
            if not any(marker in tag_classes for marker in markers):
                return m.group(0)
            count += 1
            return f'<span class="{tag_classes}">{value}</span>'
        html = re.sub(
            r'<span\s+class="([^"]*)">([\s]*)</span>',
            replacer, html, flags=re.IGNORECASE
        )
        return html, count

    # Только для Shakes-офферов где JS заполнял цены из countrieslist
    # В остальных офферах пустые спаны могут быть намеренными
    HAS_JS_PRICES = any(kw in html for kw in (
        'countryList', 'countrieslist', 'country_list[', 'lCountries',
    ))
    if HAS_JS_PRICES and price_new:
        html, n_new = fill_empty_price_span(html, PRICE_NEW_MARKERS, price_new)
        if n_new:
            added.append(f'цена новая в спаны ({n_new}x)')
    if HAS_JS_PRICES and price_old:
        html, n_old = fill_empty_price_span(html, PRICE_OLD_MARKERS, price_old)
        if n_old:
            added.append(f'цена старая в спаны ({n_old}x)')

    # ── 8. Дополнительные строковые замены (страна/города/имена) ──
    custom_done = 0
    for pair in custom_replacements:
        find = pair.get('find', '')
        repl = pair.get('replace', '')
        if not find or find == repl:
            continue
        if find in html:
            html = html.replace(find, repl)
            custom_done += 1

    if custom_done:
        added.append(f'custom replacements: {custom_done}')

    return html, added


# ══════════════════════════════════════════════════════════════
# ДИАЛОГ
# ══════════════════════════════════════════════════════════════

def ask_params(checks: dict) -> dict | None:
    """Спрашивает только то что нужно для отсутствующих блоков."""

    needs_geo     = not (checks['inp_country'] and checks['inp_language']
                         and checks['widget'] and checks['form_mask'])
    needs_vertical = True  # всегда спрашиваем вертикаль — нужна для виджета
    needs_prices  = not checks['widget']
    needs_img     = not checks['widget']

    if not any([needs_geo, needs_vertical, needs_prices, needs_img,
                not checks['php_header'], not checks['counters'],
                not checks['click_data'], not checks['backfix']]):
        ok("Все блоки уже присутствуют — ничего добавлять не нужно!")
        return None

    params = {}

    # ── ГЕО и язык ───────────────────────────────────────────
    if needs_geo:
        from scanner import load_geos
        geos = load_geos()
        geo_list = list(geos.keys())
        print(f"\n  {BOLD}ГЕО И ЯЗЫК:{RESET}\n")
        for i, code in enumerate(geo_list, 1):
            g = geos[code]
            print(f"    {DIM}[{i:2}]{RESET}  {Y}{code}{RESET}  "
                  f"{DIM}{g['currency']:5} {g['country_name']}{RESET}")
        print(f"\n    {DIM}[ 0]{RESET}  Ввести вручную\n")

        choice = ask(f"Выбери ГЕО [1-{len(geo_list)}]")
        if choice == '0':
            params['country']  = ask("Код страны (напр. IN)").upper()
            params['language'] = ask("Код языка (напр. HI)").upper()
        else:
            try:
                idx = int(choice) - 1
                code = geo_list[idx]
                params['country']  = code
                params['language'] = geos[code]['lang']
            except (ValueError, IndexError):
                err("Неверный выбор")
                return None
    else:
        params['country'] = params['language'] = ''

    # ── Вертикаль ─────────────────────────────────────────────
    if needs_vertical:
        print(f"\n  {BOLD}ВЕРТИКАЛЬ (для exclude_word):{RESET}\n")
        for k, (label, _) in VERTICALS.items():
            print(f"    {DIM}[{k}]{RESET}  {label}")
        print(f"    {DIM}[ 0]{RESET}  Ввести вручную")
        v = ask(f"\n  Выбери вертикаль [1-{len(VERTICALS)}]", "1")
        if v == '0':
            params['exclude_word'] = ask("Введи exclude_word вручную (напр. 'pt ')")
        else:
            params['exclude_word'] = VERTICALS.get(v, VERTICALS['1'])[1]
    else:
        params['exclude_word'] = ''

    # ── Цены ──────────────────────────────────────────────────
    if needs_prices:
        print(f"\n  {BOLD}ЦЕНЫ ДЛЯ ВИДЖЕТА:{RESET}")
        params['price_new'] = ask("Новая цена (напр. 2499 ₹)")
        params['price_old'] = ask("Старая цена (напр. 4998 ₹)")
    else:
        params['price_new'] = params['price_old'] = ''

    # ── Фото продукта ─────────────────────────────────────────
    if needs_img:
        from pathlib import Path as _Path
        assets_dir = _Path(__file__).parent / 'assets'
        asset_files = sorted([
            f.name for f in assets_dir.iterdir()
            if f.is_file() and not f.name.startswith('.')
        ]) if assets_dir.exists() else []

        if asset_files:
            print(f"\n  {BOLD}ФОТО ПРОДУКТА (из assets/):{RESET}\n")
            for j, af in enumerate(asset_files, 1):
                print(f"    {DIM}[{j:2}]{RESET}  {G}{af}{RESET}")
            print()
            choice = ask("Выбери номер или введи имя вручную", "1")
            try:
                aidx = int(choice) - 1
                params['prod_img'] = asset_files[aidx]
            except (ValueError, IndexError):
                params['prod_img'] = choice if choice else 'product.webp'
        else:
            print(f"\n  {BOLD}ФОТО ПРОДУКТА:{RESET}")
            params['prod_img'] = ask("Имя файла (напр. product.png)", "product.webp")
    else:
        params['prod_img'] = 'product.webp'

    return params


# ══════════════════════════════════════════════════════════════
# ОБРАБОТКА ZIP
# ══════════════════════════════════════════════════════════════

PHP_EXT  = {'.php', '.html', '.htm'}

def process_zip(zip_path: str, params: dict) -> str:
    """Обрабатывает архив, возвращает путь к результату."""

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / 'src'
        dst = Path(tmp) / 'dst'
        dst.mkdir()

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(src)

        total_added = []

        for fp in sorted(src.rglob('*')):
            if not fp.is_file():
                continue
            rel     = fp.relative_to(src)
            dst_fp  = dst / rel
            dst_fp.parent.mkdir(parents=True, exist_ok=True)

            # index.html → index.php
            if fp.name.lower() == 'index.html':
                dst_fp = dst_fp.parent / 'index.php'
                ok(f"index.html -> index.php")

            if fp.name.lower() == 'api.php':
                shutil.copy2(fp, dst_fp)
            elif fp.suffix.lower() in PHP_EXT or fp.name.lower() == 'index.php':
                html = fp.read_text(encoding='utf-8', errors='replace')
                checks = check_present(html)

                new_html, added = inject_html(html, checks, params)
                dst_fp.write_text(new_html, encoding='utf-8')

                if added:
                    ok(f"{rel}  {DIM}+{', '.join(added)}{RESET}")
                    total_added.extend(added)
                else:
                    info(f"{rel}  {DIM}без изменений{RESET}")
            else:
                shutil.copy2(fp, dst_fp)

        # ── Создаём api.php в корне если его нет ────────────────
        api_dst = dst / 'api.php'
        existed = api_dst.exists()
        api_dst.write_text(API_PHP, encoding='utf-8')
        ok(f"api.php  {DIM}({'заменён' if existed else 'создан'}){RESET}")

        # Упаковываем
        Path('output').mkdir(exist_ok=True)
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        stem     = Path(zip_path).stem
        out_path = Path('output') / f"{stem}__injected__{ts}.zip"

        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fp in dst.rglob('*'):
                if fp.is_file():
                    zf.write(fp, fp.relative_to(dst))

        return str(out_path)


# ══════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════

def main():
    print(f"\n{B}{BOLD}╔══════════════════════════════════════════════════╗")
    print(f"║        💉  INJECT  v1.0                          ║")
    print(f"║   Вставка скриптов в сырой лендинг               ║")
    print(f"╚══════════════════════════════════════════════════╝{RESET}\n")

    p = argparse.ArgumentParser(description='Inject scripts into raw landing pages')
    p.add_argument('--file', nargs='+', metavar='ZIP', required=True,
                   help='Один или несколько ZIP-архивов')
    args = p.parse_args()

    for zip_path in args.file:
        if not Path(zip_path).exists():
            err(f"Файл не найден: {zip_path}")
            continue

        section(f"📂  {Path(zip_path).name}")

        # Читаем первый PHP/HTML файл для предварительной проверки
        sample_html = ''
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                if Path(name).suffix.lower() in PHP_EXT:
                    try:
                        sample_html = zf.read(name).decode('utf-8', errors='replace')
                        break
                    except Exception:
                        continue

        if not sample_html:
            warn("Не найдено PHP/HTML файлов в архиве")
            continue

        checks = check_present(sample_html)
        print_check(checks)

        # Диалог
        params = ask_params(checks)
        if params is None:
            continue

        # Обработка
        section("⚙️   ОБРАБОТКА")
        out_path = process_zip(zip_path, params)

        section("📦  РЕЗУЛЬТАТ")
        size_kb = Path(out_path).stat().st_size // 1024
        ok(f"Готово!  →  {G}{BOLD}{out_path}{RESET}  ({size_kb} KB)")

    print(f"\n{G}{BOLD}  ✅  Готово!{RESET}\n")


if __name__ == '__main__':
    main()