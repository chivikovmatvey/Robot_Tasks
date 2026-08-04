"""Массовая заливка лендов сессии в Keitaro (фоновый поток + статус для UI).

Один поток на сессию, ленды заливаются ПОСЛЕДОВАТЕЛЬНО (Keitaro хрупкий, а
create_offer сам по себе многошаговый). Каждый ленд идёт через обычный
services.keitaro_upload.upload(execute=True) — то есть с авто-переименованием
и авто-созданием тестовой кампании. Прогресс хранится в памяти процесса:
фронт поллит /api/sessions/{sid}/keitaro-upload-all/status и рисует круговой
прогресс-бар + ссылки на тестовые кампании.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

log = logging.getLogger("keitaro.bulk")

_LOCK = threading.Lock()
_STATE: dict[str, dict] = {}   # sid → состояние массовой заливки

# Шаги одной заливки → доля выполненного (0..1). Сообщения приходят из
# keitaro_upload.upload и connectors.keitaro (create_offer / create_test_campaign),
# сопоставление по ПОДСТРОКЕ. Нужно, чтобы прогресс-кольцо росло плавно внутри
# ленда («выбираю страну»), а не рывками по одному делению на ленд.
# Регистр значим: у оффера «Выбираю группу», у кампании «выбираю группу».
_STEP_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("старт заливки", 0.02),
    ("Ищу партнёрскую сеть", 0.05),
    ("Сеть донора", 0.10),
    ("Открываю модалку создания оффера", 0.15),
    ("Заполняю название", 0.20),
    ("Выбираю группу", 0.25),
    ("Выбираю партнёрскую сеть", 0.30),
    ("Загружаю ZIP-архив", 0.38),
    ("Выбираю страну", 0.45),
    ("Нажимаю «Создать»", 0.50),
    ("Проверяю, что оффер создан", 0.55),
    ("Ищу созданный оффер", 0.60),
    ("переименовываю", 0.66),
    ("Создаю тестовую кампанию", 0.70),
    ("открываю кампании", 0.73),
    ("ввожу название", 0.76),
    ("выбираю группу", 0.79),
    ("создаю поток", 0.82),
    ("пересоздаю поток", 0.82),
    ("вкладка «Схема»", 0.85),
    ("жду готовности кнопки", 0.87),
    ("ищу оффер", 0.90),
    ("добавляю оффер в поток", 0.92),
    ("применяю поток", 0.94),
    ("создаю кампанию", 0.96),
    ("жду ссылку кампании", 0.98),
    # Финал: кампания получена ИЛИ не создалась (оффер при этом уже залит).
    ("Тестовая кампания готова", 1.0),
    ("Тестовая кампания не создана", 1.0),
)


def _step_progress(msg: str) -> float:
    """Доля выполненного по тексту шага (0 — шаг неизвестен)."""
    for needle, frac in _STEP_WEIGHTS:
        if needle in msg:
            return frac
    return 0.0


def _lander_label(ls) -> str:
    return ls.display_name or ls.lander_id


def get_status(sid: str) -> dict:
    with _LOCK:
        st = _STATE.get(sid)
        if not st:
            return {"running": False, "items": [], "total": 0, "done": 0,
                    "progress": 0.0, "cancelling": False}
        # копия без внутренних полей
        items = [dict(i) for i in st["items"]]
        cancelling = st.get("cancel", False) and st["running"]
    done = sum(1 for i in items
               if i["stage"] in ("done", "error", "needs_id", "cancelled"))
    # Кольцо заполняется по ШАГАМ: сумма долей всех лендов / их количество.
    progress = (sum(i.get("progress", 0.0) for i in items) / len(items)) if items else 0.0
    return {
        "running": st["running"],
        "started_at": st["started_at"],
        "items": items,
        "total": len(items),
        "done": done,
        "progress": round(progress, 4),
        "cancelling": cancelling,
    }


def start(sid: str, lids: Optional[list[str]] = None) -> dict:
    """Запускает массовую заливку. lids=None — все подходящие ленды сессии
    (ready/adapted и ещё не залитые). Уже запущенную не дублирует."""
    from services.session import get_manager

    with _LOCK:
        st = _STATE.get(sid)
        if st and st["running"]:
            return get_status(sid)

    mgr = get_manager()
    s = mgr.get(sid)
    if s is None:
        raise ValueError(f"Сессия {sid} не найдена")

    todo: list[tuple[str, str]] = []
    for lid, ls in s.landers.items():
        if lids is not None and lid not in lids:
            continue
        ap = ls.adapt_params or {}
        if ap.get("keitaro_offer_id"):
            continue  # уже залит
        if ls.status not in ("ready", "adapted"):
            continue
        todo.append((lid, _lander_label(ls)))

    if not todo:
        raise ValueError("Нет лендов для заливки: все уже залиты или не готовы")

    items = [{"lid": lid, "name": name, "stage": "queued", "step": "",
              "progress": 0.0,
              "offer_id": None, "final_name": None,
              "campaign_url": None, "campaign_name": None,
              "error": None} for lid, name in todo]
    with _LOCK:
        _STATE[sid] = {"running": True, "started_at": time.time(),
                       "items": items, "cancel": False}

    t = threading.Thread(target=_run, args=(sid,), daemon=True,
                         name=f"keitaro-bulk-{sid}")
    t.start()
    return get_status(sid)


def stop(sid: str) -> dict:
    """Просит остановить массовую заливку.

    ТЕКУЩИЙ ленд доводится до конца: он уже в середине многошагового сеанса
    Playwright (модалка оффера/поток кампании), и обрыв на полушаге оставил бы
    в Keitaro недоделанный оффер — руками потом разбирать. Все ленды из очереди
    после него помечаются 'cancelled' и не заливаются.
    """
    with _LOCK:
        st = _STATE.get(sid)
        if not st or not st["running"]:
            raise ValueError("Массовая заливка не запущена")
        st["cancel"] = True
    log.info("Bulk-заливка сессии %s: запрошена остановка", sid)
    return get_status(sid)


def _cancelled(sid: str) -> bool:
    with _LOCK:
        st = _STATE.get(sid)
        return bool(st and st.get("cancel"))


def _set(sid: str, lid: str, **patch) -> None:
    with _LOCK:
        st = _STATE.get(sid)
        if not st:
            return
        for i in st["items"]:
            if i["lid"] == lid:
                i.update(patch)
                return


def _set_step(sid: str, lid: str, msg: str) -> None:
    """Шаг ленда + монотонный прогресс (назад кольцо не откатывается)."""
    with _LOCK:
        st = _STATE.get(sid)
        if not st:
            return
        for i in st["items"]:
            if i["lid"] == lid:
                i["step"] = msg
                i["progress"] = max(i.get("progress", 0.0), _step_progress(msg))
                return


def _run(sid: str) -> None:
    from services.keitaro_upload import upload

    with _LOCK:
        items = [dict(i) for i in _STATE[sid]["items"]]

    for it in items:
        lid = it["lid"]
        if _cancelled(sid):
            _set(sid, lid, stage="cancelled", step="отменено пользователем",
                 progress=0.0)
            continue
        _set(sid, lid, stage="uploading", step="старт заливки", progress=0.02)

        def _progress(msg: str, _lid=lid) -> None:
            _set_step(sid, _lid, msg)

        try:
            res = upload(sid, lid, execute=True, on_progress=_progress)
            mode = res.get("mode")
            if mode == "uploaded":
                _set(sid, lid, stage="done",
                     step="готово", progress=1.0,
                     offer_id=res.get("offer_id"),
                     final_name=res.get("final_name"),
                     campaign_url=res.get("campaign_url"),
                     campaign_name=res.get("campaign_name"))
            else:
                # id не определился однозначно — нужен ручной выбор в панели
                # ленда (created_pending_rename).
                _set(sid, lid, stage="needs_id", progress=1.0,
                     step="оффер создан, id подтверди в панели ленда")
        except Exception as e:  # noqa: BLE001
            log.exception("Bulk: заливка ленда %s/%s упала", sid, lid)
            _set(sid, lid, stage="error", error=str(e), step="ошибка",
                 progress=1.0)

    with _LOCK:
        st = _STATE.get(sid)
        if st:
            st["running"] = False
            st["cancel"] = False
    log.info("Bulk-заливка сессии %s завершена", sid)
