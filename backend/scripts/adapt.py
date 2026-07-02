#!/usr/bin/env python3
"""
OFFER ADAPTER v3.0

Сканирование:
  python adapt.py --scan input/offer.zip
  python adapt.py --scan input/offer1.zip input/offer2.zip

Адаптация:
  python adapt.py --offer input/offer.zip --geo configs/BG.json
  python adapt.py --offer input/offer.zip --geo configs/BG.json --dry-run --verbose
"""

import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse, json, re, shutil, tempfile, zipfile
from pathlib import Path
from datetime import datetime

# Умная замена цен — понимает форматирование числа и не дублирует валюту
try:
    from scripts.price_replacer import apply_smart_prices
except ImportError:
    from price_replacer import apply_smart_prices  # при запуске напрямую из scripts/

# Цвета — отключаем если Windows cmd (не поддерживает ANSI)
import os as _os
if _os.name != 'nt' or 'WT_SESSION' in _os.environ or 'ANSICON' in _os.environ:
    R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
    C="\033[96m"; DIM="\033[2m"; RESET="\033[0m"; BOLD="\033[1m"
else:
    R=G=Y=B=C=DIM=RESET=BOLD=""

# PHP файлы обрабатываем — но PHP блоки внутри защищены
TEXT_EXT  = {'.php', '.html', '.htm', '.css', '.js', '.txt', '.json', '.xml'}
SKIP_EXT  = {'.backup', '.bak', '.orig'}  # эти файлы не копируем в output
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}

def banner():
    print(f"\n{B}{BOLD}╔══════════════════════════════════════════════════╗")
    print(f"║        🚀  OFFER ADAPTER  v3.0                   ║")
    print(f"╚══════════════════════════════════════════════════╝{RESET}\n")

def section(t): print(f"\n{C}{BOLD}{'─'*52}\n  {t}\n{'─'*52}{RESET}")
def ok(m):   print(f"  {G}✓{RESET}  {m}")
def warn(m): print(f"  {Y}!{RESET}  {m}")
def info(m): print(f"  {B}→{RESET}  {m}")
def err(m):  print(f"  {R}✗{RESET}  {m}")


# ══════════════════════════════════════════════════════════════
# ЗАЩИТА PHP БЛОКОВ
# ══════════════════════════════════════════════════════════════

def protect_php(text: str) -> tuple[str, dict]:
    """
    Заменяет PHP блоки <?php...?> и <?=...?> на плейсхолдеры.
    Использует find() вместо regex для скорости на больших файлах.
    """
    placeholders = {}
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
            if e == -1:
                result.append(text[pos:s])
                key = f'\x00PHP{counter}\x00'
                placeholders[key] = text[s:]
                result.append(key)
                counter += 1
                break
            else:
                e += 2
                result.append(text[pos:s])
                key = f'\x00PHP{counter}\x00'
                placeholders[key] = text[s:e]
                result.append(key)
                counter += 1
                pos = e
        else:
            result.append(text[pos:s+2])
            pos = s + 2

    return ''.join(result), placeholders


def restore_php(text: str, placeholders: dict) -> str:
    """Восстанавливает PHP блоки из плейсхолдеров."""
    for key, original in placeholders.items():
        text = text.replace(key, original)
    return text


def protect_showcases(text: str) -> tuple[str, str]:
    """
    Защищает блок Showcases v2 от замены data-country.
    Возвращает (текст с плейсхолдером, оригинальный блок или '').
    """
    START = '<!-- Showcases v2 START'
    END   = '<!-- Showcases v2 END'
    s = text.find(START)
    if s == -1:
        return text, ''
    e = text.find('\n', text.find(END, s))
    if e == -1:
        e = len(text)
    else:
        e += 1
    block = text[s:e]
    return text[:s] + '\x00SHOWCASES\x00' + text[e:], block


def restore_showcases(text: str, block: str) -> str:
    if block:
        text = text.replace('\x00SHOWCASES\x00', block)
    return text


# ══════════════════════════════════════════════════════════════
# ПРИМЕНЕНИЕ ЗАМЕН
# ══════════════════════════════════════════════════════════════

def apply_replacements(text: str, rules: list) -> tuple[str, int]:
    """
    Применяет текстовые замены из конфига.
    PHP блоки и блок Showcases защищены — не трогаются.
    Правила ЦЕНА_* и WIDGET_ЦЕНА_* пропускаются — их обрабатывает apply_smart_prices.
    """
    text, php_blocks = protect_php(text)
    text, showcase   = protect_showcases(text)

    total = 0
    for r in rules:
        # ЦЕНА_* — обрабатываются умно в apply_smart_prices (smart_replace_price)
        # WIDGET_ЦЕНА_* — обрабатываются здесь через обычный str.replace (data-атрибуты)
        if r.get('label', '').startswith('ЦЕНА_'):
            continue
        find, replace = r['find'], r['replace']
        if r.get('regex'):
            new_text, n = re.subn(find, replace, text)
        else:
            n = text.count(find)
            new_text = text.replace(find, replace)
        if n:
            total += n
            text = new_text

    text = restore_showcases(text, showcase)
    text = restore_php(text, php_blocks)
    return text, total


def apply_split_prices(text: str, rules: list) -> tuple[str, int]:
    """
    Меняет цифру цены когда она стоит отдельно в теге
    (Elementor: <span>99</span> <span>zł</span>).
    Оставлен как fallback — apply_smart_prices покрывает большинство случаев.
    """
    pairs = []
    for r in rules:
        fm = re.match(r'^(\d+)\s', r['find'])
        rm = re.match(r'^(\d+)\s', r['replace'])
        if fm and rm and fm.group(1) != rm.group(1):
            pairs.append((fm.group(1), rm.group(1)))

    if not pairs:
        return text, 0

    text, php_blocks = protect_php(text)
    total = 0
    for old_n, new_n in pairs:
        pat = r'(>\s*)' + re.escape(old_n) + r'(\s*<)'
        new_text, n = re.subn(pat, r'\g<1>' + new_n + r'\g<2>', text)
        if n:
            total += n
            text = new_text
    text = restore_php(text, php_blocks)
    return text, total


def _strip_path_prefix(text: str, filename: str) -> tuple[str, int]:
    """
    Убирает любой префикс пути перед именем файла везде в тексте.
    assets/img/Ult.webp → Ult.webp
    """
    PATH_STOP = {'"', "'", ' ', '\n', '\t', '>', '<', '(', ')', '='}
    result = []
    pos = 0
    n = len(text)
    flen = len(filename)
    count = 0
    while pos < n:
        idx = text.find(filename, pos)
        if idx == -1:
            result.append(text[pos:])
            break
        path_start = idx
        while path_start > 0 and text[path_start - 1] not in PATH_STOP:
            path_start -= 1
        if path_start < idx:
            result.append(text[pos:path_start])
            result.append(filename)
            count += 1
        else:
            result.append(text[pos:idx + flen])
        pos = idx + flen
    if count:
        text = ''.join(result)
    return text, count


def apply_image_rules(text: str, image_map: dict) -> tuple[str, int]:
    """
    Меняет все упоминания старого имени файла на новое,
    убирая любой префикс пути.
    images/bottle-1.png  → tov.png
    ./img/bottle-1.png   → tov.png
    bottle-1.png         → tov.png
    """
    PATH_STOP = {'"', "'", ' ', '\n', '\t', '>', '<', '(', ')', '='}
    total = 0

    for old_img, new_img in image_map.items():
        if old_img not in text:
            continue
        result = []
        pos = 0
        n = len(text)
        old_len = len(old_img)
        count = 0
        while pos < n:
            idx = text.find(old_img, pos)
            if idx == -1:
                result.append(text[pos:])
                break
            path_start = idx
            while path_start > 0 and text[path_start - 1] not in PATH_STOP:
                path_start -= 1
            result.append(text[pos:path_start])
            result.append(new_img)
            count += 1
            pos = idx + old_len
        if count:
            text = ''.join(result)
            total += count

    for new_img in set(image_map.values()):
        text, n = _strip_path_prefix(text, new_img)
        total += n

    return text, total


# ══════════════════════════════════════════════════════════════
# ОТЧЁТ ПЕРЕД АДАПТАЦИЕЙ
# ══════════════════════════════════════════════════════════════

def scan_counts(src_root: Path, rules: list) -> dict:
    counts = {r['find']: 0 for r in rules}
    for fp in src_root.rglob('*'):
        if fp.is_file() and fp.suffix.lower() in TEXT_EXT:
            try:
                text = fp.read_text(encoding='utf-8', errors='replace')
            except Exception:
                continue
            clean, _ = protect_php(text)
            for r in rules:
                if r.get('regex'):
                    counts[r['find']] += len(re.findall(r['find'], clean))
                else:
                    counts[r['find']] += clean.count(r['find'])
    return counts


def print_report(counts: dict, rules: list, image_map: dict):
    section("📋  ЧТО БУДЕТ ИЗМЕНЕНО")
    print(f"\n  {BOLD}ТЕКСТОВЫЕ ЗАМЕНЫ:{RESET}")
    any_found = False
    for r in rules:
        lbl = f"{DIM}[{r.get('label','')}]{RESET} " if r.get('label') else ''
        cnt = counts.get(r['find'], 0)
        sf  = (r['find'][:40]+'…') if len(r['find'])>40 else r['find']
        sr  = (r['replace'][:40]+'…') if len(r['replace'])>40 else r['replace']
        if cnt:
            any_found = True
            print(f"    {G}✓{RESET} {lbl}{DIM}[{cnt}x]{RESET}  {Y}{sf}{RESET}  →  {G}{sr}{RESET}")
        else:
            print(f"    {R}✗{RESET} {lbl}{DIM}[не найдено]{RESET}  {DIM}{sf}{RESET}")
    if not any_found:
        warn("Ни одна замена не нашла совпадений — проверь конфиг!")
    if image_map:
        print(f"\n  {BOLD}ИЗОБРАЖЕНИЯ:{RESET}")
        for old, new in image_map.items():
            asset = Path('assets') / new
            mark  = G if asset.exists() else R
            note  = '' if asset.exists() else f"  {R}(нет в assets/!){RESET}"
            print(f"    {mark}▸{RESET}  {Y}{old}{RESET}  →  {G}{new}{RESET}{note}")


# ══════════════════════════════════════════════════════════════
# ОБРАБОТКА ФАЙЛОВ
# ══════════════════════════════════════════════════════════════

def process_offer(src_root: Path, dst_root: Path, config: dict, verbose: bool,
                  extra_asset_dirs: list | None = None) -> dict:
    rules     = config.get('replacements', [])
    image_map = config.get('image_files', {})
    geo_id    = config.get('geo_id')
    assets    = Path('assets')
    # Доп. директории замен (изолированные по задаче) ищутся ПЕРЕД глобальной
    # assets/ — task-замена с тем же именем имеет приоритет.
    search_dirs = [Path(d) for d in (extra_asset_dirs or [])] + [assets]

    stats = {'files': 0, 'replacements': 0, 'images': 0, 'missing': []}

    def _find_asset(name: str) -> Path | None:
        for d in search_dirs:
            p = d / name
            if p.exists():
                return p
        return None

    # Сначала копируем новые медиа (фото/видео) из assets в корень dst
    for old_img, new_img in image_map.items():
        asset_src = _find_asset(new_img)
        if asset_src is not None:
            dst_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset_src, dst_root / new_img)
            stats['images'] += 1
            if verbose:
                ok(f"МЕДИА  {new_img}  ({asset_src.parent} → корень)")
        else:
            stats['missing'].append(new_img)
            warn(f"{new_img} не найден в assets/")

    # Обрабатываем все файлы архива
    for src in sorted(src_root.rglob('*')):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        dst = dst_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        ext = src.suffix.lower()

        if ext in SKIP_EXT or any(str(rel).endswith(s) for s in ('.backup', '.bak', '.orig')):
            continue

        if ext in TEXT_EXT:
            try:
                text = src.read_text(encoding='utf-8', errors='replace')
            except Exception as e:
                warn(f"Не удалось прочитать {rel}: {e}")
                shutil.copy2(src, dst)
                continue

            # Parser v2: DOM-aware замена для HTML/PHP
            if ext in {'.html', '.htm', '.php'}:
                from scripts.parser_v2 import apply_dom_replacements
                text, n = apply_dom_replacements(text, rules, image_map)
            elif ext == '.css':
                # В CSS нет цен и текста продукта — слепые замены ломали вёрстку
                # (цена «50» превращала translate(-50%,-50%) в translate(-229%,-229%)).
                # Меняем только пути/имена картинок (background: url(...)).
                text, n = apply_image_rules(text, image_map)
            elif ext in {'.js', '.json'}:
                # В JS/JSON замена ГОЛЫХ чисел цены ломает код: ключи палитр
                # ({50:"#f3e5f5"} → {229:...}), hex-цвета (#4caf50 → #4caf229),
                # таймауты. Цены виджетов приходят из data-атрибутов HTML
                # (parser_v2), поэтому умные цены здесь выключены; текстовые
                # правила (продукт и т.п.) и картинки — остаются.
                text, n1 = apply_replacements(text, rules)
                text, n3 = apply_image_rules(text, image_map)
                n = n1 + n3
            else:
                text, n1 = apply_replacements(text, rules)
                text, n2 = apply_smart_prices(text, rules, geo_id)
                text, n3 = apply_image_rules(text, image_map)
                n = n1 + n2 + n3

            dst.write_text(text, encoding='utf-8')
            if n:
                stats['files'] += 1
                stats['replacements'] += n
                if verbose:
                    ok(f"{rel}  {DIM}({n} замен){RESET}")

        elif ext in IMAGE_EXT:
            fname = src.name
            if fname in image_map:
                pass  # старый файл не копируем — новый уже лежит в корне
            else:
                shutil.copy2(src, dst)
        else:
            shutil.copy2(src, dst)

    # Итоговый ленд должен быть PHP-вариантом: переименовываем .html/.htm → .php
    # и правим внутренние ссылки на эти страницы (форма экшенов, <a href> и т.п.).
    htmlized = htmlize_to_php(dst_root, verbose)
    stats['htmlized'] = htmlized

    # Обвязка под наше API: гарантируем эталонный api.php в корне ленда.
    stats['api_php'] = ensure_api_php(dst_root, verbose)

    return stats


# Эталонное содержимое api.php (раздел 5.1 AGENT.md, для обычных и VSL лендов).
# Содержимое фиксированное — приёмник формы Keitaro orders_api.
API_PHP_TEMPLATE = """\
<?php
define('PATH_KEITARO_ORDERS_API_SRC','../../../orders_api/src/');
require_once(PATH_KEITARO_ORDERS_API_SRC.'send_order.php');

$protocol = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https://' : 'http://';
$currentDomain = $_SERVER['HTTP_HOST'];

// Параметры страницы "Спасибо" из POST
$language = isset($_POST['language']) ? $_POST['language'] : 'ES';
$country  = isset($_POST['country'])  ? $_POST['country']  : 'MX';

// UTM параметры из POST
$utm_source   = isset($_POST['utm_source'])   ? $_POST['utm_source']   : '';
$utm_medium   = isset($_POST['utm_medium'])   ? $_POST['utm_medium']   : '';
$utm_campaign = isset($_POST['utm_campaign']) ? $_POST['utm_campaign'] : '';
$utm_term     = isset($_POST['utm_term'])     ? $_POST['utm_term']     : '';
$utm_content  = isset($_POST['utm_content'])  ? $_POST['utm_content']  : '';

// Параметр для исключения слов из POST
$exclude_word = isset($_POST['exclude_word']) ? $_POST['exclude_word'] : '';

$params = [
    'language' => $language,
    'country'  => $country
];

if (!empty($utm_source))   $params['utm_source']   = $utm_source;
if (!empty($utm_medium))   $params['utm_medium']   = $utm_medium;
if (!empty($utm_campaign)) $params['utm_campaign'] = $utm_campaign;
if (!empty($utm_term))     $params['utm_term']     = $utm_term;
if (!empty($utm_content))  $params['utm_content']  = $utm_content;
if (!empty($exclude_word)) $params['exclude_word'] = $exclude_word;

$thxPage = $protocol . $currentDomain . '/thx-page?' . http_build_query($params);

$dimensionName = 'default';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    exit('Invalid request');
}

sendOrder($dimensionName, $thxPage, $_POST);
?>
"""


def ensure_api_php(root: Path, verbose: bool = False) -> int:
    """Кладёт эталонный api.php рядом с КАЖДОЙ точкой входа index.php.

    api.php — обязательный приёмник формы (раздел 5.1 AGENT.md), содержимое
    фиксированное. Перезаписываем любой существующий (чужой/донорский) эталоном.
    Если index.php в архиве нет — кладём в корень. Возвращает число записей.
    """
    targets = {p.parent for p in root.rglob('index.php')}
    if not targets:
        targets = {root}
    written = 0
    for d in targets:
        d.mkdir(parents=True, exist_ok=True)
        (d / 'api.php').write_text(API_PHP_TEMPLATE, encoding='utf-8')
        written += 1
        if verbose:
            rel = (d / 'api.php').relative_to(root)
            ok(f"API   {rel}  (эталонный api.php)")
    return written


def htmlize_to_php(root: Path, verbose: bool = False) -> int:
    """Переименовывает .html/.htm файлы ленда в .php и обновляет ссылки на них.

    Нужно, чтобы итоговый ленд всегда содержал PHP-вариант страниц (часть
    лендов приходит как .html). Возвращает число переименованных файлов.
    """
    html_files = [p for p in root.rglob('*')
                  if p.is_file() and p.suffix.lower() in {'.html', '.htm'}]
    if not html_files:
        return 0

    # Карта переименований по имени файла (basename) для правки ссылок.
    name_map: dict[str, str] = {}
    for p in html_files:
        new_p = p.with_suffix('.php')
        # Если .php с таким именем уже есть — оставляем как есть (не затираем).
        if new_p.exists():
            continue
        p.rename(new_p)
        name_map[p.name] = new_p.name
        if verbose:
            ok(f"HTML→PHP  {p.name} → {new_p.name}")

    if not name_map:
        return 0

    # Правим ссылки во всех текстовых файлах: index.html → index.php и т.п.
    # Слева запрещаем только словесный символ (чтобы не задеть "myindex.html",
    # но разрешить "./index.html", "/index.html"); справа — словесный символ
    # или точку (чтобы не задеть "index.html.png" и "index.htmlx").
    patterns = [
        (re.compile(r'(?<![\w])' + re.escape(old) + r'(?![\w.])'), new)
        for old, new in name_map.items()
    ]
    for p in root.rglob('*'):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT:
            continue
        try:
            text = p.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        new_text = text
        for rx, new in patterns:
            new_text = rx.sub(new, new_text)
        if new_text != text:
            p.write_text(new_text, encoding='utf-8')

    return len(name_map)


# ══════════════════════════════════════════════════════════════
# ОСНОВНОЙ ФЛОУ
# ══════════════════════════════════════════════════════════════

def adapt_one(offer_zip: str, config_path: str, dry_run: bool, verbose: bool):
    config    = json.loads(Path(config_path).read_text(encoding='utf-8'))
    geo_id    = config.get('geo_id', Path(config_path).stem)
    offer_id  = Path(offer_zip).stem
    rules     = config.get('replacements', [])
    image_map = config.get('image_files', {})

    section(f"ГЕО: {geo_id}  |  Оффер: {offer_id}")
    info(f"Конфиг: {config_path}")
    info(f"Продукт: {config.get('product_name','?')}  |  Цена: {config.get('_price_new','?')}")

    with tempfile.TemporaryDirectory() as tmp:
        src_root = Path(tmp) / 'src'
        info("Распаковываю…")
        with zipfile.ZipFile(offer_zip, 'r') as zf:
            zf.extractall(src_root)

        counts = scan_counts(src_root, rules)
        print_report(counts, rules, image_map)

        if dry_run:
            print(f"\n{Y}  ── DRY RUN: файлы не созданы ──{RESET}\n")
            return

        print()
        try:
            ans = input(f"  {BOLD}Начать адаптацию? [Y/n]: {RESET}").strip().lower()
        except EOFError:
            ans = 'y'
        if ans not in ('', 'y', 'yes', 'д', 'да'):
            warn("Пропускаю.")
            return

        section("⚙️   ОБРАБОТКА")
        dst_root = Path(tmp) / 'dst'
        dst_root.mkdir()
        stats = process_offer(src_root, dst_root, config, verbose)

        ok(f"Файлов изменено:   {stats['files']}")
        ok(f"Замен применено:   {stats['replacements']}")
        ok(f"Картинок заменено: {stats['images']}")
        for m in stats['missing']:
            warn(f"Нет в assets/: {m}")

        section("📦  РЕЗУЛЬТАТ")
        Path('output').mkdir(exist_ok=True)
        ts       = datetime.now().strftime('%Y%m%d_%H%M%S')
        out_path = Path('output') / f"{offer_id}__{geo_id}__{ts}.zip"

        info(f"Упаковываю в {out_path}…")
        with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fp in dst_root.rglob('*'):
                if fp.is_file():
                    zf.write(fp, fp.relative_to(dst_root))

        ok(f"Готово!  →  {G}{BOLD}{out_path}{RESET}  ({out_path.stat().st_size//1024} KB)")


# ══════════════════════════════════════════════════════════════
# ТОЧКА ВХОДА
# ══════════════════════════════════════════════════════════════

def main():
    banner()
    p = argparse.ArgumentParser(description='Offer Adapter v3.0')
    p.add_argument('--scan',    nargs='+', metavar='ZIP')
    p.add_argument('--offer',   nargs='+', metavar='ZIP')
    p.add_argument('--geo',     nargs='+', metavar='JSON')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--verbose', action='store_true')
    args = p.parse_args()

    if args.scan:
        if len(args.scan) > 5:
            err("Максимум 5 офферов за раз"); return
        from scanner import run_scan
        for z in args.scan:
            if Path(z).exists():
                run_scan(z)
            else:
                err(f"Файл не найден: {z}")
        print(f"\n{G}{BOLD}  ✅  Сканирование завершено!{RESET}\n")
        return

    if not args.offer or not args.geo:
        err("Укажи --offer и --geo  или используй --scan")
        p.print_help(); return

    for f in args.offer + args.geo:
        if not Path(f).exists():
            err(f"Не найден: {f}"); return

    for offer in args.offer:
        for geo_cfg in args.geo:
            adapt_one(offer, geo_cfg, dry_run=args.dry_run, verbose=args.verbose)

    print(f"\n{G}{BOLD}  ✅  Всё готово!{RESET}\n")


if __name__ == '__main__':
    main()
