# -*- coding: utf-8 -*-
"""Библиотека VSL-комментариев по вертикалям.

Комментарии VSL-лендов живут в `config.php::$config['fakeChat']['preparedComments']`
(name/text/likes/avatar/appearAtPercent/replies). Библиотека хранит наборы
комментариев ПО ВЕРТИКАЛЯМ (DI диабет, PR простатит, PT потенция и т.д.):

- сбор (harvest): перебираем VSL-офферы Keitaro, качаем архивы, забираем
  preparedComments (пропуская ленды с выключенным/пустым fakeChat);
- подстановка: пользователь выбирает вертикаль → в конфиг ленда вставляются
  ВСЕ сохранённые комментарии вертикали (дедуп, пересчёт index/appearAtPercent);
- перевод: имя + текст комментариев через deepseek (TRANSLATE_MODEL), имена
  локализуются под гео (geo_hint), лайки/аватары/тайминги не трогаются.

Файлы: storage/vsl_comments/<CODE>.json —
{"vertical": "DIABETES", "sets": [{offer_id, offer_name, geo, lang, comments}]}
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator, Optional

from services.session import VERTICAL_CODE_TO_FULL

log = logging.getLogger("vsl_comments")

BASE_DIR = Path(__file__).resolve().parents[1]
LIB_DIR = BASE_DIR / "storage" / "vsl_comments"

# ПОЛНОЕ имя вертикали → код (обратный к VERTICAL_CODE_TO_FULL; при дублях
# VARICOSIS выигрывает VA — так пишут в скобках офферов).
FULL_TO_CODE: dict[str, str] = {}
for _code, _full in VERTICAL_CODE_TO_FULL.items():
    FULL_TO_CODE.setdefault(_full, _code)
FULL_TO_CODE["VARICOSIS"] = "VA"

_BRACKET_RE = re.compile(r"\[([A-Z][A-Z ]+?)-([A-Z]{2})\]")
_VSL_LANG_RE = re.compile(r"\[vsl\s+([a-z]{2})", re.I)


# ── хранилище ────────────────────────────────────────────────────
def _lib_path(code: str) -> Path:
    return LIB_DIR / f"{code.upper()}.json"


def _load(code: str) -> dict:
    p = _lib_path(code)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.exception("Битый файл библиотеки %s", p)
    return {"vertical": VERTICAL_CODE_TO_FULL.get(code.upper(), code.upper()),
            "sets": []}


def _save(code: str, data: dict) -> None:
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    _lib_path(code).write_text(json.dumps(data, ensure_ascii=False, indent=1),
                               encoding="utf-8")


def _comments_hash(comments: list) -> str:
    return hashlib.md5(json.dumps(comments, ensure_ascii=False,
                                  sort_keys=True).encode()).hexdigest()


def add_set(code: str, entry: dict) -> bool:
    """Добавляет набор комментариев в библиотеку вертикали. False = дубль."""
    code = code.upper()
    data = _load(code)
    h = _comments_hash(entry.get("comments") or [])
    if any(_comments_hash(s.get("comments") or []) == h for s in data["sets"]):
        return False
    data["sets"].append(entry)
    _save(code, data)
    return True


def list_library() -> list[dict]:
    """Сводка библиотеки: вертикаль → наборов/комментариев."""
    out = []
    for code in sorted(VERTICAL_CODE_TO_FULL):
        data = _load(code)
        if not data["sets"]:
            continue
        out.append({
            "code": code,
            "vertical": data["vertical"],
            "sets": len(data["sets"]),
            "comments": sum(len(s.get("comments") or []) for s in data["sets"]),
            "langs": sorted({s.get("lang") or "?" for s in data["sets"]}),
        })
    return out


def get_sets(code: str) -> list[dict]:
    return _load(code)["sets"]


# ── извлечение комментариев из архива ────────────────────────────
def extract_comments_from_zip(zip_path: Path) -> Optional[list[dict]]:
    """preparedComments из config.php архива. None — VSL без комментариев
    (fakeChat выключен/пуст) или это не VSL."""
    from services.vsl import parse_config_php
    try:
        with zipfile.ZipFile(zip_path) as zf:
            member = next((n for n in zf.namelist()
                           if n.endswith("config.php")), None)
            if member is None:
                return None
            cfg = parse_config_php(zf.read(member).decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return None
    chat = cfg.get("fakeChat") or {}
    comments = chat.get("preparedComments") or []
    if not chat.get("enabled", True) or not comments:
        return None
    # только валидные записи с текстом
    comments = [c for c in comments if isinstance(c, dict) and c.get("text")]
    return comments or None


def parse_offer_vertical(name: str) -> Optional[tuple[str, str]]:
    """(код вертикали, гео) из названия оффера по скобке [VERTICAL-GEO]."""
    m = _BRACKET_RE.search(name or "")
    if not m:
        return None
    code = FULL_TO_CODE.get(m.group(1).strip())
    return (code, m.group(2)) if code else None


def offer_lang(name: str) -> str:
    m = _VSL_LANG_RE.search(name or "")
    return m.group(1).lower() if m else ""


# ── сбор из Keitaro ──────────────────────────────────────────────
def harvest_stream(max_per_vertical: int = 3) -> Iterator[dict]:
    """Собирает комментарии из VSL-офферов Keitaro (SSE-события).

    По каждой вертикали качаем до `max_per_vertical` НАБОРОВ с комментариями,
    предпочитая разные продукты (уникальные ленды). Ленды с выключенными
    комментариями пропускаются (но считаются проверенными).
    События: step / vertical_done / done / error.
    """
    from connectors.keitaro import client_from_env

    try:
        yield {"type": "step", "message": "Открываю Keitaro, ищу VSL-офферы…"}
        with client_from_env() as kt:
            offers = kt.list_offers("vsl")
            yield {"type": "step",
                   "message": f"Найдено офферов по фильтру 'vsl': {len(offers)}"}

            # Группируем по вертикали; уникальность ленда = продукт (текст до '[').
            by_vert: dict[str, list[dict]] = {}
            for o in offers:
                pv = parse_offer_vertical(o["name"])
                if not pv:
                    continue
                code, geo = pv
                product = re.sub(r"^\s*\d+\s*", "", o["name"].split("[")[0]).strip().lower()
                by_vert.setdefault(code, []).append(
                    {**o, "geo": geo, "product": product,
                     "lang": offer_lang(o["name"])})

            summary: dict[str, dict] = {}
            tmp_dir = Path(tempfile.mkdtemp(prefix="vsl_harvest_"))
            for code in sorted(by_vert):
                cands = by_vert[code]
                # уже сохранённые наборы вертикали — доподбираем до лимита
                have = len(get_sets(code))
                got, checked, seen_products = 0, 0, set()
                yield {"type": "step",
                       "message": f"[{code}] {VERTICAL_CODE_TO_FULL.get(code, code)}: "
                                  f"кандидатов {len(cands)}, в библиотеке наборов {have}"}
                for o in cands:
                    if have + got >= max_per_vertical:
                        break
                    if o["product"] in seen_products:
                        continue  # уникальные ленды: один продукт — один заход
                    seen_products.add(o["product"])
                    checked += 1
                    try:
                        zp = kt.download_offer(o["id"], tmp_dir)
                    except Exception as e:  # noqa: BLE001
                        yield {"type": "step",
                               "message": f"[{code}] {o['id']}: не скачался ({str(e)[:60]})"}
                        continue
                    comments = extract_comments_from_zip(Path(zp))
                    Path(zp).unlink(missing_ok=True)
                    if not comments:
                        yield {"type": "step",
                               "message": f"[{code}] {o['id']} {o['name'][:50]}: "
                                          "комментарии выключены/нет — пропуск"}
                        continue
                    fresh = add_set(code, {
                        "offer_id": o["id"], "offer_name": o["name"],
                        "geo": o["geo"], "lang": o["lang"],
                        "comments": comments,
                    })
                    if fresh:
                        got += 1
                        yield {"type": "step",
                               "message": f"[{code}] {o['id']}: +{len(comments)} "
                                          f"комментариев ({o['lang'] or '?'})"}
                    else:
                        yield {"type": "step",
                               "message": f"[{code}] {o['id']}: дубль набора — пропуск"}
                summary[code] = {"checked": checked, "added": got,
                                 "total_sets": len(get_sets(code))}
                yield {"type": "vertical_done", "code": code, **summary[code]}
        yield {"type": "done", "summary": summary, "library": list_library()}
    except Exception as e:  # noqa: BLE001
        log.exception("Сбой harvest VSL-комментариев")
        yield {"type": "error", "error": str(e)}


# ── подстановка в ленд ───────────────────────────────────────────
_AVATARS = [f"avatars/{i}.jpeg" for i in range(1, 15)]
_AVATAR_RE = re.compile(r"(?:^|/)(avatars/[^/]+\.(?:jpe?g|png|webp|gif))$", re.I)


def _available_avatars(sid: str, lid: str) -> list[str]:
    """Аватарки, реально лежащие в архиве ленда (пути как в конфиге:
    'avatars/N.jpeg'). Отсортированы по числу в имени."""
    from services.vsl import _zip_for_read
    found: set[str] = set()
    try:
        with zipfile.ZipFile(_zip_for_read(sid, lid)) as zf:
            for n in zf.namelist():
                m = _AVATAR_RE.search(n.replace("\\", "/"))
                if m:
                    found.add(m.group(1))
    except Exception:  # noqa: BLE001
        log.exception("Не прочитаны аватарки ленда %s/%s", sid, lid)

    def _key(p: str) -> tuple:
        m = re.search(r"(\d+)", Path(p).stem)
        return (0, int(m.group(1))) if m else (1, p)
    return sorted(found, key=_key)


def _respread(comments: list[dict], avatars: list[str]) -> list[dict]:
    """Пересчитывает index/appearAtPercent (равномерно 5..95%) и раздаёт
    КАЖДОМУ комментарию/ответу уникальную СУЩЕСТВУЮЩУЮ аватарку из `avatars`.
    Комментариев больше, чем аватарок, быть не должно (обрезаем ДО вызова)."""
    pool = list(avatars)
    n = len(comments)
    out = []
    for i, c in enumerate(comments):
        c = dict(c)
        c["index"] = i
        c["appearAtPercent"] = 5 + round(i * (90 / max(1, n - 1))) if n > 1 else 5
        c["avatar"] = pool.pop(0) if pool else _AVATARS[i % len(_AVATARS)]
        replies = []
        for j, r in enumerate(c.get("replies") or []):
            r = dict(r)
            r["index"] = j
            r["avatar"] = pool.pop(0) if pool else _AVATARS[(i + j + 1) % len(_AVATARS)]
            replies.append(r)
        c["replies"] = replies
        out.append(c)
    return out


# Названия стран на испанском — комментарии собраны с лендов других гео и
# упоминают СВОЮ страну («En Chile cuesta encontrar…»); при подстановке на
# ленд другого испаноязычного гео меняем на целевую детерминированно.
_COUNTRY_ES = {
    "MX": "México", "CL": "Chile", "PE": "Perú", "CO": "Colombia",
    "AR": "Argentina", "BO": "Bolivia", "EC": "Ecuador", "GT": "Guatemala",
    "PY": "Paraguay", "UY": "Uruguay", "VE": "Venezuela", "CR": "Costa Rica",
    "PA": "Panamá", "DO": "República Dominicana", "SV": "El Salvador",
    "HN": "Honduras", "NI": "Nicaragua", "ES": "España", "CU": "Cuba",
}


def _set_product(offer_name: str) -> str:
    """Продукт ленда-источника из названия оффера ('20123 Hondrotex [JOINT-CL]…'
    → 'Hondrotex') — для замены на целевой продукт в текстах комментариев."""
    return re.sub(r"^\s*\d+\s*", "", (offer_name or "").split("[")[0]).strip()


def _lander_target(sid: str, lid: str) -> tuple[str, str]:
    """(целевой продукт, целевое гео) ленда — из его оффера/группы."""
    from services.session import get_manager, parse_target_offer
    from utils import runners
    mgr = get_manager()
    s, ls = mgr._get_lander(sid, lid)
    parsed = parse_target_offer(s.lander_offer(ls), runners.load_geos())
    return (parsed.get("product_search") or parsed.get("product") or "",
            (parsed.get("geo_id") or "").upper())


def _localize_comment(c: dict, reps: list[tuple[re.Pattern, str]]) -> dict:
    """Применяет замены (продукт/страна источника → целевые) к тексту
    комментария и его ответов. Имя не трогаем (там продуктов не бывает)."""
    if not reps:
        return c
    c = dict(c)
    txt = c.get("text") or ""
    for pat, repl in reps:
        txt = pat.sub(repl, txt)
    c["text"] = txt
    if c.get("replies"):
        c["replies"] = [_localize_comment(r, reps) for r in c["replies"]]
    return c


def apply_comments(sid: str, lid: str, code: str) -> dict:
    """Вставляет в конфиг ленда сохранённые комментарии вертикали `code`
    (дедуп по имя+текст, пересчёт таймингов), включает fakeChat.

    Комментарии из библиотеки упоминают ЧУЖОЙ продукт и ЧУЖУЮ страну (набор
    собран с ленда Hondrotex CL, а подставляем в Vitaflex PE) — продукт и
    испаноязычное название страны заменяются на целевые при подстановке.

    Комментариев НЕ больше, чем аватарок в архиве ленда: каждый комментарий
    и ответ получает уникальную существующую аватарку — иначе половина
    комментариев оставалась с битой картинкой."""
    from services.vsl import read_config, write_config
    sets = get_sets(code)
    if not sets:
        raise ValueError(f"В библиотеке нет комментариев вертикали {code}")
    target_product, target_geo = _lander_target(sid, lid)
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for s in sets:
        reps: list[tuple[re.Pattern, str]] = []
        src_product = _set_product(s.get("offer_name") or "")
        if (target_product and src_product
                and src_product.lower() != target_product.lower()):
            reps.append((re.compile(rf"(?<!\w){re.escape(src_product)}(?!\w)",
                                    re.IGNORECASE), target_product))
        src_country = _COUNTRY_ES.get((s.get("geo") or "").upper(), "")
        dst_country = _COUNTRY_ES.get(target_geo, "")
        if src_country and dst_country and src_country != dst_country:
            reps.append((re.compile(rf"(?<!\w){re.escape(src_country)}(?!\w)"),
                         dst_country))
        for c in s.get("comments") or []:
            c = _localize_comment(c, reps)
            key = ((c.get("name") or "").strip().lower(),
                   (c.get("text") or "").strip().lower())
            if key in seen:
                continue
            seen.add(key)
            merged.append(c)

    # Бюджет = число аватарок ленда; ответы тоже расходуют бюджет.
    avatars = _available_avatars(sid, lid) or list(_AVATARS)
    budget = len(avatars)
    capped: list[dict] = []
    used = 0
    for c in merged:
        replies = list(c.get("replies") or [])
        need = 1 + len(replies)
        if used + need > budget:
            trimmed = budget - used - 1  # сколько ответов влезает
            if trimmed < 0:
                break  # сам комментарий уже не помещается
            c = {**c, "replies": replies[:trimmed]}
            need = 1 + trimmed
        capped.append(c)
        used += need
        if used >= budget:
            break
    merged = _respread(capped, avatars)

    cfg = read_config(sid, lid)["config"]
    chat = dict(cfg.get("fakeChat") or {})
    chat["enabled"] = True
    chat["preparedComments"] = merged
    cfg["fakeChat"] = chat
    write_config(sid, lid, cfg)
    log.info("VSL-комментарии %s (%d шт., аватарок %d) вставлены в %s/%s",
             code, len(merged), budget, sid, lid)
    return {"applied": len(merged), "sets": len(sets), "vertical": code,
            "avatars": budget, "skipped": max(0, len(seen) - len(merged))}


# ── перевод (deepseek) ───────────────────────────────────────────
def translate_comments(sid: str, lid: str,
                       target_lang: Optional[str] = None) -> dict:
    """Переводит имя+текст комментариев конфига через deepseek (TRANSLATE_MODEL).

    Имена локализуются под гео ленда (geo_hint: типичные для страны имена).
    Остальные поля (лайки/аватары/тайминги/index) не трогаются.
    """
    from connectors.aitunnel import client_from_env
    from services.translate import (target_geo_for, target_lang_for,
                                    translate_blocks, translate_model)
    from services.vsl import read_config, write_config

    client = client_from_env()
    if client is None:
        raise ValueError("AITUNNEL не настроен (AITUNNEL_API_KEY)")
    lang = (target_lang or target_lang_for(sid, lid)).strip()
    geo = target_geo_for(sid, lid)

    cfg = read_config(sid, lid)["config"]
    chat = dict(cfg.get("fakeChat") or {})
    comments = chat.get("preparedComments") or []
    if not comments:
        raise ValueError("В конфиге нет комментариев (fakeChat.preparedComments пуст)")

    # Собираем уникальные блоки: имена и тексты (включая ответы).
    blocks: dict[str, None] = {}
    def _collect(c: dict) -> None:
        for k in ("name", "text"):
            v = (c.get(k) or "").strip()
            if v:
                blocks.setdefault(v, None)
    for c in comments:
        _collect(c)
        for r in c.get("replies") or []:
            _collect(r)

    mapping = translate_blocks(list(blocks), lang, client,
                               translate_model(), geo=geo)

    def _apply(c: dict) -> dict:
        c = dict(c)
        for k in ("name", "text"):
            v = (c.get(k) or "").strip()
            if v and mapping.get(v):
                c[k] = mapping[v]
        if c.get("replies"):
            c["replies"] = [_apply(r) for r in c["replies"]]
        return c

    chat["preparedComments"] = [_apply(c) for c in comments]
    cfg["fakeChat"] = chat
    write_config(sid, lid, cfg)
    changed = sum(1 for o, t in mapping.items() if t.strip() != o.strip())
    log.info("VSL-комментарии %s/%s переведены на %s: %d/%d блоков",
             sid, lid, lang, changed, len(blocks))
    return {"lang": lang, "blocks": len(blocks), "changed": changed,
            "comments": len(comments)}
