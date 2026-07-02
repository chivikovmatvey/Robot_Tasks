"""
parser_v2 main entry point: DOM-aware HTML replacement.

Принцип:
  1. Защита PHP-блоков плейсхолдерами
  2. Применение правил по категориям (с учётом контекста DOM)
  3. Замены к ИСХОДНОЙ строке — форматирование не ломается (str(soup) не используется)
  4. Восстановление PHP-блоков
"""

import re
from bs4 import BeautifulSoup

from ._php import protect as _protect_php, restore as _restore_php
from ._primitives import (
    replace_outside_attrs as _outside,
    replace_in_file_attrs as _file_attrs,
    replace_in_named_attr as _named_attr,
)
from ._split import replace_split_text as _split_text, replace_split_price as _split_price


def apply_dom_replacements(html_text: str, rules: list[dict], image_map: dict) -> tuple[str, int]:
    """
    Применяет правила замены с DOM-осведомлённостью.

    Категории правил:
    - ПРОДУКТ          → только видимый текст (не атрибуты)
    - PROD_IMG         → только файловые атрибуты (src, srcset, ...)
    - PROD_DATA        → только data-product-image
    - ЦЕНА_НОВ_ЧИСЛО   → текст + data-new-price (число)
    - ЦЕНА_СТА_ЧИСЛО   → текст + data-old-price (число)
    - ЦЕНА_*_ВАЛЮТА    → только текст (НЕ атрибуты: там может быть другой символ)
    - ЦЕНА_*           → текст (комбинированная замена с fallback)
    - WIDGET_ЦЕНА_НОВ  → только data-new-price (полная замена значения)
    - WIDGET_ЦЕНА_СТА  → только data-old-price (полная замена значения)
    - ВАЛЮТА           → текст + data-new-price + data-old-price + data-currency
    - остальные        → текст + value/data-country/data-language/data-currency
    """
    result, php_map = _protect_php(html_text)
    total = 0

    for r in rules:
        label     = r.get('label', '')
        find_str  = r.get('find', '')
        repl_str  = r.get('replace', '')

        if not find_str or find_str == repl_str:
            continue

        # ── ПРОДУКТ ──────────────────────────────────────────────────────────
        if label == 'ПРОДУКТ':
            result, n = _outside(result, find_str, repl_str)
            if n == 0:
                soup = BeautifulSoup(result, 'html.parser')
                result, n = _split_text(result, soup, find_str, repl_str)
            # Имя продукта используется и в атрибуте data-product-name (виджет формы)
            result, na = _named_attr(result, 'data-product-name', find_str, repl_str)
            total += n + na

        # ── PROD_IMG ─────────────────────────────────────────────────────────
        elif label == 'PROD_IMG':
            result, n = _file_attrs(result, find_str, repl_str)
            total += n

        # ── PROD_DATA ────────────────────────────────────────────────────────
        elif label == 'PROD_DATA':
            old_v = re.search(r'"([^"]+)"$', find_str)
            new_v = re.search(r'"([^"]+)"$', repl_str)
            if old_v and new_v:
                result, n = _named_attr(result, 'data-product-image',
                                        old_v.group(1), new_v.group(1))
                total += n

        # ── ЦЕНА_НОВ_ЧИСЛО ───────────────────────────────────────────────────
        # Число новой цены: заменяем в тексте и в data-new-price
        elif label.startswith('ЦЕНА_НОВ_') and '_ЧИСЛО' in label:
            result, n = _outside(result, find_str, repl_str,
                                 number_mode=find_str.isdigit())
            if n == 0:
                soup = BeautifulSoup(result, 'html.parser')
                result, n = _split_text(result, soup, find_str, repl_str)
            # Fallback: если в data-new-price осталось старое число — меняем
            result, n2 = _named_attr(result, 'data-new-price', find_str, repl_str)
            total += n + n2

        # ── ЦЕНА_СТА_ЧИСЛО ───────────────────────────────────────────────────
        # Число старой цены: заменяем в тексте и в data-old-price
        elif label.startswith('ЦЕНА_СТА_') and '_ЧИСЛО' in label:
            result, n = _outside(result, find_str, repl_str,
                                 number_mode=find_str.isdigit())
            if n == 0:
                soup = BeautifulSoup(result, 'html.parser')
                result, n = _split_text(result, soup, find_str, repl_str)
            result, n2 = _named_attr(result, 'data-old-price', find_str, repl_str)
            total += n + n2

        # ── ЦЕНА_*_ВАЛЮТА ────────────────────────────────────────────────────
        # Валюта: только в видимом тексте (атрибуты виджета могут использовать
        # другой символ — они обрабатываются правилом ВАЛЮТА или WIDGET_*)
        elif '_ВАЛЮТА' in label:
            result, n = _outside(result, find_str, repl_str)
            if n == 0:
                soup = BeautifulSoup(result, 'html.parser')
                result, n = _split_text(result, soup, find_str, repl_str)
            total += n

        # ── ЦЕНА_* (комбинированный, старый формат) ──────────────────────────
        elif label.startswith('ЦЕНА_'):
            result, n = _outside(result, find_str, repl_str,
                                 number_mode=find_str.isdigit())
            if n == 0:
                soup = BeautifulSoup(result, 'html.parser')
                result, n = _split_price(result, soup, find_str, repl_str)
                if n == 0:
                    result, n = _split_text(result, soup, find_str, repl_str)
            total += n

        # ── WIDGET_ЦЕНА_НОВ ──────────────────────────────────────────────────
        # Полная замена значения data-new-price
        elif label.startswith('WIDGET_ЦЕНА_НОВ'):
            old_v = re.search(r'"([^"]+)"$', find_str)
            new_v = re.search(r'"([^"]+)"$', repl_str)
            if old_v and new_v:
                result, n = _named_attr(result, 'data-new-price',
                                        old_v.group(1), new_v.group(1))
            else:
                result, n = _named_attr(result, 'data-new-price', find_str, repl_str)
            total += n

        # ── WIDGET_ЦЕНА_СТА ──────────────────────────────────────────────────
        elif label.startswith('WIDGET_ЦЕНА_СТА'):
            old_v = re.search(r'"([^"]+)"$', find_str)
            new_v = re.search(r'"([^"]+)"$', repl_str)
            if old_v and new_v:
                result, n = _named_attr(result, 'data-old-price',
                                        old_v.group(1), new_v.group(1))
            else:
                result, n = _named_attr(result, 'data-old-price', find_str, repl_str)
            total += n

        # ── ВАЛЮТА ───────────────────────────────────────────────────────────
        # Символ валюты (₹, €, £, ...): в тексте и в data-атрибутах виджета
        elif label == 'ВАЛЮТА':
            result, n = _outside(result, find_str, repl_str)
            n2 = 0
            for attr in ('data-new-price', 'data-old-price', 'data-currency'):
                result, k = _named_attr(result, attr, find_str, repl_str)
                n2 += k
            total += n + n2

        # ── Всё остальное (СТРАНА, LANG, INP, CUSTOM, ...) ───────────────────
        else:
            result, n = _outside(result, find_str, repl_str)
            n2 = 0
            for attr in ('value', 'data-country', 'data-language', 'data-currency'):
                result, k = _named_attr(result, attr, find_str, repl_str)
                n2 += k
            total += n + n2

    # image_map — замена имён файлов в атрибутах
    for old_img, new_img in image_map.items():
        result, n = _file_attrs(result, old_img, new_img)
        total += n

    result = _restore_php(result, php_map)
    return result, total
