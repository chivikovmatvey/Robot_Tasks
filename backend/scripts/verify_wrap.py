"""verify_wrap.py — чеклист обвязки готового ленда (AGENT.md §5/§6).

Прогоняется автоматически ПОСЛЕ адаптации (run_adapt): сверяет итоговый архив
с регламентом и возвращает список проблем. Появился после кейса
luminaeterna.com (DI Biosulin MX): адаптация прошла «успешно», но форма была
id="order_form", чужие hidden (subid донора, fbclid) остались, а
language/exclude_word/utm_campaign/offerId не вставились — обвязка не работала.
Эталон правильной обвязки: 21411_offer_archive.

Каждая проблема: {"level": "error"|"warning", "msg": "..."}.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

# Наш набор hidden-инпутов формы (§5.3) и обязательные значения-макросы.
_REQUIRED_HIDDEN = ["click_data", "thx_page", "language", "country",
                    "exclude_word", "utm_campaign", "subid", "offerId"]
_MACRO_VALUES = {"utm_campaign": "{offer_id}", "subid": "{subid}",
                 "offerId": "{offer_id}"}

# Следы чужой обвязки, которых в готовом ленде быть не должно.
_FOREIGN_MARKS = [
    ("masked.js", "чужая маска masked.js"),
    ("jquery.maskedinput", "чужая маска jquery.maskedinput"),
    ("back.js", "чужой back.js"),
    ("api.m1.top", "скрипт M1"),
    ("KMA.validateAndSendForm", "скрипт KMA"),
    ("cloudflareinsights.com", "метрика Cloudflare Insights"),
    ("mc.yandex.ru", "Яндекс.Метрика"),
]


def _err(msg: str) -> dict:
    return {"level": "error", "msg": msg}


def _warn(msg: str) -> dict:
    return {"level": "warning", "msg": msg}


def verify_html(html: str) -> list[dict]:
    """Проверяет ГЛАВНЫЙ html/php ленда. → список проблем."""
    out: list[dict] = []

    # 1. PHP-шапка (init + clickJson + exit).
    if "orders_api/src/v2/init.php" not in html:
        out.append(_err("нет PHP-шапки init.php (§5.2a)"))
    if "$clickJson" not in html:
        out.append(_err("нет $clickJson = getClickData(...)"))
    if not re.search(r"if\s*\(\s*!isset\(\$rawClick\)\s*\)", html):
        out.append(_err("нет exit() без $rawClick"))

    # 2. Counters + Backfix перед </head>.
    if "counters/first.min.js" not in html:
        out.append(_err("нет Counters first step"))
    if "{_from_file:backfix_file_path}" not in html:
        out.append(_err("нет Backfix (макрос backfix_file_path)"))

    # 3. Виджет + маска перед </body>.
    if "{_from_file:widget_2in1_path}" not in html:
        out.append(_err("нет виджета (макрос widget_2in1_path)"))
    if "{_from_file:form_mask_file_path}" not in html:
        out.append(_err("нет маски формы (макрос form_mask_file_path)"))
    n_widget = html.count('id="universal-widget-combined"')
    if n_widget > 1:
        out.append(_err(f"виджет вставлен {n_widget} раза (дубль)"))
    m = re.search(r'<script[^>]*id="form_mask"[^>]*>', html)
    if m and 'data-country="' not in m.group(0):
        out.append(_err("у маски формы нет data-country"))

    # 4. Форма: id="form", hidden-набор, phone-input-1.
    forms = re.findall(r"<form\b[^>]*>", html, re.I)
    if not forms:
        out.append(_err("на ленде нет <form>"))
    else:
        if not any('id="form"' in f for f in forms):
            out.append(_err(f'ни одна форма не имеет id="form" (есть: '
                            f'{", ".join(forms)[:120]})'))
        for name in _REQUIRED_HIDDEN:
            if f'name="{name}"' not in html:
                out.append(_err(f'нет hidden-инпута name="{name}"'))
        for name, macro in _MACRO_VALUES.items():
            mm = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html)
            mm2 = re.search(rf'value="([^"]*)"[^>]*name="{name}"', html)
            val = (mm or mm2).group(1) if (mm or mm2) else None
            if val is not None and macro not in val:
                out.append(_err(f'hidden {name}: значение «{val[:40]}» вместо '
                                f'макроса {macro} (зашитое значение донора)'))
        if "phone-input-1" not in html:
            out.append(_err("нет инпута телефона с классом phone-input-1"))

    # 5. Следы чужой обвязки.
    for mark, label in _FOREIGN_MARKS:
        if mark in html:
            out.append(_warn(f"остался след чужой обвязки: {label} ({mark})"))

    # 6. Виджет: фото продукта задано.
    pm = re.search(r'data-product-image="([^"]*)"', html)
    if pm and not pm.group(1).strip():
        out.append(_warn("у виджета пустой data-product-image"))

    return out


def verify_zip(zip_path: str | Path) -> list[dict]:
    """Проверяет итоговый архив ленда. → список проблем (пустой = всё ок)."""
    out: list[dict] = []
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            basenames = {Path(n).name for n in names}

            # index.php (не .html) обязателен.
            index = next((n for n in names
                          if Path(n).name == "index.php"), None)
            if not index:
                if any(Path(n).name in ("index.html", "index.htm") for n in names):
                    out.append(_err("index остался .html — в КТ нужен index.php"))
                else:
                    out.append(_err("в архиве нет index.php"))
                return out

            html = zf.read(index).decode("utf-8", "replace")
            out.extend(verify_html(html))

            # api.php по шаблону.
            api = next((n for n in names if Path(n).name == "api.php"), None)
            if not api:
                out.append(_err("нет api.php"))
            else:
                api_txt = zf.read(api).decode("utf-8", "replace")
                if "send_order.php" not in api_txt:
                    out.append(_err("api.php не по шаблону (нет send_order.php)"))

            # Фото продукта виджета существует в архиве.
            pm = re.search(r'data-product-image="([^"{}]+)"', html)
            if pm and Path(pm.group(1)).name not in basenames:
                out.append(_err(f"фото продукта виджета «{pm.group(1)}» "
                                f"нет в архиве"))

            # Явно чужие файлы, оставшиеся в архиве.
            for junk in ("back.js", "masked.js", "showcase.js",
                         "webpack-pro-runtime.js"):
                hit = next((n for n in names if Path(n).name == junk), None)
                if hit:
                    out.append(_warn(f"в архиве остался чужой файл: {hit}"))
    except Exception as e:  # noqa: BLE001
        out.append(_warn(f"проверка обвязки не выполнена: {e}"))
    return out
