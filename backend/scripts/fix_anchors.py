#!/usr/bin/env python3
"""
fix_anchors.py — фиксит якорные ссылки в лендинге

Что делает:
  1. Находит <form> в HTML
  2. Если у формы нет id — добавляет id="order_form"
  3. Находит все <a href="#..."> и меняет их на правильный id формы
  4. Находит все <a href=""> (пустые) и тоже проставляет id формы

Использование:
  python fix_anchors.py --file input/site.zip
  python fix_anchors.py --file input/s1.zip input/s2.zip
"""

import sys, io
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import argparse, re, shutil, tempfile, zipfile
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
def section(t): print(f"\n{C}{BOLD}{'─'*52}\n  {t}\n{'─'*52}{RESET}")

TEXT_EXT = {'.php', '.html', '.htm'}
DEFAULT_FORM_ID = 'order_form'


def find_form_id(text: str) -> str | None:
    """Находит id у первой формы в тексте."""
    m = re.search(r'<form\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>', text, re.IGNORECASE)
    if m:
        return m.group(1)
    # Ищем id перед или после других атрибутов
    m = re.search(r'<form\b[^>]*>', text, re.IGNORECASE)
    if m:
        tag = m.group(0)
        mid = re.search(r'\bid=["\']([^"\']+)["\']', tag, re.IGNORECASE)
        if mid:
            return mid.group(1)
    return None


def add_form_id(text: str, form_id: str) -> tuple[str, bool]:
    """Добавляет id к первой форме если его нет."""
    def replacer(m):
        tag = m.group(0)
        if re.search(r'\bid=', tag, re.IGNORECASE):
            return tag  # id уже есть
        # Вставляем id после <form
        return re.sub(r'(<form\b)', rf'\1 id="{form_id}"', tag, flags=re.IGNORECASE)

    new_text = re.sub(r'<form\b[^>]*>', replacer, text, count=1, flags=re.IGNORECASE)
    return new_text, new_text != text


def fix_anchor_links(text: str, form_id: str) -> tuple[str, int]:
    """
    Меняет все якорные ссылки на правильный id формы:
    - <a href="#anything"> → <a href="#order_form">
    - <a href=""> → <a href="#order_form">
    - <a href="#"> → <a href="#order_form">
    НЕ трогает:
    - ссылки на внешние URL (http://, https://)
    - ссылки на файлы (.pdf, .php и т.д.)
    """
    count = 0
    target = f'#{form_id}'

    def replacer(m):
        nonlocal count
        full = m.group(0)    # весь тег <a ...>
        href_val = m.group(1)  # значение href

        # Пропускаем внешние ссылки
        if href_val.startswith(('http://', 'https://', 'mailto:', 'tel:')):
            return full
        # Пропускаем ссылки на файлы/скрипты
        if '.' in href_val and not href_val.startswith('#'):
            return full
        # Пропускаем javascript:
        if href_val.lower().startswith('javascript'):
            return full
        # Пропускаем если уже правильный id
        if href_val == target:
            return full

        # Меняем href на правильный
        new_tag = re.sub(
            r'href=["\'][^"\']*["\']',
            f'href="{target}"',
            full,
            flags=re.IGNORECASE
        )
        if new_tag != full:
            count += 1
        return new_tag

    # Ищем все <a href="...">
    new_text = re.sub(
        r'<a\b[^>]*\bhref=["\']([^"\']*)["\'][^>]*>',
        replacer,
        text,
        flags=re.IGNORECASE
    )
    return new_text, count


def process_file(text: str) -> tuple[str, dict]:
    """Обрабатывает один HTML/PHP файл."""
    stats = {'form_id': None, 'id_added': False, 'links_fixed': 0}

    # 1. Ищем id формы
    form_id = find_form_id(text)

    if form_id:
        stats['form_id'] = form_id
        info(f"Form id найден: #{form_id}")
    else:
        # Проверяем есть ли форма вообще
        if '<form' in text.lower():
            form_id = DEFAULT_FORM_ID
            text, added = add_form_id(text, form_id)
            stats['form_id'] = form_id
            stats['id_added'] = added
            if added:
                ok(f"Добавлен id к форме: #{form_id}")
        else:
            warn("Форма не найдена в файле — пропускаю")
            return text, stats

    # 2. Фиксим якорные ссылки
    text, n = fix_anchor_links(text, form_id)
    stats['links_fixed'] = n
    if n:
        ok(f"Исправлено ссылок: {n}")

    return text, stats


def process_zip(zip_path: str) -> tuple[str, dict]:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / 'src'
        dst = Path(tmp) / 'dst'
        dst.mkdir()

        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(src)

        total = {'links': 0, 'files': 0}

        for fp in sorted(src.rglob('*')):
            if not fp.is_file():
                continue
            rel = fp.relative_to(src)
            dst_fp = dst / rel
            dst_fp.parent.mkdir(parents=True, exist_ok=True)

            if fp.suffix.lower() in TEXT_EXT:
                try:
                    text = fp.read_text(encoding='utf-8', errors='replace')
                except Exception:
                    shutil.copy2(fp, dst_fp)
                    continue

                info(f"Обрабатываю: {rel}")
                new_text, stats = process_file(text)

                if new_text != text:
                    total['files'] += 1
                    total['links'] += stats['links_fixed']

                dst_fp.write_text(new_text, encoding='utf-8')
            else:
                shutil.copy2(fp, dst_fp)

        Path('output').mkdir(exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = Path('output') / f"{Path(zip_path).stem}__anchors__{ts}.zip"
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fp in dst.rglob('*'):
                if fp.is_file():
                    zf.write(fp, fp.relative_to(dst))

    return str(out), total


def main():
    print(f"\n{B}{BOLD}╔══════════════════════════════════════════════════╗")
    print(f"║        Anchor Fixer v1.0                         ║")
    print(f"╚══════════════════════════════════════════════════╝{RESET}\n")

    p = argparse.ArgumentParser()
    p.add_argument('--file', nargs='+', metavar='ZIP', required=True)
    args = p.parse_args()

    for zip_path in args.file:
        if not Path(zip_path).exists():
            err(f"Not found: {zip_path}")
            continue

        section(f"Processing: {Path(zip_path).name}")
        out, total = process_zip(zip_path)

        section("Result")
        ok(f"Files changed: {total['files']}")
        ok(f"Links fixed:   {total['links']}")
        print(f"\n  {G}{BOLD}Done -> {out}{RESET}\n")

    print(f"\n{G}{BOLD}  All done!{RESET}\n")


if __name__ == '__main__':
    main()
