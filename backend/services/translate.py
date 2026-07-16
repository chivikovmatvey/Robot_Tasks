"""Система перевода ленда на целевой язык через AITUNNEL (deepseek-v4-flash).

Подход (с учётом антипаттернов §6 AGENT.md — не ломать структуру):
  1. извлекаем ВИДИМЫЙ текст ПОБЛОЧНО (текстовые узлы + переводимые атрибуты),
     не переводим файл целиком одним промптом;
  2. маскируем макросы Keitaro / URL / e-mail токенами — модель их не трогает;
  3. переводим батчами, строгий JSON (response_format json_object), тот же
     порядок и количество блоков;
  4. валидируем (число блоков, маски на месте);
  5. применяем точечной заменой original→translated в файлах output-архива
     (структура HTML/PHP не парсится на запись — сохраняется как есть);
  6. отдаём дифф для обязательной вычитки человеком перед заливкой.

Имена/города/валюту переводчик НЕ меняет (это отдельный шаг geo_words);
модель инструктируется не трогать имена собственные/бренды/числа.

Модель настраивается через TRANSLATE_MODEL (по умолчанию deepseek-v4-flash).
"""

from __future__ import annotations

import json
import logging
import os
import re
import zipfile
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup, NavigableString

from services.session import get_manager
from utils import runners

log = logging.getLogger("translate")

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUTS = BASE_DIR / "storage" / "outputs"

DEFAULT_MODEL = "deepseek-v4-flash"
BATCH_SIZE = 25                 # блоков на запрос (меньше — надёжнее против обрезки)
MAX_TOKENS = 16384              # запас под языки с «дорогими» токенами (ar/hi/ur/zh…)
TEXT_FILE_EXT = {".html", ".htm", ".php"}

# Переводимые атрибуты (видимый пользователю текст).
_ATTR_TEXTS = ("placeholder", "alt", "title", "aria-label")

# Что маскируем (НЕ переводить, сохранить дословно): макросы, URL, e-mail.
_MASK_PATTERNS = [
    re.compile(r"\{\{.*?\}\}"),          # {{...}}
    re.compile(r"\{[^{}\n]*\}"),         # {macro}
    re.compile(r"%[A-Za-z0-9_]+%"),      # %macro%
    re.compile(r"https?://[^\s\"'<>]+"),  # url
    re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b"),  # email
]


def translate_model() -> str:
    return os.getenv("TRANSLATE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


# Код языка (ISO 639-1) → человекочитаемое название с нативным написанием.
# Модель ЗАМЕТНО лучше следует названию, чем коду (проверено: код 'pt' → не
# переводил; «португальский (Português)» → переводит). Покрывает все языки CSV.
_LANG_NAMES = {
    "es": "испанский (Español)", "pt": "португальский (Português)",
    "en": "английский (English)", "de": "немецкий (Deutsch)",
    "fr": "французский (Français)", "it": "итальянский (Italiano)",
    "pl": "польский (Polski)", "ro": "румынский (Română)",
    "cs": "чешский (Čeština)", "sk": "словацкий (Slovenčina)",
    "hu": "венгерский (Magyar)", "el": "греческий (Ελληνικά)",
    "tr": "турецкий (Türkçe)", "ar": "арабский (العربية)",
    "th": "тайский (ไทย)", "vi": "вьетнамский (Tiếng Việt)",
    "id": "индонезийский (Bahasa Indonesia)", "ms": "малайский (Bahasa Melayu)",
    "nl": "нидерландский (Nederlands)", "hr": "хорватский (Hrvatski)",
    # — расширение под все гео CSV (вкл. арабский/урду/хинди и др.) —
    "ur": "урду (اردو)", "hi": "хинди (हिन्दी)", "bn": "бенгальский (বাংলা)",
    "fa": "персидский/фарси (فارسی)", "he": "иврит (עברית)",
    "ru": "русский", "uk": "украинский (Українська)", "be": "белорусский (Беларуская)",
    "bg": "болгарский (Български)", "sr": "сербский (Српски)",
    "bs": "боснийский (Bosanski)", "sl": "словенский (Slovenščina)",
    "mk": "македонский (Македонски)", "sq": "албанский (Shqip)",
    "lt": "литовский (Lietuvių)", "lv": "латышский (Latviešu)",
    "et": "эстонский (Eesti)", "fi": "финский (Suomi)", "sv": "шведский (Svenska)",
    "da": "датский (Dansk)", "nb": "норвежский (Norsk Bokmål)",
    "nn": "норвежский (Norsk Nynorsk)", "is": "исландский (Íslenska)",
    "ga": "ирландский (Gaeilge)", "ca": "каталанский (Català)",
    "ja": "японский (日本語)", "ko": "корейский (한국어)",
    "zh": "китайский (中文)", "ta": "тамильский (தமிழ்)",
    "si": "сингальский (සිංහල)", "ne": "непальский (नेपाली)",
    "km": "кхмерский (ខ្មែរ)", "lo": "лаосский (ລາວ)",
    "my": "бирманский (မြန်မာ)", "ka": "грузинский (ქართული)",
    "hy": "армянский (Հայերեն)", "az": "азербайджанский (Azərbaycan)",
    "kk": "казахский (Қазақ)", "ky": "киргизский (Кыргызча)",
    "uz": "узбекский (Oʻzbek)", "tg": "таджикский (Тоҷикӣ)",
    "tk": "туркменский (Türkmen)", "mn": "монгольский (Монгол)",
    "ps": "пушту (پښتو)", "ku": "курдский (Kurdî)", "am": "амхарский (አማርኛ)",
    "ti": "тигринья (ትግርኛ)", "so": "сомалийский (Soomaali)",
    "sw": "суахили (Kiswahili)", "rw": "киньяруанда (Kinyarwanda)",
    "ny": "чичева (Chichewa)", "sn": "шона (Shona)", "nd": "ндебеле (Ndebele)",
    "st": "сесото (Sesotho)", "tn": "тсвана (Setswana)", "ts": "тсонга (Xitsonga)",
    "ss": "свати (siSwati)", "ve": "венда (Tshivenḓa)", "xh": "коса (isiXhosa)",
    "zu": "зулу (isiZulu)", "nr": "ндебеле южный (isiNdebele)",
    "af": "африкаанс (Afrikaans)", "mg": "малагасийский (Malagasy)",
    "ln": "лингала (Lingála)", "kg": "конго (Kikongo)", "lu": "луба (Tshiluba)",
    "rn": "рунди (Kirundi)", "sg": "санго (Sängö)", "ht": "гаитянский креольский (Kreyòl)",
    "fil": "филиппинский (Filipino)", "fj": "фиджийский (Vakaviti)",
    "mi": "маори (Māori)", "sm": "самоанский (Gagana Samoa)",
    "to": "тонганский (Lea faka-Tonga)", "dv": "дивехи (ދިވެހި)",
    "dz": "дзонг-кэ (རྫོང་ཁ)", "lb": "люксембургский (Lëtzebuergesch)",
    "rm": "ретороманский (Rumantsch)", "la": "латынь (Latina)",
    "ay": "аймара (Aymar)", "qu": "кечуа (Runa Simi)", "gn": "гуарани (Avañe'ẽ)",
    "os": "осетинский (Ирон)",
}


def lang_name(code: str) -> str:
    """Код (или несколько через запятую — берётся первый) → название языка."""
    first = (code or "").split(",")[0].strip().lower()
    return _LANG_NAMES.get(first, code)


# ── гео → основной язык (из CSV Документация/Имена-и-валюта-по-гео.csv) ──
_CSV_PATH = BASE_DIR.parents[1] / "Документация" / "Имена-и-валюта-по-гео.csv"
_geo_lang_cache: Optional[dict[str, str]] = None


def load_geo_languages() -> dict[str, str]:
    """{ГЕО: код_основного_языка} из CSV. Первый язык в ячейке — основной."""
    global _geo_lang_cache
    if _geo_lang_cache is not None:
        return _geo_lang_cache
    import csv
    out: dict[str, str] = {}
    try:
        with open(_CSV_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                geo = (row.get("Гео") or "").strip().upper()
                lang = (row.get("Язык") or "").split(",")[0].strip().lower()
                if geo and lang:
                    out[geo] = lang
    except Exception as e:  # noqa: BLE001
        log.warning("Не прочитать CSV языков (%s): %s", _CSV_PATH, e)
    _geo_lang_cache = out
    return out


def lang_for_geo(geo: str) -> Optional[str]:
    """Основной язык гео: сначала CSV, потом geos.lang_html."""
    geo = (geo or "").strip().upper()
    if not geo:
        return None
    csv_lang = load_geo_languages().get(geo)
    if csv_lang:
        return csv_lang
    geos = runners.load_geos()
    return (geos.get(geo, {}) or {}).get("lang_html")


# ── маскирование ─────────────────────────────────────────────────
def mask_text(text: str) -> tuple[str, dict[str, str]]:
    """Заменяет макросы/URL/email на редкие токены ⟦N⟧. → (masked, mapping)."""
    mapping: dict[str, str] = {}
    masked = text
    idx = 0
    for pat in _MASK_PATTERNS:
        def repl(m, _i=[idx]):
            tok = f"⟦{_i[0]}⟧"
            mapping[tok] = m.group(0)
            _i[0] += 1
            return tok
        masked = pat.sub(repl, masked)
        idx = len(mapping)
    return masked, mapping


def unmask_text(text: str, mapping: dict[str, str]) -> str:
    for tok, orig in mapping.items():
        text = text.replace(tok, orig)
    return text


# ── извлечение видимых блоков ────────────────────────────────────
def _translatable(s: str) -> bool:
    s = (s or "").strip()
    if len(s) < 2:
        return False
    # нужна хотя бы одна буква (любой алфавит)
    if not re.search(r"[^\W\d_]", s):
        return False
    # чистый макрос / число / токен — пропускаем
    if re.fullmatch(r"[\d\s.,%:/+\-]+", s):
        return False
    return True


def extract_visible_texts(html: str) -> list[str]:
    """Уникальные видимые тексты: текстовые узлы (вне script/style/коммент.)
    + переводимые атрибуты + value у submit/button. Порядок — по длине убыв.
    (длинные заменяем первыми, чтобы короткие не попортили подстроки)."""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, None] = {}

    skip_parents = {"script", "style", "noscript", "code", "pre"}
    for node in soup.find_all(string=True):
        # Только «чистые» текстовые узлы. Подклассы NavigableString — это
        # комментарии, doctype, CDATA и processing instructions (`<?php … ?>`
        # парсится именно как PI) — переводить их нельзя: правка PHP-кода
        # моделью ломает ленд.
        if type(node) is not NavigableString:
            continue
        parent = node.parent.name if node.parent else ""
        if parent in skip_parents:
            continue
        txt = str(node)
        if _translatable(txt):
            seen.setdefault(txt.strip(), None)

    for tag in soup.find_all(True):
        for attr in _ATTR_TEXTS:
            val = tag.get(attr)
            if isinstance(val, str) and _translatable(val):
                seen.setdefault(val.strip(), None)
        if tag.name == "input" and (tag.get("type") or "").lower() in ("submit", "button"):
            val = tag.get("value")
            if isinstance(val, str) and _translatable(val):
                seen.setdefault(val.strip(), None)

    return sorted(seen.keys(), key=len, reverse=True)


# Столицы по гео — для замены городов на характерные для целевой страны
# (в местном написании). Используется в подсказке переводчику.
COUNTRY_CAPITALS = {
    "MX": "Ciudad de México", "CO": "Bogotá", "PE": "Lima", "CL": "Santiago",
    "AR": "Buenos Aires", "BO": "La Paz", "EC": "Quito", "GT": "Ciudad de Guatemala",
    "DO": "Santo Domingo", "CR": "San José", "PA": "Ciudad de Panamá",
    "PY": "Asunción", "UY": "Montevideo", "VE": "Caracas", "SV": "San Salvador",
    "HN": "Tegucigalpa", "NI": "Managua", "ES": "Madrid",
    "CZ": "Praha", "SK": "Bratislava", "PL": "Warszawa", "HU": "Budapest",
    "RO": "București", "BG": "София", "HR": "Zagreb", "SI": "Ljubljana",
    "RS": "Београд", "GR": "Αθήνα", "IT": "Roma", "PT": "Lisboa",
    "DE": "Berlin", "FR": "Paris", "LT": "Vilnius", "LV": "Rīga", "EE": "Tallinn",
    "TR": "Ankara", "SA": "الرياض", "AE": "أبو ظبي", "EG": "القاهرة",
    "MA": "الرباط", "VN": "Hà Nội", "TH": "กรุงเทพมหานคร", "ID": "Jakarta",
    "MY": "Kuala Lumpur", "PH": "Manila", "IN": "New Delhi",
}


def geo_hint(geo: str, geos: Optional[dict] = None) -> str:
    """Подсказка переводчику для локализации имён/городов под целевое гео.
    Пусто, если гео не задано."""
    geo = (geo or "").strip().upper()
    if not geo:
        return ""
    geos = geos if geos is not None else runners.load_geos()
    info = geos.get(geo, {}) or {}
    country = info.get("country_name") or geo
    capital = COUNTRY_CAPITALS.get(geo, "")
    parts = [f"Целевая страна: {country} ({geo})."]
    parts.append(
        "Имена людей заменяй на типичные/распространённые для этой страны "
        "(чтобы звучали естественно для местного читателя), а НЕ транслитерируй "
        "исходные.")
    if capital:
        parts.append(
            f"Города и адреса заменяй на города этой страны; столица — {capital}.")
    else:
        parts.append("Города заменяй на города этой страны.")
    return " ".join(parts)


# ── перевод батча через AITUNNEL ─────────────────────────────────
_SYS_PROMPT = """\
Ты — профессиональный переводчик рекламных лендингов. ОБЯЗАТЕЛЬНО переведи КАЖДЫЙ
блок на ЦЕЛЕВОЙ язык: {lang}. Даже если исходный язык похож на целевой — всё
равно переведи полностью на {lang}. Сохраняй продающий тон, естественность, регистр.

ЛОКАЛИЗАЦИЯ ПОД ГЕО:
{geo_hint}

СТРОГО:
- Верни ТОЛЬКО JSON: {{"translations": [...]}} — массив той же длины и в том же
  порядке, что и входной массив blocks. Ничего лишнего.
- Каждый элемент translations — это блок, ПЕРЕВЕДЁННЫЙ на {lang}.
- НЕ переводи и сохрани ДОСЛОВНО: токены вида ⟦0⟧ ⟦1⟧ (плейсхолдеры макросов),
  числа и цены, бренды и название продукта, валюты, URL, e-mail, единицы измерения.
- Имена людей и города/адреса — НЕ сохраняй дословно: адаптируй их под целевую
  страну согласно блоку «ЛОКАЛИЗАЦИЯ ПОД ГЕО» выше.
- Не добавляй и не удаляй блоки.
"""


def _parse_translations(content: str) -> Optional[list]:
    """Извлекает массив translations из ответа модели, устойчиво к markdown
    и мусору вокруг JSON. None, если распарсить не удалось."""
    s = (content or "").strip()
    # Снять ```json … ``` обёртку.
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    # Вырезать от первой { до последней } (отбросить пролог/эпилог).
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i:j + 1]
    try:
        data = json.loads(s)
    except ValueError:
        return None
    return data.get("translations") or data.get("blocks") or []


def _translate_one_batch(chunk: list[str], lang_full: str, client, model: str,
                         geo_hint_text: str = "") -> dict[str, str]:
    """Переводит один батч блоков. → {original: translated}."""
    masks: list[dict] = []
    masked_blocks: list[str] = []
    for b in chunk:
        mb, mp = mask_text(b)
        masked_blocks.append(mb)
        masks.append(mp)

    hint = geo_hint_text or "Гео не задано — имена и города оставь нейтральными."
    messages = [
        {"role": "system", "content": _SYS_PROMPT.format(lang=lang_full, geo_hint=hint)},
        {"role": "user", "content": json.dumps({"blocks": masked_blocks}, ensure_ascii=False)},
    ]
    resp = client.chat(messages, model=model, temperature=0.2,
                       max_tokens=MAX_TOKENS, response_format={"type": "json_object"})
    content = (resp["message"].get("content") or "").strip()
    translations = _parse_translations(content)
    if translations is None:
        if resp.get("finish_reason") == "length":
            raise ValueError("Ответ модели обрезан (слишком длинный батч).")
        raise ValueError(f"Модель вернула не-JSON: {content[:200]}")
    if len(translations) != len(chunk):
        raise ValueError(
            f"Несовпадение числа блоков: ждали {len(chunk)}, пришло {len(translations)}")

    out: dict[str, str] = {}
    for orig, mp, tr in zip(chunk, masks, translations):
        tr = str(tr)
        for tok in mp:
            if tok not in tr:
                log.warning("Потеряна маска %s в переводе блока %r", tok, orig[:40])
        out[orig] = unmask_text(tr, mp)
    return out


def _translate_batch_resilient(chunk: list[str], lang_full: str, client, model: str,
                               geo_hint_text: str = "") -> dict[str, str]:
    """Перевод батча, устойчивый к сбоям модели. Раньше упавший батч молча
    терялся целиком (25 блоков оставались без перевода — «перевод затронул не
    весь текст»). Теперь: обрезанный ответ → сразу делим батч пополам; прочие
    ошибки → один повтор, затем деление; одиночный блок не перевёлся → теряем
    ТОЛЬКО его (с warning), остальное переводится."""
    def _once() -> dict[str, str]:
        return _translate_one_batch(chunk, lang_full, client, model, geo_hint_text)

    try:
        return _once()
    except Exception as e1:  # noqa: BLE001
        truncated = "обрезан" in str(e1)
        if not truncated and len(chunk) > 0:
            try:
                return _once()  # повтор: транзиентный сбой сети/модели
            except Exception:  # noqa: BLE001
                pass
        if len(chunk) == 1:
            log.warning("Блок не переведён (%s): %r", e1, chunk[0][:60])
            return {}
        mid = len(chunk) // 2
        out = _translate_batch_resilient(chunk[:mid], lang_full, client, model, geo_hint_text)
        out.update(_translate_batch_resilient(chunk[mid:], lang_full, client, model, geo_hint_text))
        return out


def translate_blocks(blocks: list[str], lang: str, client, model: str,
                     geo: str = "") -> dict[str, str]:
    """Переводит блоки последовательно (для CLI/агента). → {original: translated}."""
    lang_full = lang_name(lang)
    hint = geo_hint(geo)
    result: dict[str, str] = {}
    for start in range(0, len(blocks), BATCH_SIZE):
        result.update(_translate_batch_resilient(blocks[start:start + BATCH_SIZE],
                                                 lang_full, client, model, hint))
    return result


# ── применение к файлам output-архива ───────────────────────────
def _output_zip(sid: str, lid: str) -> Path:
    mgr = get_manager()
    s = mgr.get(sid)
    if s is None:
        raise ValueError(f"Сессия {sid} не найдена")
    ls = s.landers.get(lid)
    if ls is None:
        raise ValueError(f"Ленд {lid} не найден")
    if not ls.output_name:
        raise ValueError("Ленд ещё не адаптирован — нет выходного архива")
    p = OUTPUTS / ls.output_name
    if not p.exists():
        raise ValueError("Выходной архив не найден")
    return p


def _snapshot_translation(sid: str, lid: str, lang: str) -> None:
    """Снимок версии после применения перевода — для отката (best-effort)."""
    try:
        get_manager()._snapshot_output(sid, lid, f"Перевод ({lang})")
    except Exception:  # noqa: BLE001
        log.warning("Не снять снимок версии после перевода %s/%s", sid, lid)


def _journal_translation(sid: str, lid: str, lang: str,
                         translations: dict[str, str]) -> None:
    """Кэширует словарь перевода и пишет операцию в журнал пост-правок ленда —
    после переадаптации перевод переприменяется из кэша БЕЗ повторного похода
    в нейросеть (см. SessionManager.reapply_post_edits)."""
    useful = {o: t for o, t in translations.items() if t.strip() != o.strip()}
    if not useful:
        return
    try:
        import time as _time
        mgr = get_manager()
        d = mgr.dir / sid / "translations"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{lid}_{int(_time.time())}.json"
        f.write_text(json.dumps(useful, ensure_ascii=False), encoding="utf-8")
        mgr.journal_append(sid, lid, {"type": "translation", "lang": lang,
                                      "mapping_file": str(f)})
    except Exception:  # noqa: BLE001
        log.exception("Не записан кэш перевода %s/%s", sid, lid)


# Языки с письмом справа налево.
RTL_LANGS = {"ar", "he", "fa", "ur", "ps", "dv", "ku", "sd", "ug", "yi"}


def is_rtl(code: str) -> bool:
    return (code or "").split(",")[0].strip().lower() in RTL_LANGS


def ensure_rtl_html(html: str, lang: str) -> str:
    """Выставляет dir=\"rtl\" (и lang) на <html> для RTL-языков. Если <html>
    нет — на <body>. Уже прописанный dir=\"ltr\" ПЕРЕЗАПИСЫВАЕТСЯ: доноры,
    скачанные с сайтов, часто несут жёсткий ltr, и на арабском вёрстка едет."""
    code = (lang or "").split(",")[0].strip().lower()

    def add_dir(tag: str) -> str:
        if re.search(r"\sdir\s*=", tag, re.I):
            return re.sub(r'(\sdir\s*=\s*["\']?)[^"\'\s>]*', r"\1rtl", tag,
                          count=1, flags=re.I)
        ins = ' dir="rtl"'
        if not re.search(r"\slang\s*=", tag, re.I):
            ins += f' lang="{code}"'
        return tag[:-1] + ins + tag[-1]

    if re.search(r"<html\b", html, re.I):
        return re.sub(r"<html\b[^>]*>", lambda m: add_dir(m.group(0)), html, count=1, flags=re.I)
    if re.search(r"<body\b", html, re.I):
        return re.sub(r"<body\b[^>]*>", lambda m: add_dir(m.group(0)), html, count=1, flags=re.I)
    return html


def _entity_flex_pattern(orig: str) -> "re.Pattern[str]":
    """Regex, где каждый символ оригинала матчится и как символ, и как его
    HTML-entity. BS4 при извлечении декодирует entities (&nbsp;→\xa0,
    &rsquo;→’), а в сыром файле остаётся entity — дословный str.replace не
    находил такие блоки, и они оставались непереведёнными (кейс 20772:
    51 блок с &nbsp;)."""
    import html.entities
    parts: list[str] = []
    for ch in orig:
        alts = [re.escape(ch)]
        cp = ord(ch)
        name = html.entities.codepoint2name.get(cp)
        if name:
            alts.append(f"&{name};")
        if name or ch in "&<>\"'":
            alts.append(f"&#{cp};")
            alts.append(f"&#[xX]0*{cp:x};")
        parts.append("(?:%s)" % "|".join(alts) if len(alts) > 1 else alts[0])
    return re.compile("".join(parts))


# Атрибуты, чьё ЦЕЛОЕ значение можно заменять однословным переводом.
_SAFE_ATTR_RE_TPL = r'((?:placeholder|alt|title|aria-label)\s*=\s*["\']){0}(["\'])'


def _apply_single_word(content: str, orig: str, tr: str) -> tuple[str, int]:
    """Безопасная замена ОДНОСЛОВНОГО блока: только целые значения переводимых
    атрибутов, value у submit/button и текстовые узлы (>слово<).

    Дословный replace для таких блоков ломал разметку и код: блок «name»
    (aria-label донора) превращал `name="country"` в `όνομα="country"`,
    CSS-классы `ingredients__name` и JS-код виджета — конверсия падала
    (кейс 20683 GR)."""
    n = 0
    esc = re.escape(orig)
    # 1) целые значения переводимых атрибутов
    content, k = re.subn(_SAFE_ATTR_RE_TPL.format(esc),
                         lambda m: m.group(1) + tr + m.group(2),
                         content, flags=re.IGNORECASE)
    n += k
    # 2) value целиком — только у submit/button/image инпутов
    def _sub_btn(m: re.Match) -> str:
        nonlocal n
        tag = m.group(0)
        if not re.search(r'type\s*=\s*["\'](submit|button|image)["\']', tag, re.I):
            return tag
        new_tag, k2 = re.subn(rf'(value\s*=\s*["\']){esc}(["\'])',
                              lambda mm: mm.group(1) + tr + mm.group(2), tag)
        n += k2
        return new_tag
    content = re.sub(r"<input\b[^>]*>", _sub_btn, content)
    # 3) текстовые узлы: слово — единственное содержимое между тегами
    content, k = re.subn(rf"(>\s*){esc}(\s*<)",
                         lambda m: m.group(1) + tr + m.group(2), content)
    n += k
    return content, n


def apply_to_text(content: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Точечная замена original→translated (по убыванию длины оригинала).

    Однословные блоки применяются ТОЛЬКО в безопасных контекстах (атрибуты
    перевода / текстовые узлы) — глобальный replace короткого слова ломает
    name=/class=/JS (см. _apply_single_word)."""
    n = 0
    for orig in sorted(mapping, key=len, reverse=True):
        tr = mapping[orig]
        if orig == tr:
            continue
        if not re.search(r"\s", orig):
            content, k = _apply_single_word(content, orig, tr)
            if k:
                n += 1
            continue
        if orig in content:
            content = content.replace(orig, tr)
            n += 1
            continue
        # Фолбэк: в файле оригинал с HTML-entities (&nbsp; и т.п.).
        try:
            content, k = _entity_flex_pattern(orig).subn(lambda _m: tr, content)
        except re.error:
            k = 0
        if k:
            n += 1
    return content, n


def _apply_to_zip(zip_path: Path, file_texts: dict[str, str],
                  translations: dict[str, str], lang: str,
                  overrides: Optional[dict[str, str]] = None) -> int:
    """Пересобирает output-архив с переводом (+ dir=rtl для RTL-языков).

    overrides — готовые новые тексты файлов (VSL config.php пересобирается
    сериализацией конфига, а не текстовой заменой: в PHP-строках экранированные
    кавычки, дословный replace их не находит)."""
    import tempfile
    rtl = is_rtl(lang)
    changed = 0
    fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(zip_path.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "r") as zin, \
             zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if overrides and item.filename in overrides:
                    new = overrides[item.filename]
                    if new != data.decode("utf-8", errors="replace"):
                        changed += 1
                    data = new.encode("utf-8")
                elif item.filename in file_texts:
                    text = data.decode("utf-8", errors="replace")
                    text, n = apply_to_text(text, translations)
                    if rtl:
                        text = ensure_rtl_html(text, lang)
                    if n or rtl:
                        data = text.encode("utf-8")
                        if n:
                            changed += 1
                zout.writestr(item, data)
        os.replace(tmp, zip_path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    return changed


# ── VSL: перевод строк config.php ────────────────────────────────
def _vsl_config_info(zip_path: Path) -> Optional[tuple[str, str, dict]]:
    """(member, text, cfg) config.php архива; None — не VSL-ленд.

    Видимые тексты VSL живут в PHP-массиве $config (BS4 их не видит — это
    processing instruction), поэтому конфиг переводится отдельной веткой."""
    from services.vsl import _find_config_member, parse_config_php
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member = _find_config_member(zf)
            text = zf.read(member).decode("utf-8", errors="replace")
        return member, text, parse_config_php(text)
    except Exception:  # noqa: BLE001
        return None


def target_lang_for(sid: str, lid: str) -> str:
    """Язык целевого гео ленда (код, напр. 'es'/'ar'/'ur') — из CSV/geos."""
    mgr = get_manager()
    s = mgr.get(sid)
    ls = s.landers.get(lid) if s else None
    from services.session import parse_target_offer
    geos = runners.load_geos()
    offer = s.lander_offer(ls) if (s and ls) else ""
    geo = parse_target_offer(offer, geos).get("geo_id", "")
    return lang_for_geo(geo) or "es"


def target_geo_for(sid: str, lid: str) -> str:
    """Целевое ГЕО ленда (код, напр. 'CZ'/'HU') — с учётом подмены группы."""
    mgr = get_manager()
    s = mgr.get(sid)
    ls = s.landers.get(lid) if s else None
    from services.session import parse_target_offer
    offer = s.lander_offer(ls) if (s and ls) else ""
    return parse_target_offer(offer, runners.load_geos()).get("geo_id", "")


def list_languages() -> list[dict]:
    """Все поддерживаемые языки (код + название) — для выпадашки в UI."""
    return [{"code": code, "name": name}
            for code, name in sorted(_LANG_NAMES.items(), key=lambda kv: kv[1])]


def translate_lander(sid: str, lid: str, *, target_lang: Optional[str] = None,
                     execute: bool = False) -> dict:
    """Переводит видимый текст адаптированного ленда.

    execute=False — превью (дифф, без записи). execute=True — применить в
    output-архив. Возвращает {lang, diff:[{file,original,translated}], applied}.
    """
    from connectors.aitunnel import client_from_env
    zip_path = _output_zip(sid, lid)
    lang = (target_lang or target_lang_for(sid, lid)).strip()

    client = client_from_env()
    if client is None:
        raise ValueError("AITUNNEL не настроен — задай AITUNNEL_API_KEY в .env")
    model = translate_model()

    # 1) Извлечь блоки из всех текстовых файлов (с дедупликацией по всему ленду).
    with zipfile.ZipFile(zip_path, "r") as zf:
        text_members = [n for n in zf.namelist()
                        if Path(n).suffix.lower() in TEXT_FILE_EXT]
        file_texts = {m: zf.read(m).decode("utf-8", errors="replace")
                      for m in text_members}

    # VSL: строки config.php — отдельной веткой (parse → walk → serialize).
    cfg_info = _vsl_config_info(zip_path)
    cfg_blocks: list[str] = []
    if cfg_info:
        from services.vsl import config_translatable_strings
        file_texts.pop(cfg_info[0], None)
        cfg_blocks = config_translatable_strings(cfg_info[2])

    all_blocks: dict[str, None] = {}
    per_file_blocks: dict[str, list[str]] = {}
    for m, content in file_texts.items():
        blocks = extract_visible_texts(content)
        per_file_blocks[m] = blocks
        for b in blocks:
            all_blocks.setdefault(b, None)
    if cfg_blocks:
        per_file_blocks[cfg_info[0]] = cfg_blocks
        for b in cfg_blocks:
            all_blocks.setdefault(b, None)

    if not all_blocks:
        return {"lang": lang, "diff": [], "applied": 0,
                "note": "Видимый текст не найден"}

    # 2) Перевести один раз каждый уникальный блок (с локализацией под гео).
    geo = target_geo_for(sid, lid)
    translations = translate_blocks(list(all_blocks.keys()), lang, client, model, geo)

    # 3) Дифф (только реально изменившиеся).
    diff = []
    for m, blocks in per_file_blocks.items():
        for b in blocks:
            tr = translations.get(b)
            if tr and tr.strip() != b.strip():
                diff.append({"file": m, "original": b, "translated": tr})

    if not execute:
        return {"lang": lang, "model": model, "diff": diff,
                "applied": 0, "mode": "preview"}

    overrides: dict[str, str] = {}
    if cfg_info and cfg_blocks:
        from services.vsl import config_apply_translations, replace_config_php
        overrides[cfg_info[0]] = replace_config_php(
            cfg_info[1], config_apply_translations(cfg_info[2], translations))
    # Применяем только к файлам, где извлеклись блоки: файлы без видимого
    # текста (api.php и другой чистый PHP) словарём не трогаем — замена там
    # ломала код ($dimensionName). Для RTL нужен dir= во всех html-файлах.
    apply_files = (file_texts if is_rtl(lang) else
                   {m: t for m, t in file_texts.items() if per_file_blocks.get(m)})
    changed_files = _apply_to_zip(zip_path, apply_files, translations, lang, overrides)
    _snapshot_translation(sid, lid, lang)
    _journal_translation(sid, lid, lang, translations)
    return {"lang": lang, "model": model, "diff": diff,
            "applied": changed_files, "mode": "executed"}


def translate_lander_stream(sid: str, lid: str, *, target_lang: Optional[str] = None):
    """Стриминговый перевод: параллельные батчи + события прогресса, СРАЗУ
    применяет к output-архиву (без отдельного подтверждения).

    События: start / block (переведённый блок) / progress / done / error.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from connectors.aitunnel import client_from_env
    try:
        zip_path = _output_zip(sid, lid)
        lang = (target_lang or target_lang_for(sid, lid)).strip()
        client = client_from_env()
        if client is None:
            yield {"type": "error", "error": "AITUNNEL не настроен (AITUNNEL_API_KEY)"}
            return
        model = translate_model()
        lang_full = lang_name(lang)
        geo_hint_text = geo_hint(target_geo_for(sid, lid))

        with zipfile.ZipFile(zip_path, "r") as zf:
            members = [n for n in zf.namelist() if Path(n).suffix.lower() in TEXT_FILE_EXT]
            file_texts = {m: zf.read(m).decode("utf-8", errors="replace") for m in members}

        # VSL: строки config.php — отдельной веткой (parse → walk → serialize).
        cfg_info = _vsl_config_info(zip_path)
        cfg_blocks: list[str] = []
        if cfg_info:
            from services.vsl import config_translatable_strings
            file_texts.pop(cfg_info[0], None)
            cfg_blocks = config_translatable_strings(cfg_info[2])

        all_blocks: dict[str, None] = {}
        files_with_blocks: set[str] = set()
        for m, content in file_texts.items():
            found = extract_visible_texts(content)
            if found:
                files_with_blocks.add(m)
            for b in found:
                all_blocks.setdefault(b, None)
        for b in cfg_blocks:
            all_blocks.setdefault(b, None)
        blocks = list(all_blocks.keys())
        total = len(blocks)
        yield {"type": "start", "lang": lang, "lang_name": lang_full,
               "model": model, "total": total, "rtl": is_rtl(lang)}
        if total == 0:
            yield {"type": "done", "applied": 0, "changed": 0}
            return

        batches = [blocks[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
        translations: dict[str, str] = {}
        done = 0
        # Параллельные запросы к aitunnel — кратно быстрее (было ~2 мин).
        # НЕ context-manager: при обрыве клиента (кнопка «Стоп» закрывает SSE)
        # GeneratorExit прилетает в yield — гасим пул БЕЗ ожидания и НЕ применяем
        # перевод к архиву (прерванный перевод не должен трогать ленд).
        ex = ThreadPoolExecutor(max_workers=5)
        try:
            futs = {ex.submit(_translate_batch_resilient, b, lang_full, client, model,
                              geo_hint_text): b
                    for b in batches}
            for fut in as_completed(futs):
                batch = futs[fut]
                res = fut.result()  # resilient: не бросает, возвращает что перевелось
                translations.update(res)
                done += len(batch)
                lost = len(batch) - len(res)
                if lost:
                    yield {"type": "progress", "done": done, "total": total,
                           "warn": f"{lost} блок(ов) не переведено после ретраев"}
                changed = [{"original": o, "translated": t}
                           for o, t in res.items() if t.strip() != o.strip()]
                yield {"type": "block", "items": changed, "done": done, "total": total}
        except GeneratorExit:
            ex.shutdown(wait=False, cancel_futures=True)
            log.info("Перевод %s/%s прерван пользователем — изменения НЕ применены",
                     sid, lid)
            raise
        ex.shutdown(wait=True)

        # Применяем (+RTL) сразу — только если дошли до конца без прерывания.
        overrides: dict[str, str] = {}
        if cfg_info and cfg_blocks:
            from services.vsl import config_apply_translations, replace_config_php
            overrides[cfg_info[0]] = replace_config_php(
                cfg_info[1], config_apply_translations(cfg_info[2], translations))
        # Только файлы с извлечёнными блоками — не трогаем чистый PHP (api.php).
        apply_files = (file_texts if is_rtl(lang) else
                       {m: t for m, t in file_texts.items() if m in files_with_blocks})
        changed_files = _apply_to_zip(zip_path, apply_files, translations, lang, overrides)
        _snapshot_translation(sid, lid, lang)
        _journal_translation(sid, lid, lang, translations)
        yield {"type": "done", "applied": changed_files, "translated": len(translations)}
    except Exception as e:  # noqa: BLE001
        log.exception("Сбой стрим-перевода")
        yield {"type": "error", "error": str(e)}


# ── CLI ──────────────────────────────────────────────────────────
def _main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Перевод ленда через AITUNNEL.")
    ap.add_argument("sid")
    ap.add_argument("lid")
    ap.add_argument("--lang", default=None, help="Целевой язык (иначе по гео)")
    ap.add_argument("--execute", action="store_true", help="Применить (иначе превью)")
    args = ap.parse_args()
    res = translate_lander(args.sid, args.lid, target_lang=args.lang, execute=args.execute)
    print(f"Язык: {res['lang']} | блоков в диффе: {len(res['diff'])} | "
          f"применено файлов: {res['applied']}")
    for d in res["diff"][:30]:
        print(f"  [{d['file']}] {d['original'][:50]!r} → {d['translated'][:50]!r}")


if __name__ == "__main__":
    _main()
