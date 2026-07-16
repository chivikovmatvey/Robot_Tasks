"""Заливка адаптированного ленда в Keitaro (создание оффера).

ВНИМАНИЕ: Playwright-код создания оффера в connectors/keitaro.py НЕ протестирован
вживую. Этот модуль — оркестрация + безопасный dry-run.

По умолчанию (без --execute) НЕ трогает Keitaro вообще: собирает план из сессии
и печатает его. Реальная заливка (--execute) запускается ТОЛЬКО вручную в
рабочее время — она найдёт партнёрскую сеть/скобку донора, создаст оффер,
узнает фактический id и поправит название (id в названии = фактический id).

Формат названия (раздел 5/Шаг 3 AGENT.md + уточнения пользователя):
  обычный: '{id} {product} [{VERTICAL-GEO}] [{type} {lang} -]'
           19712 Detox Now [PARASITES-CO] [pl es -]
  vsl:     '{id} {product} [{VERTICAL-GEO}] [vsl {lang} ] {react}'
           20091 Detox Now [PARASITES-CO] [vsl es ] ReactJS v5
  {react} настраивается через KEITARO_VSL_REACT (по умолчанию 'ReactJS v5').

Группа = оффер из задачи. Если группы нет в Keitaro — заливка прерывается.
"""

from __future__ import annotations

import logging
import os
import zipfile
from pathlib import Path
from typing import Optional

from services.session import get_manager, parse_target_offer
from utils import runners

log = logging.getLogger("keitaro.upload")

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUTS = BASE_DIR / "storage" / "outputs"

# Маркеры VSL-ленда в архиве (см. Часть 3 / раздел VSL AGENT.md).
_VSL_MARKERS = ("config.php", "vsl_start_video.js", "vsl-offer")


def vsl_react_template() -> str:
    return os.getenv("KEITARO_VSL_REACT", "ReactJS v5").strip() or "ReactJS v5"


def detect_site_type(zip_path: Path, scan: Optional[dict]) -> str:
    """Тип сайта для второй скобки названия: 'vsl' | 'land' | 'pl'.

    vsl  — есть VSL-маркеры (config.php / vsl-бандл);
    land — на первой странице есть фото продукта (scan.prod_images);
    pl   — иначе.
    Эвристика — при заливке можно переопределить параметром --type.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n.lower() for n in zf.namelist()]
        if any(any(m in n for m in _VSL_MARKERS) for n in names):
            return "vsl"
    except Exception:  # noqa: BLE001
        pass
    if scan and (scan.get("prod_images") or []):
        return "land"
    return "pl"


def build_offer_name(product: str, bracket: str, site_type: str, lang: str,
                     offer_id: Optional[int] = None, adult: bool = False) -> str:
    """Собирает название оффера. offer_id=None — без префикса id (для создания,
    id подставляется после, когда станет известен фактический).
    adult — третья позиция скобки: '-' → 'adult' ([pl fi -] → [pl fi adult])."""
    prefix = f"{offer_id} " if offer_id is not None else ""
    br = bracket or ""  # уже со скобками: '[PARASITES-CO]'
    mark = "adult" if adult else "-"
    if site_type == "vsl":
        # Пример: '20490 Sustarox [JOINT-EC] [vsl es -] ReactJS v5'
        tail = f"[vsl {lang} {mark}] {vsl_react_template()}"
    else:
        tail = f"[{site_type} {lang} {mark}]"
    parts = [p for p in (br, tail) if p]
    return f"{prefix}{product} {' '.join(parts)}".strip()


def prepare_plan(sid: str, lid: str, site_type: Optional[str] = None,
                 adult: bool = False) -> dict:
    """Собирает план заливки ИЗ СЕССИИ (без обращения к Keitaro).

    Возвращает всё, что известно локально: zip, группа (оффер задачи), продукт,
    гео, язык, тип сайта, шаблон названия (без id; сеть и скобку донора узнаём
    из Keitaro только на этапе execute).
    """
    mgr = get_manager()
    s = mgr.get(sid)
    if s is None:
        raise ValueError(f"Сессия {sid} не найдена")
    ls = s.landers.get(lid)
    if ls is None:
        raise ValueError(f"Ленд {lid} не найден в сессии {sid}")
    # Адаптированный output, а если его нет — ИСХОДНЫЙ архив ленда
    # (заливка без адаптации разрешена).
    adapted = bool(ls.output_name)
    if adapted:
        zip_path = OUTPUTS / ls.output_name
        if not zip_path.exists():
            raise ValueError(f"Выходной архив не найден: {zip_path}")
    else:
        if not ls.zip_path or not Path(ls.zip_path).exists():
            raise ValueError("Нет ни адаптированного, ни исходного архива ленда")
        zip_path = Path(ls.zip_path)

    group = s.lander_offer(ls)  # оффер ленда (с учётом ручной подмены) = группа в Keitaro
    geos = runners.load_geos()
    parsed = parse_target_offer(group, geos)
    geo_id = parsed.get("geo_id") or ""
    product = parsed.get("product") or ""          # ПОЛНОЕ имя продукта — для названия
    product_search = parsed.get("product_search") or product  # ядро — для поиска донора
    geo_info = geos.get(geo_id, {}) or {}
    lang = geo_info.get("lang_html", "es") or "es"
    country_name = geo_info.get("country_name", "") or ""
    # VSL-сессия всегда даёт тип vsl (работаем от эталонного шаблона).
    stype = site_type or ("vsl" if getattr(s, "is_vsl", False)
                          else detect_site_type(zip_path, ls.scan))

    # Скобка [ВЕРТИКАЛЬ-ГЕО] строится ИЗ ГРУППЫ (вертикаль + целевое гео), а не
    # копируется с донора (донор давал не ту вертикаль/гео).
    vertical_full = parsed.get("vertical_full") or parsed.get("vertical") or ""
    bracket = f"[{vertical_full}-{geo_id}]" if (vertical_full and geo_id) else ""

    return {
        "sid": sid,
        "lid": lid,
        "adapted": adapted,
        "adult": adult,
        "zip_path": str(zip_path),
        "group": group,
        "product": product,
        "product_search": product_search,
        "geo_id": geo_id,
        "country_query": geo_id.lower(),   # код страны для шаблона
        "country_name": country_name,      # имя страны (фолбэк-поиск в дропдауне)
        "lang": lang,
        "site_type": stype,
        "bracket": bracket,
        "name_template": build_offer_name(product, bracket or "[VERTICAL-GEO]",
                                          stype, lang, adult=adult),
        "vertical_code": parsed.get("vertical") or "",
        "vertical_full": vertical_full,
    }


def upload(sid: str, lid: str, *, execute: bool = False,
           site_type: Optional[str] = None,
           network_override: Optional[str] = None,
           adult: bool = False,
           on_progress: Optional[callable] = None) -> dict:
    """Заливка ленда. execute=False (по умолчанию) — dry-run без Keitaro.

    network_override — принудительно задать партнёрскую сеть (если авто-детект с
    донора ошибается). adult — пометка '[.. .. adult]' в названии.
    on_progress(msg) — колбэк шагов для UI.
    """
    plan = prepare_plan(sid, lid, site_type=site_type, adult=adult)

    def _step(msg: str) -> None:
        if on_progress:
            try:
                on_progress(msg)
            except Exception:  # noqa: BLE001
                pass

    if not execute:
        plan["mode"] = "dry-run"
        plan["note"] = ("Скобка [ВЕРТИКАЛЬ-ГЕО] построена из группы. Сеть донора "
                        "и фактический id будут получены из Keitaro при запуске. "
                        "Сейчас Keitaro не затронут.")
        return plan

    from connectors.keitaro import client_from_env, KeitaroError
    with client_from_env() as kt:
        # Скобка [ВЕРТИКАЛЬ-ГЕО] уже построена из группы (см. prepare_plan).
        bracket = plan["bracket"]
        if not bracket:
            raise KeitaroError(
                f"Не удалось определить вертикаль/гео из группы '{plan['group']}' "
                f"— скобка [ВЕРТИКАЛЬ-ГЕО] не построена, заливка прервана")

        # Сеть: ручное переопределение, иначе копируем с донора (по ядру + гео).
        network = network_override or plan.get("network_override")
        if not network:
            _step("Ищу партнёрскую сеть у донора продукта")
            meta = kt.find_offer_meta(plan["product_search"], plan["geo_id"])
            network = meta.get("network")
            _step(f"Сеть донора: {network or 'не найдена'}")

        # 3) Создаём с названием БЕЗ id. Оффер НЕ переименовываем автоматически —
        # это делает пользователь после ПОДТВЕРЖДЕНИЯ id (см. rename_offer ниже).
        # Авто-выбор id опасен: был инцидент с переименованием чужого оффера 6506.
        name_no_id = build_offer_name(plan["product"], bracket,
                                      plan["site_type"], plan["lang"], adult=adult)
        detection = kt.create_offer(
            name=name_no_id,
            group=plan["group"],
            network=network,
            zip_path=plan["zip_path"],
            country_query=plan["country_query"],
            country_name=plan.get("country_name", ""),
            on_progress=on_progress,
        )
        best = detection.get("best")
        confident = detection.get("confident", False)
        # Предполагаемое финальное имя для лучшего кандидата (если уверенно).
        proposed_name = build_offer_name(
            plan["product"], bracket, plan["site_type"], plan["lang"],
            offer_id=best, adult=adult) if best else None

        # АВТО-ПЕРЕИМЕНОВАНИЕ: если детекция уверенная (точное совпадение
        # названия без id-префикса) — сразу дописываем id, без подтверждения.
        # Ручной выбор остаётся только как fallback при неуверенной детекции.
        if best and confident and proposed_name:
            _step(f"Оффер создан (id={best}) — переименовываю: {proposed_name}")
            kt.rename_offer(best, proposed_name,
                            country_query=plan.get("country_query", ""),
                            country_name=plan.get("country_name", ""))
            _record_publish(sid, lid, int(best), proposed_name, plan)
            log.info("Оффер %s создан и авто-переименован → %s", best, proposed_name)
            return {
                **plan,
                "mode": "uploaded",  # создан И переименован — готово
                "offer_id": best,
                "final_name": proposed_name,
                "network": network, "bracket": bracket,
            }

        _step("Оффер создан, но id не определён однозначно — нужен выбор id")
        result = {
            **plan,
            "mode": "created_pending_rename",  # fallback: ручной выбор id
            "name_no_id": name_no_id,
            "network": network, "bracket": bracket,
            "id_candidates": detection.get("candidates", []),
            "id_best": best,
            "id_confident": confident,
            "proposed_name": proposed_name,
        }
        log.info("Оффер создан (детекция id неуверенная). best=%s confident=%s",
                 best, confident)
        return result


def _record_publish(sid: str, lid: str, offer_id: int, final_name: str, plan: dict) -> None:
    """Сохраняет факт заливки в ленд + историю публикаций (общее для авто- и ручного rename)."""
    s = get_manager().get(sid)
    ls = s.landers.get(lid) if s else None
    if ls is not None:
        ls.adapt_params = {**(ls.adapt_params or {}),
                           "keitaro_offer_id": offer_id,
                           "keitaro_name": final_name}
        get_manager()._save(s)
    try:
        from services.publish_history import get_history
        get_history().add(offer_id, product=plan.get("product", ""),
                          geo=plan.get("geo_id", ""), session_id=sid,
                          name=final_name)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось записать публикацию id=%s в историю", offer_id)


def rename_offer(sid: str, lid: str, offer_id: int, *,
                 site_type: Optional[str] = None, adult: bool = False) -> dict:
    """Переименовывает ПОДТВЕРЖДЁННЫЙ пользователем оффер: дописывает id в название.

    offer_id — id, который пользователь выбрал/подтвердил в UI (после create).
    Имя собирается из плана ленда (product/bracket/type/lang) + этого id.
    """
    plan = prepare_plan(sid, lid, site_type=site_type, adult=adult)
    bracket = plan["bracket"]
    final_name = build_offer_name(plan["product"], bracket,
                                  plan["site_type"], plan["lang"],
                                  offer_id=offer_id, adult=adult)
    from connectors.keitaro import client_from_env
    with client_from_env() as kt:
        # Передаём страну — чтобы при сохранении модалка не сбросила её в
        # «Неизвестно» (переподтверждаем перед Save).
        kt.rename_offer(offer_id, final_name,
                        country_query=plan.get("country_query", ""),
                        country_name=plan.get("country_name", ""))

    # Сохраним факт заливки в ленд + историю публикаций.
    _record_publish(sid, lid, offer_id, final_name, plan)
    log.info("Оффер %s переименован → %s", offer_id, final_name)
    return {"offer_id": offer_id, "final_name": final_name, "mode": "renamed"}


def create_test_campaign(sid: str, lid: str, *, on_progress=None) -> dict:
    """Создаёт тестовую кампанию для залитого (и переименованного с id) ленда.

    Название кампании: «test mch <полное имя оффера с id>». Группа — Andrei AM.
    Возвращает и сохраняет в ленд ссылку на кампанию (campaign_url)."""
    mgr = get_manager()
    s = mgr.get(sid)
    ls = s.landers.get(lid) if s else None
    if ls is None:
        raise ValueError(f"Ленд {lid} не найден в сессии {sid}")
    ap = ls.adapt_params or {}
    offer_id = ap.get("keitaro_offer_id")
    offer_name = ap.get("keitaro_name")
    if not offer_id or not offer_name:
        raise ValueError(
            "Сначала залей ленд в Keitaro (нужны id и имя оффера после переименования)")

    from connectors.keitaro import client_from_env
    with client_from_env() as kt:
        link = kt.create_test_campaign(offer_id, offer_name, on_progress=on_progress)

    campaign_name = f"test mch {offer_name}"
    ls.adapt_params = {**ap, "campaign_url": link, "campaign_name": campaign_name}
    mgr._save(s)
    log.info("Тестовая кампания для оффера %s создана: %s", offer_id, link)
    return {"campaign_url": link, "campaign_name": campaign_name,
            "offer_id": offer_id}


# ── CLI ──────────────────────────────────────────────────────────
def _main() -> None:
    import argparse
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    ap = argparse.ArgumentParser(
        description="Заливка адаптированного ленда в Keitaro (создание оффера).")
    ap.add_argument("sid", help="ID сессии")
    ap.add_argument("lid", help="ID ленда внутри сессии")
    ap.add_argument("--execute", action="store_true",
                    help="РЕАЛЬНАЯ заливка в Keitaro (по умолчанию — dry-run без Keitaro)")
    ap.add_argument("--type", choices=["land", "pl", "vsl"], default=None,
                    help="Переопределить тип сайта (иначе авто по архиву/scan)")
    args = ap.parse_args()

    res = upload(args.sid, args.lid, execute=args.execute, site_type=args.type)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
