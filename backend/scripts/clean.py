#!/usr/bin/env python3
"""
clean.py — очистка лендинга от чужеродного кода
Правила в clean_rules.json

Использование:
  python clean.py --file input/site.zip
  python clean.py --file input/s1.zip input/s2.zip
"""
import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8','utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse, json, re, shutil, tempfile, zipfile
from pathlib import Path
from datetime import datetime

import os as _os
if _os.name != 'nt' or 'WT_SESSION' in _os.environ or 'ANSICON' in _os.environ:
    R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"
    C="\033[96m"; DIM="\033[2m"; RESET="\033[0m"; BOLD="\033[1m"
else:
    R=G=Y=B=C=DIM=RESET=BOLD=""

def ok(m):      print(f"  {G}+{RESET}  {m}")
def warn(m):    print(f"  {Y}!{RESET}  {m}")
def info(m):    print(f"  {B}>{RESET}  {m}")
def err(m):     print(f"  {R}x{RESET}  {m}")
def gone(m):    print(f"  {R}-{RESET}  {DIM}{m}{RESET}")
def section(t): print(f"\n{C}{BOLD}{'─'*52}\n  {t}\n{'─'*52}{RESET}")

TEXT_EXT = {'.php', '.html', '.htm', '.js', '.css'}

PHP_SESSION_BLOCK = """<?php
session_start();
$_SESSION['click_id'] = !empty($_GET['click_id']) ? htmlspecialchars($_GET['click_id']) : "";
$_SESSION['fb_pixel'] = !empty($_GET['fb_pixel']) ? htmlspecialchars($_GET['fb_pixel']) : "";
$_SESSION['stream_id'] = !empty($_GET['stream_id']) ? htmlspecialchars($_GET['stream_id']) : "";
$_SESSION['pub_sub_id'] = !empty($_GET['pub_sub_id']) ? htmlspecialchars($_GET['pub_sub_id']) : "";
$_SESSION['extra_id_1'] = !empty($_GET['extra_id_1']) ? htmlspecialchars($_GET['extra_id_1']) : "";
$_SESSION['extra_id_2'] = !empty($_GET['extra_id_2']) ? htmlspecialchars($_GET['extra_id_2']) : "";
$_SESSION['google_pixel'] = !empty($_GET['google_pixel']) ? htmlspecialchars($_GET['google_pixel']) : "";
$_SESSION['tiktok_pixel'] = !empty($_GET['tiktok_pixel']) ? htmlspecialchars($_GET['tiktok_pixel']) : "";
$_SESSION['publisher_order_id'] = !empty($_GET['publisher_order_id']) ? htmlspecialchars($_GET['publisher_order_id']) : "";
?>
"""


def load_rules() -> dict:
    p = Path(__file__).parent / 'clean_rules.json'
    if not p.exists():
        warn("clean_rules.json not found")
        return {}
    return json.loads(p.read_text(encoding='utf-8'))


# ══════════════════════════════════════════════════════════════
# ФУНКЦИИ ОЧИСТКИ
# ══════════════════════════════════════════════════════════════

def remove_blocks(text: str, rules: list) -> tuple[str, list]:
    """Удаляет блоки от start до end по точным маркерам."""
    removed = []
    for r in rules:
        start_m, end_m = r['start'], r['end']
        while True:
            s = text.find(start_m)
            if s == -1:
                break
            e = text.find(end_m, s)
            if e == -1:
                break
            e += len(end_m)
            if e < len(text) and text[e] == '\n':
                e += 1
            text = text[:s] + text[e:]
            if r['label'] not in removed:
                removed.append(r['label'])
    return text, removed


def remove_scripts_with(text: str, rules: list) -> tuple[str, list]:
    """
    Удаляет целые блоки <script>...</script> и <noscript>...</noscript>
    если внутри содержится заданная строка.
    """
    removed = []
    for tag in ['<script', '<noscript']:
        close = '</' + tag[1:] + '>'
        i = 0
        while True:
            low = text.lower()
            s = low.find(tag, i)
            if s == -1:
                break
            e = low.find(close, s)
            if e == -1:
                i = s + 1
                continue
            e += len(close)
            block = text[s:e]
            matched = None
            for r in rules:
                if r['contains'] in block:
                    matched = r['label']
                    break
            if matched:
                end = e
                if end < len(text) and text[end] == '\n':
                    end += 1
                text = text[:s] + text[end:]
                if matched not in removed:
                    removed.append(matched)
                # не двигаем i
            else:
                i = s + 1
    return text, removed


def remove_lines_with(text: str, rules: list) -> tuple[str, list]:
    """Удаляет строки содержащие заданные подстроки."""
    removed = []
    lines = text.splitlines(keepends=True)
    result = []
    for line in lines:
        matched = None
        for r in rules:
            if r['contains'] in line:
                matched = r['label']
                break
        if matched:
            if matched not in removed:
                removed.append(matched)
        else:
            result.append(line)
    return ''.join(result), removed


def remove_inputs_by_name(text: str, rules: list) -> tuple[str, list]:
    """Удаляет hidden input по name."""
    removed = []
    for r in rules:
        name = r['name']
        pattern = r'<input[^>]*name=["\']' + re.escape(name) + r'["\'][^>]*/?>[ \t]*\n?'
        new_text, n = re.subn(pattern, '', text, flags=re.IGNORECASE)
        if n:
            text = new_text
            removed.append(name)
    return text, removed


def strip_input_attrs(text: str, config: dict) -> tuple[str, list]:
    """
    Удаляет указанные атрибуты (например maxlength/minlength) у <input>,
    name которых попадает в список target_names. Если name не указан —
    обрабатываем все <input>, кроме hidden.

    Конфиг (clean_rules.json):
      "strip_input_attrs": {
        "target_names": ["name", "phone", "tel", "fname", "fio", ...],
        "attrs": ["maxlength", "minlength", "pattern"]
      }
    """
    if not config:
        return text, []

    target_names = {n.lower() for n in config.get('target_names', [])}
    attrs        = [a.lower() for a in config.get('attrs', [])]
    if not attrs:
        return text, []

    removed = []  # сюда положим имена атрибутов, которые реально удалили

    def replacer(m):
        tag = m.group(0)
        # выясняем name и type
        nm = re.search(r'name=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        tp = re.search(r'type=["\']([^"\']*)["\']', tag, re.IGNORECASE)
        name_val = nm.group(1).lower().strip() if nm else ''
        type_val = tp.group(1).lower().strip() if tp else ''

        # hidden пропускаем — там этих атрибутов и не бывает
        if type_val == 'hidden':
            return tag

        # если задан target_names и тег под него не подходит — пропускаем
        if target_names and name_val not in target_names and type_val not in target_names:
            return tag

        new_tag = tag
        for attr in attrs:
            # удаляем атрибут с любым значением: maxlength="50", maxlength='50', maxlength=50
            pat = r'\s+' + re.escape(attr) + r'(?:\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+))?'
            new_tag2, n = re.subn(pat, '', new_tag, flags=re.IGNORECASE)
            if n and attr not in removed:
                removed.append(attr)
            new_tag = new_tag2
        return new_tag

    text = re.sub(r'<input\b[^>]*/?>', replacer, text, flags=re.IGNORECASE)
    return text, removed


def clean_form_action(text: str) -> tuple[str, int]:
    """Очищает action у всех форм."""
    new_text = re.sub(r'action=["\'][^"\']+["\']', 'action=""', text)
    n = len(re.findall(r'action=["\'][^"\']+["\']', text))
    return new_text, n


def inject_php_session(text: str, filename: str) -> tuple[str, bool]:
    """Вставляет PHP session блок в начало index.php, если его там ещё нет."""
    if filename != 'index.php':
        return text, False
    if 'session_start()' in text:
        return text, False
    return PHP_SESSION_BLOCK + text, True


def clean_text(text: str, rules: dict, filename: str = '') -> tuple[str, dict]:
    stats = {'blocks': [], 'scripts': [], 'lines': [], 'inputs': [], 'actions': 0, 'stripped_attrs': []}

    if rules.get('remove_blocks'):
        text, labels = remove_blocks(text, rules['remove_blocks'])
        stats['blocks'] = labels

    if rules.get('remove_scripts_with'):
        text, labels = remove_scripts_with(text, rules['remove_scripts_with'])
        stats['scripts'] = labels

    if rules.get('remove_lines_with'):
        text, labels = remove_lines_with(text, rules['remove_lines_with'])
        stats['lines'] = labels

    if rules.get('remove_inputs_by_name'):
        text, labels = remove_inputs_by_name(text, rules['remove_inputs_by_name'])
        stats['inputs'] = labels

    # Снимаем maxlength/minlength и подобные атрибуты с инпутов имени/телефона
    if rules.get('strip_input_attrs'):
        text, attrs = strip_input_attrs(text, rules['strip_input_attrs'])
        stats['stripped_attrs'] = attrs

    if rules.get('clean_form_action'):
        text, n = clean_form_action(text)
        stats['actions'] = n

    return text, stats


# ══════════════════════════════════════════════════════════════
# ОБРАБОТКА АРХИВА
# ══════════════════════════════════════════════════════════════

def process_zip(zip_path: str, rules: dict) -> str:
    remove_files = {r['path'] for r in rules.get('remove_files', [])}
    remove_dirs  = {r['path'].strip('/') for r in rules.get('remove_dirs', [])}

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / 'src'
        dst = Path(tmp) / 'dst'
        dst.mkdir()

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(src)

        total = {'blocks': set(), 'scripts': set(), 'lines': set(),
                 'inputs': set(), 'actions': 0, 'files': 0, 'removed': [],
                 'stripped_attrs': set()}

        for fp in sorted(src.rglob('*')):
            if not fp.is_file():
                continue
            rel = fp.relative_to(src)
            rel_str = str(rel).replace('\\', '/')
            dst_fp = dst / rel
            dst_fp.parent.mkdir(parents=True, exist_ok=True)

            # Удаляем файлы из списка
            if rel_str in remove_files or fp.name in remove_files:
                gone(f"File removed: {rel_str}")
                total['removed'].append(rel_str)
                continue

            # Удаляем файлы из папок которые нужно удалить целиком
            skip_dir = False
            for d in remove_dirs:
                if rel_str == d or rel_str.startswith(d + '/') or rel_str.startswith(d + '\\'):
                    skip_dir = True
                    break
            if skip_dir:
                if d not in total['removed']:
                    gone(f"Dir removed: {d}/")
                    total['removed'].append(d + '/')
                continue

            if fp.suffix.lower() in TEXT_EXT:
                try:
                    text = fp.read_text(encoding='utf-8', errors='replace')
                except Exception:
                    shutil.copy2(fp, dst_fp)
                    continue

                new_text, stats = clean_text(text, rules, filename=fp.name)

                if new_text != text:
                    total['files'] += 1
                    total['blocks'].update(stats['blocks'])
                    total['scripts'].update(stats['scripts'])
                    total['lines'].update(stats['lines'])
                    total['inputs'].update(stats['inputs'])
                    total['actions'] += stats['actions']
                    total['stripped_attrs'].update(stats['stripped_attrs'])

                    for lbl in stats['blocks']:   gone(f"[block]  {lbl}")
                    for lbl in stats['scripts']:  gone(f"[script] {lbl}")
                    for lbl in stats['lines']:    gone(f"[line]   {lbl}")
                    for nm  in stats['inputs']:   gone(f"[input]  name={nm}")
                    for at  in stats['stripped_attrs']: gone(f"[attr]   {at}=")
                    if stats['actions']:          ok(f"form action cleared ({stats['actions']}x) in {rel}")

                dst_fp.write_text(new_text, encoding='utf-8')
            else:
                shutil.copy2(fp, dst_fp)

        Path('output').mkdir(exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = Path('output') / f"{Path(zip_path).stem}__clean__{ts}.zip"
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fp in dst.rglob('*'):
                if fp.is_file():
                    zf.write(fp, fp.relative_to(dst))

    return str(out), total


def main():
    print(f"\n{B}{BOLD}╔══════════════════════════════════════════════════╗")
    print(f"║        Cleaner v1.0                              ║")
    print(f"╚══════════════════════════════════════════════════╝{RESET}\n")

    p = argparse.ArgumentParser()
    p.add_argument('--file', nargs='+', metavar='ZIP', required=True)
    args = p.parse_args()

    rules = load_rules()
    info(f"Block rules:  {len(rules.get('remove_blocks', []))}")
    info(f"Script rules: {len(rules.get('remove_scripts_with', []))}")
    info(f"Line rules:   {len(rules.get('remove_lines_with', []))}")

    for zip_path in args.file:
        if not Path(zip_path).exists():
            err(f"Not found: {zip_path}")
            continue
        section(f"Cleaning: {Path(zip_path).name}")
        out, total = process_zip(zip_path, rules)
        section("Result")
        ok(f"Files changed:   {total['files']}")
        ok(f"Files removed:   {len(total['removed'])}")
        if total['blocks']:           ok(f"Blocks removed:  {', '.join(total['blocks'])}")
        if total['scripts']:          ok(f"Scripts removed: {', '.join(total['scripts'])}")
        if total['lines']:            ok(f"Lines removed:   {', '.join(total['lines'])}")
        if total['stripped_attrs']:   ok(f"Attrs stripped:  {', '.join(total['stripped_attrs'])}")
        print(f"\n  {G}{BOLD}Done -> {out}{RESET}\n")

    print(f"\n{G}{BOLD}  All done!{RESET}\n")

if __name__ == '__main__':
    main()