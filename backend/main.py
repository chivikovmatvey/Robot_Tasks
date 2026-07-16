"""
FastAPI сервер для Offer Processor.
Запуск:  uvicorn main:app --reload --port 8000

Очистка storage при остановке сервера: см. OFFER_PURGE_ON_SHUTDOWN в utils/storage_purge.py
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

STORAGE = Path(__file__).parent / "storage"


def _setup_logging() -> None:
    """Гарантируем вывод логов наших модулей в консоль на уровне INFO.

    uvicorn настраивает свои логгеры, но наши (keitaro/session/...) могли не
    показываться. Нужно, чтобы было видно пошаговый трейс скачивания из Keitaro.
    Уровень переопределяется через LOG_LEVEL (напр. DEBUG).
    """
    import logging
    import os
    import sys
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    for name in ("keitaro.connector", "session", "keitaro.upload",
                 "adrobot", "intake", "agent", "image_edit", "bg_remove"):
        lg = logging.getLogger(name)
        lg.setLevel(level)
        if not any(isinstance(h, logging.StreamHandler) for h in lg.handlers):
            lg.addHandler(handler)
        lg.propagate = False


_setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # .env (Keitaro / AdRobot / Telegram)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent / ".env")
    except ImportError:
        pass

    # Фоновый приём задач из AdRobot (если настроен).
    import threading
    from services.task_intake import build_from_env

    app.state.intake = None
    app.state._intake_stop = None
    app.state._intake_thread = None
    try:
        intake = build_from_env()
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("intake").exception("Не удалось создать TaskIntake")
        intake = None

    # Пул потоков для sync-эндпоинтов: дефолтные 40 съедаются долгими
    # Playwright-операциями (скачивание/заливка Keitaro) и ресурсами превью —
    # остальные запросы (сохранение, адаптация) вставали в очередь.
    try:
        import anyio.to_thread
        anyio.to_thread.current_default_thread_limiter().total_tokens = 120
    except Exception:  # noqa: BLE001
        pass

    if intake is not None:
        import os
        interval = int(os.getenv("POLL_INTERVAL", "60") or "60")
        notify_first = os.getenv("NOTIFY_ON_FIRST_RUN", "false").lower() == "true"
        stop = threading.Event()
        thread = threading.Thread(
            target=intake.run_forever,
            args=(stop, interval, notify_first),
            daemon=True,
            name="adrobot-poller",
        )
        thread.start()
        # Слушатель Telegram (кнопки «Принять задачу»/«Список задач», /tasks).
        if intake.notifier is not None:
            threading.Thread(
                target=intake.run_telegram_forever,
                args=(stop,),
                daemon=True,
                name="telegram-listener",
            ).start()
        app.state.intake = intake
        app.state._intake_stop = stop
        app.state._intake_thread = thread

    yield

    if app.state._intake_stop is not None:
        app.state._intake_stop.set()

    from utils.storage_purge import purge_storage_on_shutdown
    purge_storage_on_shutdown(STORAGE)


# ── Инициализация ─────────────────────────────────────────────
app = FastAPI(
    title="Offer Processor API",
    description="Локальный веб-инструмент для обработки лендингов",
    version="0.1.0",
    lifespan=lifespan,
)

# Диагностика зависаний: запросы дольше порога попадают в лог с длительностью —
# видно, ЧТО именно тормозит (адаптация, превью, сохранение архива и т.д.).
import logging as _logging
import os as _os
import sys as _sys
import time as _time

_SLOW_MS = int(_os.getenv("SLOW_REQUEST_MS", "3000") or "3000")
_slow_log = _logging.getLogger("slow")
_slow_log.setLevel(_logging.INFO)
if not _slow_log.handlers:
    _h = _logging.StreamHandler(_sys.stdout)
    _h.setFormatter(_logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    _slow_log.addHandler(_h)
    _slow_log.propagate = False


@app.middleware("http")
async def _log_slow_requests(request, call_next):
    t0 = _time.monotonic()
    try:
        return await call_next(request)
    finally:
        sec = _time.monotonic() - t0
        if sec * 1000 >= _SLOW_MS:
            _slow_log.warning("МЕДЛЕННО %.1fс %s %s", sec,
                              request.method, request.url.path)


# CORS — для разработки, чтобы Vite на :3000 мог стучаться сюда на :8000.
# На проде Vite-прокси убирает эту необходимость, но в dev удобнее иметь.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Папки для рантайма
for sub in ("uploads", "outputs", "assets", "configs", "output"):
    (STORAGE / sub).mkdir(parents=True, exist_ok=True)


# ── Эндпоинты ────────────────────────────────────────────────
@app.get("/api/health")
def health():
    """Пинг для проверки что бэк жив."""
    return {
        "status": "ok",
        "service": "offer-processor",
        "version": "0.1.0",
    }


@app.get("/api/info")
def info():
    """Базовая инфа о текущем состоянии storage."""
    return {
        "uploads": len(list((STORAGE / "uploads").glob("*"))),
        "outputs": len(list((STORAGE / "outputs").glob("*.zip"))),
        "assets":  len(list((STORAGE / "assets").glob("*"))),
        "configs": len(list((STORAGE / "configs").glob("*.json"))),
    }


class StorageClearBody(BaseModel):
    """temp — uploads + output; all — ещё и всё в outputs/."""
    scope: str = Field("temp", description="temp | all")


@app.post("/api/storage/clear")
def storage_clear(body: StorageClearBody):
    """Ручная очистка (кнопка на главной). assets/ и configs/ не трогаем."""
    scope = (body.scope or "temp").strip().lower()
    if scope not in ("temp", "all"):
        raise HTTPException(400, "scope must be 'temp' or 'all'")
    from utils.storage_purge import clear_storage
    return clear_storage(STORAGE, scope)


# ── Задачи (AdRobot) ─────────────────────────────────────────
from fastapi import Request


def _require_intake(request: Request):
    intake = getattr(request.app.state, "intake", None)
    if intake is None:
        raise HTTPException(503, "Приём задач не настроен (заполни ADROBOT_* в .env)")
    return intake


def _fetch_url(intake, url: str):
    """Скачивает вложение: облако (Google Drive/Яндекс Диск) или robotmediaassets
    через AdRobot-сессию. → (bytes, filename, content_type)."""
    from connectors.cloud import cloud_kind, download_cloud
    if cloud_kind(url):
        data, fname = download_cloud(url)
        return data, fname, "application/octet-stream"
    return intake.client.download_attachment(url)


@app.get("/api/tasks")
def tasks_list(request: Request, refresh: int = 0):
    """Релевантные задачи: пул Anyone + личные (mch). refresh=1 — перезапросить."""
    intake = _require_intake(request)
    cached = intake.list_cached()
    # На холодном старте кэш ещё не наполнен поллером — тянем сразу.
    if refresh or not cached:
        try:
            intake.fetch_relevant()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Не удалось получить задачи: {e}")
        cached = intake.list_cached()
    return cached


@app.get("/api/tasks/groups")
def tasks_groups(request: Request, refresh: int = 0):
    """Кластеры задач на один оффер (>1 задачи) — кандидаты на объединение."""
    intake = _require_intake(request)
    if refresh or not intake.list_cached():
        try:
            intake.fetch_relevant()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Не удалось получить задачи: {e}")
    return intake.group_duplicates()


@app.get("/api/tasks/attachment")
def task_attachment(request: Request, url: str, download: int = 0):
    """Прокси вложений комментариев (нужна авторизация AdRobot-сессии).

    download=1 — отдать как файл (Content-Disposition), иначе — инлайн (превью).
    """
    from fastapi import Response
    intake = _require_intake(request)
    try:
        data, filename, ctype = _fetch_url(intake, url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось скачать вложение: {e}")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content=data, media_type=ctype, headers=headers)


class AssetFromUrlBody(BaseModel):
    url: str = Field(..., description="URL картинки-вложения из комментария задачи")
    filename: str | None = Field(None, description="Имя файла (иначе из URL)")


@app.post("/api/assets/from-url")
def asset_from_url(request: Request, body: AssetFromUrlBody):
    """Импортирует картинку из комментария задачи в storage/assets/.

    Дальше её можно использовать как замену фото на ленде через image_map
    (имя возвращается в ответе и появляется в списке ассетов).
    """
    import re as _re
    from utils.runners import STORAGE
    intake = _require_intake(request)
    try:
        data, filename, _ctype = _fetch_url(intake, body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось скачать вложение: {e}")

    name = _re.sub(r"[^\w.-]", "_", (body.filename or filename or "image").strip())
    if not name:
        name = "image"
    assets_dir = STORAGE / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    # Не затираем существующий ассет с тем же именем — уникализируем.
    target = assets_dir / name
    if target.exists():
        stem, suf = target.stem, target.suffix
        i = 2
        while (assets_dir / f"{stem}_{i}{suf}").exists():
            i += 1
        target = assets_dir / f"{stem}_{i}{suf}"
    target.write_bytes(data)
    return {"name": target.name, "size": len(data)}


@app.get("/api/tasks/{uid}")
def tasks_detail(request: Request, uid: str):
    """Полная карточка задачи по UID."""
    intake = _require_intake(request)
    try:
        return intake.get_detail(uid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось получить задачу: {e}")


@app.post("/api/tasks/poll")
def tasks_poll(request: Request, notify: int = 1):
    """Принудительный проход поллера. Возвращает новые задачи."""
    intake = _require_intake(request)
    try:
        new = intake.poll_once(notify=bool(notify))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Ошибка опроса: {e}")
    from dataclasses import asdict
    return {"new_count": len(new), "new": [asdict(t) for t in new]}


class TaskStatusBody(BaseModel):
    status: str = Field("IN_PROCESS", description="IN_PROCESS | NEED_DETAILS")


@app.post("/api/tasks/{uid}/status")
def task_change_status(request: Request, uid: str, body: TaskStatusBody):
    """Сменить статус задачи (PENDING → IN_PROCESS, «Start working»).
    Меняющее действие в AdRobot. Возвращает обновлённую карточку + обновляет кэш."""
    from dataclasses import asdict
    intake = _require_intake(request)
    try:
        detail = intake.client.change_status(uid, body.status)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось сменить статус: {e}")
    # Обновим кэш задач, чтобы список сразу показал новый статус.
    try:
        intake.fetch_relevant()
    except Exception:  # noqa: BLE001
        pass
    return asdict(detail)


@app.get("/api/notifications")
def notifications_list(request: Request, refresh: int = 0):
    """Лента уведомлений AdRobot (статусы + комментарии). refresh=1 — перезапросить."""
    intake = _require_intake(request)
    cached = intake.list_notifications_cached()
    if refresh or not cached:
        try:
            intake.fetch_notifications()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Не удалось получить уведомления: {e}")
        cached = intake.list_notifications_cached()
    return cached


@app.post("/api/notifications/poll")
def notifications_poll(request: Request, notify: int = 1):
    """Принудительный проход по уведомлениям (ACCEPTED + комментарии)."""
    intake = _require_intake(request)
    try:
        new = intake.poll_notifications(notify=bool(notify))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Ошибка опроса уведомлений: {e}")
    from dataclasses import asdict
    return {"new_count": len(new), "new": [asdict(n) for n in new]}


# ── Сессии адаптации ─────────────────────────────────────────
class SessionCreateBody(BaseModel):
    task_uid: str | None = Field(None, description="UID задачи AdRobot (создать из задачи)")
    task_uids: list[str] | None = Field(
        None, description="Несколько задач на один оффер → объединённая сессия")
    lander_ids: list[str] | None = Field(None, description="ID лендов вручную")
    offer: str | None = Field(None, description="Целевой оффер (для ручного создания)")
    vsl: bool = Field(False, description="VSL-сессия: добавить эталонный шаблон 19201, работа через config.php")


@app.post("/api/sessions")
def session_create(request: Request, body: SessionCreateBody):
    """Создать сессию адаптации.

    Источники лендов (можно комбинировать):
      - task_uids   — несколько задач на ОДИН оффер → одна объединённая сессия,
                      каждый ленд помечается своей задачей (для проверки потом);
      - task_uid    — взять оффер/поля из задачи, ID лендов извлечь из неё;
      - lander_ids  — ручной список ID (переопределяет извлечённые);
      - (после создания) — загрузить архивы через .../landers/upload.
    Пустая сессия (без лендов) допустима — ленды добавляются позже.
    """
    from services.session import get_manager, extract_lander_ids

    mgr = get_manager()

    # Объединение нескольких задач одного оффера в одну сессию.
    uids = [u for u in (body.task_uids or []) if u and u.strip()]
    if not uids and body.task_uid:
        uids = [body.task_uid]

    if len(uids) > 1:
        intake = getattr(request.app.state, "intake", None)
        if intake is None:
            raise HTTPException(503, "Приём задач не настроен — создавай сессию по lander_ids")
        details = []
        for u in uids:
            try:
                details.append(intake.client.get_task(u))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"Не удалось получить задачу {u}: {e}")
        s = mgr.create_from_tasks(details)
        if body.vsl:
            from services.session import LanderState
            from services.vsl import VSL_TEMPLATE_ID
            s.is_vsl = True
            if VSL_TEMPLATE_ID not in s.landers:
                s.landers[VSL_TEMPLATE_ID] = LanderState(lander_id=VSL_TEMPLATE_ID)
            mgr._save(s)
        if s.landers:
            mgr.prepare_async(s.id)
        return s.to_dict()

    fields: dict = {}
    title = ""
    uid = None
    url = ""
    offer = (body.offer or "").strip()

    if uids:
        intake = getattr(request.app.state, "intake", None)
        if intake is None:
            raise HTTPException(503, "Приём задач не настроен — создавай сессию по lander_ids")
        try:
            detail = intake.client.get_task(uids[0])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Не удалось получить задачу: {e}")
        fields = detail.fields
        title = detail.title
        uid = detail.uid
        url = detail.url
        if not offer:
            offer = fields.get("Offer", "")
        # ВАЖНО: пустой список (юзер очистил поле) != отсутствие поля. Если фронт
        # прислал lander_ids (пусть даже []), уважаем его и НЕ перечитываем id из
        # задачи (иначе убранные из автоподстановки номера снова попадали в сессию).
        ids = body.lander_ids if body.lander_ids is not None else extract_lander_ids(fields)
    else:
        ids = body.lander_ids or []

    s = mgr.create_manual(ids, offer, task_uid=uid, task_title=title,
                          fields=fields, task_url=url, is_vsl=body.vsl)
    if s.landers:  # VSL добавляет шаблон 19201 даже при пустых ids
        mgr.prepare_async(s.id)
    return s.to_dict()


@app.get("/api/sessions")
def session_list(archived: int = 0):
    """Список сессий. archived=1 — показать содержимое архива."""
    from services.session import get_manager
    return get_manager().list(archived=bool(archived))


@app.get("/api/sessions/{sid}")
def session_get(sid: str):
    from services.session import get_manager
    s = get_manager().get(sid)
    if s is None:
        raise HTTPException(404, "Сессия не найдена")
    return s.to_dict()


@app.post("/api/sessions/{sid}/archive")
def session_archive(sid: str):
    """Переместить сессию в архив (хранится 1 день, потом стирается)."""
    from services.session import get_manager
    try:
        return get_manager().archive(sid).to_dict()
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/sessions/{sid}/unarchive")
def session_unarchive(sid: str):
    """Вернуть сессию из архива в активные."""
    from services.session import get_manager
    try:
        return get_manager().unarchive(sid).to_dict()
    except KeyError as e:
        raise HTTPException(404, str(e))


class AddLandersBody(BaseModel):
    lander_ids: list[str] = Field(default_factory=list)


@app.post("/api/sessions/{sid}/landers")
def session_add_landers(sid: str, body: AddLandersBody):
    """Добавить ленды по ID (скачиваются из Keitaro)."""
    from services.session import get_manager
    if not body.lander_ids:
        raise HTTPException(422, "Пустой список lander_ids")
    try:
        s = get_manager().add_landers(sid, body.lander_ids)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return s.to_dict()


class ReorderLandersBody(BaseModel):
    order: list[str] = Field(..., description="ID лендов в нужном порядке")


@app.post("/api/sessions/{sid}/landers/reorder")
def session_reorder_landers(sid: str, body: ReorderLandersBody):
    """Переставить ленды в сессии (порядок задаётся списком id)."""
    from services.session import get_manager
    try:
        s = get_manager().reorder_landers(sid, body.order)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return s.to_dict()


@app.delete("/api/sessions/{sid}/landers/{lid}")
def session_delete_lander(sid: str, lid: str):
    """Удалить ленд из сессии (с подчисткой его файлов)."""
    from services.session import get_manager
    try:
        s = get_manager().delete_lander(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return s.to_dict()


@app.post("/api/sessions/{sid}/landers/{lid}/reinstall")
def session_reinstall_lander(sid: str, lid: str):
    """Переустановить ленд: стереть текущее состояние и заново скачать
    первоначальный архив из Keitaro."""
    from services.session import get_manager
    try:
        s = get_manager().reinstall_lander(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return s.to_dict()


class LanderNameBody(BaseModel):
    name: str = Field("", description="Новое имя вкладки ленда (пусто = id)")


@app.put("/api/sessions/{sid}/landers/{lid}/name")
def session_rename_lander(sid: str, lid: str, body: LanderNameBody):
    """Переименовать вкладку ленда (пользовательское имя, id не меняется)."""
    from services.session import get_manager
    try:
        get_manager().rename_lander(sid, lid, body.name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return get_manager().get(sid).to_dict()


@app.post("/api/sessions/{sid}/landers/{lid}/duplicate")
def session_duplicate_lander(sid: str, lid: str):
    """Дублировать ленд (копия архивов/параметров/журнала правок)."""
    from services.session import get_manager
    try:
        dup = get_manager().duplicate_lander(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"lander_id": dup.lander_id, "session": get_manager().get(sid).to_dict()}


class LanderTaskBody(BaseModel):
    task_uid: str = Field(..., description="UID задачи-источника из сессии")


@app.put("/api/sessions/{sid}/landers/{lid}/task")
def session_set_lander_task(sid: str, lid: str, body: LanderTaskBody):
    """Привязывает ленд к одной из задач сессии (для объединённых сессий:
    загруженный вручную ленд без привязки нельзя было отправить в
    вариант/ревью — «не могу определить задачу для действия»)."""
    from services.session import get_manager
    mgr = get_manager()
    s = mgr.get(sid)
    ls = s.landers.get(lid) if s else None
    if ls is None:
        raise HTTPException(404, f"Ленд {lid} не найден в сессии {sid}")
    t = next((t for t in s.tasks if t.get("uid") == body.task_uid), None)
    if t is None:
        raise HTTPException(422, "Такой задачи нет в этой сессии")
    ls.task_uid = t["uid"]
    ls.task_title = t.get("title") or ""
    mgr._save(s)
    return {"task_uid": ls.task_uid, "task_title": ls.task_title}


class AddTaskBody(BaseModel):
    task_uid: str = Field(..., description="UID задачи AdRobot того же оффера")


@app.post("/api/sessions/{sid}/tasks")
def session_add_task(request: Request, sid: str, body: AddTaskBody):
    """Подмешать ещё одну задачу того же оффера в сессию (её ленды добавятся)."""
    from services.session import get_manager
    intake = getattr(request.app.state, "intake", None)
    if intake is None:
        raise HTTPException(503, "Приём задач не настроен")
    try:
        detail = intake.client.get_task(body.task_uid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось получить задачу: {e}")
    try:
        s = get_manager().add_task(sid, detail)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return s.to_dict()


@app.post("/api/sessions/{sid}/landers/upload")
async def session_upload_lander(sid: str, file: UploadFile = File(...),
                                lander_id: str = Form("")):
    """Добавить ленд из загруженного ZIP-архива (без Keitaro)."""
    from services.session import get_manager
    from starlette.concurrency import run_in_threadpool
    if not file.filename:
        raise HTTPException(400, "Пустое имя файла")
    data = await file.read()
    # Скан архива (zip + регэкспы сканера) — тяжёлая БЛОКИРУЮЩАЯ работа. В async-
    # эндпоинте она крутилась бы прямо в event loop и замораживала ВЕСЬ бэк (даже
    # /api/health) до конца скана — отсюда «теряется связь с бэком» при загрузке
    # больших донор-архивов. Выносим в threadpool: цикл остаётся отзывчивым.
    try:
        ls = await run_in_threadpool(
            get_manager().add_uploaded_lander, sid, data,
            file.filename, lander_id or None)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    from dataclasses import asdict
    return asdict(ls)


class LanderFromUrlBody(BaseModel):
    url: str = Field(..., description="URL вложения-архива из комментария задачи")
    filename: str | None = Field(None, description="Имя файла (иначе из URL)")
    lander_id: str | None = None
    task_uid: str | None = Field(None, description="Привязка ленда к задаче (для изоляции замен)")


@app.post("/api/sessions/{sid}/landers/from-url")
def session_lander_from_url(request: Request, sid: str, body: LanderFromUrlBody):
    """Добавить ленд из архива, прикреплённого в комментарии задачи AdRobot."""
    from services.session import get_manager
    intake = _require_intake(request)
    try:
        data, filename, _ctype = _fetch_url(intake, body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось скачать вложение: {e}")
    try:
        ls = get_manager().add_uploaded_lander(
            sid, data, body.filename or filename, body.lander_id or None,
            task_uid=body.task_uid or None)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    from dataclasses import asdict
    return asdict(ls)


# ── Прокси для скрапинга гео-защищённых лендов ──────────────
@app.get("/api/proxies")
def proxies_list():
    """Сохранённые прокси (без паролей)."""
    from services.proxies import get_store
    return get_store().list()


class ProxyAddBody(BaseModel):
    proxy: str = Field(..., description="host:port[:user:pass] | user:pass@host:port | scheme://...")
    label: str | None = None
    geo: str | None = None


@app.post("/api/proxies")
def proxies_add(body: ProxyAddBody):
    from services.proxies import get_store
    try:
        return get_store().add(body.proxy, label=body.label or "", geo=body.geo or "")
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/proxies/{proxy_id}")
def proxies_delete(proxy_id: str):
    from services.proxies import get_store
    get_store().delete(proxy_id)
    return {"ok": True}


@app.post("/api/proxies/import-dolphin")
def proxies_import_dolphin():
    """Импортирует прокси из библиотеки Dolphin Anty (Remote API, токен в .env)."""
    from services.proxies import get_store
    from connectors.dolphin import list_proxies, proxy_to_raw
    try:
        proxies = list_proxies()
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Dolphin: {e}")
    store = get_store()
    added = 0
    for p in proxies:
        res = store.add(proxy_to_raw(p), label=p.get("name", ""), geo=p.get("geo", ""))
        if not res.get("duplicate"):
            added += 1
    return {"imported": added, "total": len(proxies)}


class LanderFromSiteBody(BaseModel):
    url: str = Field(..., description="Ссылка на сайт-ленд для скачивания архивом")
    lander_id: str | None = None
    task_uid: str | None = None
    proxy_id: str | None = Field(None, description="ID сохранённого прокси")
    proxy: str | None = Field(None, description="Прокси-строка (разово, не сохраняется)")


@app.post("/api/sessions/{sid}/landers/from-site")
def session_lander_from_site(sid: str, body: LanderFromSiteBody):
    """Скачивает лендинг по ссылке (Playwright, как webscrapbook) в ZIP и
    добавляет в сессию. proxy_id/proxy — обход гео-защиты. Долгая операция."""
    from services.session import get_manager
    from services.scrape import scrape_site
    from services.proxies import get_store, parse_proxy

    proxy = None
    if body.proxy_id:
        proxy = get_store().get_parsed(body.proxy_id)
        if proxy is None:
            raise HTTPException(404, "Прокси не найден")
    elif body.proxy:
        proxy = parse_proxy(body.proxy)
        if proxy is None:
            raise HTTPException(422, "Не удалось распознать прокси")
    try:
        data, filename = scrape_site(body.url.strip(), proxy=proxy)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось скачать сайт: {e}")
    name = f"{body.lander_id}.zip" if body.lander_id else filename
    try:
        ls = get_manager().add_uploaded_lander(
            sid, data, name, body.lander_id or None, task_uid=body.task_uid or None)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    from dataclasses import asdict
    return asdict(ls)


_LANDER_PREVIEW_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif",
    ".mp4", ".webm", ".mov", ".ogg",
}
_LANDER_PREVIEW_MAX = 60 * 1024 * 1024


@app.get("/api/sessions/{sid}/landers/{lid}/file")
def session_lander_file(sid: str, lid: str, path: str, output: int = 0):
    """Отдаёт файл (фото/гиф/видео) из архива ленда — для превью.

    output=1 — из output-архива (рабочей копии), напр. product.png у VSL;
    иначе — из исходного."""
    import zipfile
    from pathlib import Path as _Path
    from fastapi import Response
    from services.session import get_manager

    norm = (path or "").replace("\\", "/").strip()
    if not norm or norm.startswith("/") or ".." in norm.split("/"):
        raise HTTPException(400, "Некорректный путь")
    if _Path(norm).suffix.lower() not in _LANDER_PREVIEW_EXT:
        raise HTTPException(400, "Превью доступно только для изображений/видео")

    mgr = get_manager()
    s = mgr.get(sid)
    if s is None:
        raise HTTPException(404, "Сессия не найдена")
    ls = s.landers.get(lid)
    if ls is None:
        raise HTTPException(404, "Ленд не найден")
    zip_src = None
    if output and ls.output_name:
        p = STORAGE / "outputs" / ls.output_name
        if p.exists():
            zip_src = str(p)
    if zip_src is None:
        if not ls.zip_path or not _Path(ls.zip_path).exists():
            raise HTTPException(404, "Архив ленда не найден")
        zip_src = ls.zip_path

    try:
        with zipfile.ZipFile(zip_src, "r") as zf:
            names = zf.namelist()
            member = norm if norm in names else next(
                (n for n in names if n.replace("\\", "/") == norm), None)
            if member is None:
                raise HTTPException(404, f"Файл не найден в архиве: {path}")
            if zf.getinfo(member).file_size > _LANDER_PREVIEW_MAX:
                raise HTTPException(413, "Файл слишком большой для превью")
            data = zf.read(member)
    except zipfile.BadZipFile:
        raise HTTPException(400, "Повреждённый архив")

    import mimetypes
    media = mimetypes.guess_type(member)[0] or "application/octet-stream"
    return Response(content=data, media_type=media)


@app.get("/api/sessions/{sid}/landers/{lid}/media")
def session_lander_media(sid: str, lid: str, all: int = 0):
    """Медиа-ресурсы ленда (фото/гиф/видео) — для блока замены.

    По умолчанию только реально используемые на ленде; all=1 — включая лишние
    файлы из архива."""
    from services.session import get_manager
    try:
        return get_manager().list_lander_media(sid, lid, used_only=not all)
    except KeyError as e:
        raise HTTPException(404, str(e))


def _autoload_offer_photos(intake, mgr, sid: str, lid: str, offer: str) -> int:
    """Подтягивает фото продукта со страницы оффера AdRobot
    (/kt/offer_groups/extended/?search_term=<оффер>) в изолированные замены
    ленда. Уже скачанные (по имени) не дублируются. → сколько добавлено."""
    import logging
    import os
    import re
    log = logging.getLogger("session")
    if intake is None or not (offer or "").strip():
        return 0
    existing = {r["name"] for r in mgr.list_replacements(sid, lid)}
    try:
        urls = intake.client.get_offer_product_images(offer)
    except Exception:  # noqa: BLE001
        log.exception("Фото группы %r: не удалось получить страницу оффера", offer)
        urls = []
    base = re.sub(r"[^\w.-]+", "_", offer.strip()) or "offer"
    added = 0
    for i, url in enumerate(urls):
        ext = os.path.splitext(url.split("?")[0])[1] or ".png"
        name = f"{base}{ext}" if i == 0 else f"{base}_{i + 1}{ext}"
        if name in existing:
            continue
        try:
            data, _fn, _ct = intake.client.download_attachment(url)
            mgr.save_replacement(sid, lid, data, name)
            added += 1
        except Exception:  # noqa: BLE001
            log.exception("Фото группы %r: не удалось скачать %s", offer, url)
            continue
    log.info("Фото группы %r: найдено %d url, добавлено %d (ленд %s/%s)",
             offer, len(urls), added, sid, lid)
    return added


@app.get("/api/sessions/{sid}/landers/{lid}/replacements")
def session_lander_replacements(request: Request, sid: str, lid: str, autoload: int = 0):
    """Изолированные по задаче медиа-замены + картинки из комментариев задачи.

    autoload=1 — подтянуть фото продукта со страницы оффера (по названию оффера)
    в изолированную папку замен (если ещё не подтянуто).
    """
    from services.session import get_manager
    mgr = get_manager()
    s = mgr.get(sid)
    if s is None:
        raise HTTPException(404, "Сессия не найдена")
    ls = s.landers.get(lid)
    if ls is None:
        raise HTTPException(404, "Ленд не найден")

    intake = getattr(request.app.state, "intake", None)

    if autoload:
        # ВАЖНО: оффер с учётом ручной подмены группы — после смены группы
        # подтягивается фото продукта НОВОЙ группы.
        _autoload_offer_photos(intake, mgr, sid, lid, s.lander_offer(ls))

    # Картинки из комментариев задачи (для ручного добавления в замены).
    comment_images: list[dict] = []
    if intake is not None and ls.task_uid:
        try:
            detail = intake.client.get_task(ls.task_uid)
            for a in detail.attachments:
                if a.kind == "image" and "robotmediaassets.com" in a.url:
                    comment_images.append({"url": a.url, "filename": a.filename})
        except Exception:  # noqa: BLE001
            pass

    return {
        "replacements": mgr.list_replacements(sid, lid),
        "comment_images": comment_images,
    }


class ReplacementImportBody(BaseModel):
    url: str = Field(..., description="URL картинки/вложения для импорта в замены задачи")
    filename: str | None = None


@app.post("/api/sessions/{sid}/landers/{lid}/replacements/import")
def session_lander_replacement_import(request: Request, sid: str, lid: str,
                                      body: ReplacementImportBody):
    """Импортирует медиа из URL в изолированную по задаче папку замен."""
    from services.session import get_manager
    intake = _require_intake(request)
    try:
        data, filename, _ct = _fetch_url(intake, body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось скачать: {e}")
    try:
        name = get_manager().save_replacement(sid, lid, data, body.filename or filename)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"name": name}


@app.post("/api/sessions/{sid}/landers/{lid}/replacements/upload")
async def session_lander_replacement_upload(sid: str, lid: str,
                                            files: list[UploadFile] = File(...)):
    """Загружает локальные файлы (фото/гиф/видео) в изолированные замены задачи."""
    from services.session import get_manager
    from starlette.concurrency import run_in_threadpool
    mgr = get_manager()
    saved: list[str] = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        try:
            # save_replacement читает/пишет файлы (для видео — мегабайты) — не
            # держим event loop, уводим в threadpool.
            name = await run_in_threadpool(
                mgr.save_replacement, sid, lid, data, f.filename or "media")
        except KeyError as e:
            raise HTTPException(404, str(e))
        saved.append(name)
    if not saved:
        raise HTTPException(400, "Пустой файл(ы)")
    return {"names": saved}


@app.get("/api/sessions/{sid}/landers/{lid}/replacements/file")
def session_lander_replacement_file(sid: str, lid: str, name: str):
    """Отдаёт файл изолированной замены — для превью."""
    from fastapi.responses import FileResponse
    from services.session import get_manager
    try:
        p = get_manager().replacement_file(sid, lid, name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if p is None:
        raise HTTPException(404, "Файл замены не найден")
    return FileResponse(p)


@app.delete("/api/sessions/{sid}/landers/{lid}/replacements/file")
def session_lander_replacement_delete(sid: str, lid: str, name: str):
    """Удаляет файл из загруженных медиа (замен) задачи."""
    from services.session import get_manager
    try:
        ok = get_manager().delete_replacement(sid, lid, name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if not ok:
        raise HTTPException(404, "Файл замены не найден")
    return {"ok": True}


class RemoveBgBody(BaseModel):
    name: str = Field(..., description="Имя файла-замены, у которого удалить фон")


@app.post("/api/sessions/{sid}/landers/{lid}/replacements/remove-bg")
def session_lander_replacement_remove_bg(sid: str, lid: str, body: RemoveBgBody):
    """Удаляет фон у замены (rembg, локально) → новая замена nobg_*.png (RGBA)."""
    from services.session import get_manager
    from services.bg_remove import remove_background
    import os
    mgr = get_manager()
    try:
        p = mgr.replacement_file(sid, lid, body.name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    if p is None:
        raise HTTPException(404, "Файл замены не найден")
    try:
        out = remove_background(p.read_bytes())
    except RuntimeError as e:  # rembg не установлен
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Удаление фона: {e}")
    stem = os.path.splitext(os.path.basename(body.name))[0]
    new_name = mgr.save_replacement(sid, lid, out, f"nobg_{stem}.png")
    return {"name": new_name}


@app.get("/api/sessions/{sid}/landers/{lid}/suggest")
def session_suggest(sid: str, lid: str):
    """Черновик параметров адаптации (из scan + оффера). Требует проверки."""
    from services.session import get_manager
    try:
        return get_manager().suggest_adapt_params(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))


class LanderGroupBody(BaseModel):
    offer: str = Field("", description="Оффер/группа для ленда (пусто — сброс к офферу задачи)")


@app.put("/api/sessions/{sid}/landers/{lid}/group")
def session_set_lander_group(request: Request, sid: str, lid: str, body: LanderGroupBody):
    """Сменить «группу»/оффер ленда. Возвращает пересчитанный черновик параметров.

    Заодно подтягивает фото продукта НОВОЙ группы со страницы оффера AdRobot
    в замены ленда (photos_added в ответе)."""
    from services.session import get_manager
    mgr = get_manager()
    try:
        suggest = mgr.set_lander_group(sid, lid, body.offer)
    except KeyError as e:
        raise HTTPException(404, str(e))
    intake = getattr(request.app.state, "intake", None)
    try:
        s = mgr.get(sid)
        ls = s.landers.get(lid)
        suggest["photos_added"] = _autoload_offer_photos(
            intake, mgr, sid, lid, s.lander_offer(ls))
    except Exception:  # noqa: BLE001 — фото не критично для смены группы
        suggest["photos_added"] = 0
    return suggest


class AdaptBody(BaseModel):
    geo_id: str = ""
    product_old: str = ""
    product_new: str = ""
    price_new: str = ""
    price_old: str = ""
    price_new_num: str = ""
    price_new_cur: str = ""
    price_old_num: str = ""
    price_old_cur: str = ""
    src_price_new_num: str = ""
    src_price_new_cur: str = ""
    src_price_old_num: str = ""
    src_price_old_cur: str = ""
    exclude_word: str = ""
    image_map: dict = Field(default_factory=dict)
    custom_replacements: str = ""


@app.post("/api/sessions/{sid}/landers/{lid}/adapt")
def session_adapt(sid: str, lid: str, body: AdaptBody):
    """Применяет адаптацию к скачанному ленду. Результат → storage/outputs (+preview)."""
    from services.session import get_manager
    try:
        return get_manager().adapt_lander(sid, lid, body.model_dump())
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── VSL: конфиг, фото продукта, видео ────────────────────────
@app.get("/api/sessions/{sid}/landers/{lid}/vsl-config")
def vsl_config_get(sid: str, lid: str):
    """Читает $config из config.php ленда (output-копия, иначе исходник)."""
    from services import vsl
    try:
        return vsl.read_config(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


class VslConfigBody(BaseModel):
    config: dict = Field(..., description="Полный $config для записи в config.php")


@app.put("/api/sessions/{sid}/landers/{lid}/vsl-config")
def vsl_config_put(sid: str, lid: str, body: VslConfigBody):
    """Записывает $config в config.php рабочей копии (создаёт копию при нужде)."""
    from services import vsl
    try:
        res = vsl.write_config(sid, lid, body.config)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    # scan-сводка ленда отражает конфиг — пересчитать после правки.
    try:
        vsl.refresh_scan(sid, lid)
    except Exception:  # noqa: BLE001
        pass
    return res


# ── VSL: библиотека комментариев по вертикалям ──────────────────
@app.get("/api/vsl/comments/library")
def vsl_comments_library():
    """Сводка библиотеки VSL-комментариев: вертикаль → наборов/комментариев."""
    from services import vsl_comments
    from services.session import VERTICAL_CODE_TO_FULL
    return {"library": vsl_comments.list_library(),
            "verticals": [{"code": c, "name": n}
                          for c, n in sorted(VERTICAL_CODE_TO_FULL.items())]}


@app.post("/api/vsl/comments/harvest")
def vsl_comments_harvest():
    """Сбор комментариев из VSL-офферов Keitaro (SSE: step/vertical_done/done)."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from services.vsl_comments import harvest_stream

    def gen():
        inner = harvest_stream()
        try:
            for ev in inner:
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            inner.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


class VslCommentsApplyBody(BaseModel):
    vertical: str = Field(..., description="Код вертикали (DI, PR, PT, …)")


@app.post("/api/sessions/{sid}/landers/{lid}/vsl/comments/apply")
def vsl_comments_apply(sid: str, lid: str, body: VslCommentsApplyBody):
    """Вставить в конфиг ленда все сохранённые комментарии вертикали."""
    from services import vsl, vsl_comments
    try:
        res = vsl_comments.apply_comments(sid, lid, body.vertical)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        vsl.refresh_scan(sid, lid)
    except Exception:  # noqa: BLE001
        pass
    return res


class VslCommentsTranslateBody(BaseModel):
    target_lang: Optional[str] = Field(None, description="Язык (пусто = по гео)")


@app.post("/api/sessions/{sid}/landers/{lid}/vsl/comments/translate")
def vsl_comments_translate(sid: str, lid: str, body: VslCommentsTranslateBody):
    """Перевести имя+текст комментариев конфига (deepseek, имена под гео)."""
    from services import vsl_comments
    try:
        return vsl_comments.translate_comments(sid, lid, body.target_lang)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/sessions/{sid}/landers/{lid}/vsl-product-image")
async def vsl_product_image(sid: str, lid: str, file: UploadFile = File(...)):
    """Загружает фото продукта VSL: конвертируется в PNG, кладётся в архив
    как product.png и прописывается в config.orderForm.productImage."""
    from services import vsl
    from starlette.concurrency import run_in_threadpool
    data = await file.read()
    if not data:
        raise HTTPException(400, "Пустой файл")
    try:
        return await run_in_threadpool(vsl.set_product_image, sid, lid, data)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/sessions/{sid}/landers/{lid}/vsl-product-image/from-group")
def vsl_product_image_from_group(request: Request, sid: str, lid: str):
    """Подтягивает фото продукта ГРУППЫ ленда со страницы оффера AdRobot
    (/kt/offer_groups/extended/?search_term=<группа>) и ставит его как
    product.png в конфиг VSL. → {found, offer, name?}."""
    from services import vsl
    from services.session import get_manager
    intake = _require_intake(request)
    mgr = get_manager()
    s = mgr.get(sid)
    ls = s.landers.get(lid) if s else None
    if ls is None:
        raise HTTPException(404, "Ленд не найден")
    offer = s.lander_offer(ls)
    try:
        urls = intake.client.get_offer_product_images(offer)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AdRobot: {e}")
    if not urls:
        return {"found": False, "offer": offer}
    try:
        data, _fn, _ct = intake.client.download_attachment(urls[0])
        res = vsl.set_product_image(sid, lid, data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Не удалось скачать/поставить фото: {e}")
    return {"found": True, "offer": offer, **res}


@app.get("/api/sessions/{sid}/landers/{lid}/vsl-download")
def vsl_download(sid: str, lid: str):
    """Отдаёт архив VSL-ленда с текущим конфигом/правками (рабочую копию;
    создаёт её из шаблона, если правок ещё не было)."""
    from fastapi.responses import FileResponse
    from services import vsl
    try:
        p = vsl.ensure_output(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return FileResponse(p, filename=p.name, media_type="application/zip")


@app.post("/api/sessions/{sid}/landers/{lid}/vsl-video")
async def vsl_video_start(sid: str, lid: str,
                          file: UploadFile | None = File(None),
                          m3u8_url: str = Form("")):
    """Запускает подготовку видео VSL: mp4-файл ИЛИ ссылка m3u8 → HLS + постер
    → архив содержимого папки видео. Долгая фоновая задача — статус poll'ится."""
    from services import vsl_video
    data = await file.read() if file is not None else None
    try:
        return vsl_video.start_job(sid, lid, upload=data or None,
                                   m3u8_url=m3u8_url or None)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(422, str(e))


@app.get("/api/sessions/{sid}/landers/{lid}/vsl-video")
def vsl_video_status(sid: str, lid: str):
    """Статус подготовки видео + готовый архив (имя/размер), если есть."""
    from services import vsl_video
    try:
        return vsl_video.job_status(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))


class VslVideoNameBody(BaseModel):
    name: str = Field(..., description="Новое имя архива видео (= папки на CDN)")


@app.put("/api/sessions/{sid}/landers/{lid}/vsl-video/name")
def vsl_video_rename(sid: str, lid: str, body: VslVideoNameBody):
    """Переименовывает архив видео и обновляет ссылки src/poster в config.php
    (меняется только имя папки в ссылках)."""
    from services import vsl_video
    try:
        return vsl_video.rename_archive(sid, lid, body.name)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/sessions/{sid}/landers/{lid}/vsl-video/download")
def vsl_video_download(sid: str, lid: str):
    """Отдаёт готовый архив видео (содержимое папки для CDN)."""
    from fastapi.responses import FileResponse
    from services import vsl_video
    from services.session import get_manager
    s = get_manager().get(sid)
    ls = s.landers.get(lid) if s else None
    if ls is None:
        raise HTTPException(404, "Ленд не найден")
    name = (ls.adapt_params or {}).get("vsl_archive_name") or ""
    p = vsl_video.archive_path(sid, lid, name) if name else None
    if not p or not p.exists():
        raise HTTPException(404, "Архив видео ещё не создан")
    return FileResponse(p, filename=p.name, media_type="application/zip")


# ── История версий ленда (откат «на шаг назад») ──────────────
@app.get("/api/sessions/{sid}/landers/{lid}/history")
def session_lander_history(sid: str, lid: str):
    """Список версий output-архива ленда (шаги адаптации/правок) — для отката.
    Возвращает {versions, current}."""
    from services.session import get_manager
    try:
        return get_manager().list_history(sid, lid)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/sessions/{sid}/landers/{lid}/history/{version_id}/restore")
def session_lander_history_restore(sid: str, lid: str, version_id: str):
    """Откатить ленд к выбранной версии (текущее состояние сохраняется для возврата)."""
    from services.session import get_manager
    try:
        return get_manager().restore_version(sid, lid, version_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── Перевод ленда (AITUNNEL / deepseek) ─────────────────────
@app.get("/api/translate/languages")
def translate_languages():
    """Список поддерживаемых языков (код + название) для выпадашки."""
    from services.translate import list_languages
    return list_languages()


class TranslateBody(BaseModel):
    target_lang: str | None = Field(None, description="Целевой язык (иначе по гео)")


@app.post("/api/sessions/{sid}/landers/{lid}/translate/preview")
def translate_preview(sid: str, lid: str, body: TranslateBody):
    """Превью перевода: дифф original→translated, БЕЗ записи в архив."""
    from services.translate import translate_lander
    try:
        return translate_lander(sid, lid, target_lang=body.target_lang, execute=False)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Перевод: {e}")


@app.post("/api/sessions/{sid}/landers/{lid}/translate/apply")
def translate_apply(sid: str, lid: str, body: TranslateBody):
    """Применить перевод к адаптированному ленду (output-архив)."""
    from services.translate import translate_lander
    try:
        return translate_lander(sid, lid, target_lang=body.target_lang, execute=True)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Перевод: {e}")


@app.post("/api/sessions/{sid}/landers/{lid}/translate/stream")
def translate_stream(sid: str, lid: str, body: TranslateBody):
    """Стриминговый перевод (SSE): прогресс по блокам, сразу применяет к архиву."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from services.translate import translate_lander_stream

    def gen():
        inner = translate_lander_stream(sid, lid, target_lang=body.target_lang)
        try:
            for ev in inner:
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"
        finally:
            # Клиент оборвал SSE (кнопка «Стоп») → закрываем ВНУТРЕННИЙ генератор
            # сразу: GeneratorExit гасит пул батчей и НЕ применяет перевод.
            inner.close()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Нейро-редактор картинок ленда (GPT Image 2) ─────────────
@app.post("/api/sessions/{sid}/landers/{lid}/media/edit")
async def media_edit(sid: str, lid: str,
                     path: str = Form(...),
                     prompt: str = Form(...),
                     quality: str = Form("low"),
                     refs: list[UploadFile] = File(default=[])):
    """Редактирует картинку ленда по промпту (GPT Image 2) и заменяет её в архиве.

    refs — необязательные референсные фото (multipart): напр. «замени продукт
    на тот, что на втором фото» — второе фото прикладывается сюда."""
    from services.image_edit import edit_lander_media
    from starlette.concurrency import run_in_threadpool
    ref_bytes: list[bytes] = []
    for f in refs or []:
        data = await f.read()
        if data:
            ref_bytes.append(data)
    try:
        # Нейро-правка — долгий блокирующий вызов внешнего API. В event loop он
        # заморозил бы весь бэк — выносим в threadpool.
        return await run_in_threadpool(
            edit_lander_media, sid, lid, path, prompt,
            quality=quality or "low", ref_images=ref_bytes or None)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Image edit: {e}")


# ── Заливка ленда в Keitaro (создание оффера) ───────────────
@app.get("/api/sessions/{sid}/landers/{lid}/keitaro-plan")
def keitaro_plan(sid: str, lid: str, type: str | None = None, adult: int = 0):
    """Dry-run план заливки (БЕЗ обращения к Keitaro): что будет создано."""
    from services.keitaro_upload import prepare_plan
    try:
        plan = prepare_plan(sid, lid, site_type=type, adult=bool(adult))
        plan["mode"] = "dry-run"
        return plan
    except ValueError as e:
        raise HTTPException(422, str(e))


class KeitaroUploadBody(BaseModel):
    type: str | None = Field(None, description="Переопределить тип сайта (land|pl|vsl)")
    network: str | None = Field(None, description="Принудительно задать партнёрскую сеть (номер)")
    adult: bool = Field(False, description="Adult-пометка в названии: [pl fi -] → [pl fi adult]")


@app.post("/api/sessions/{sid}/landers/{lid}/keitaro-upload")
def keitaro_upload(sid: str, lid: str, body: KeitaroUploadBody):
    """РЕАЛЬНАЯ заливка: создаёт оффер и возвращает КАНДИДАТОВ на id
    (mode=created_pending_rename). Переименование — отдельным шагом после
    подтверждения id пользователем (см. keitaro-rename).
    """
    from services.keitaro_upload import upload
    try:
        return upload(sid, lid, execute=True, site_type=body.type,
                      network_override=body.network, adult=body.adult)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Keitaro: {e}")


class KeitaroRenameBody(BaseModel):
    offer_id: int = Field(..., description="Подтверждённый пользователем id оффера")
    type: str | None = Field(None, description="Тип сайта (для сборки имени)")
    adult: bool = Field(False, description="Adult-пометка в названии")


@app.post("/api/sessions/{sid}/landers/{lid}/keitaro-rename")
def keitaro_rename(sid: str, lid: str, body: KeitaroRenameBody):
    """Переименовывает ПОДТВЕРЖДЁННЫЙ оффер: дописывает id в название.

    Запускается ТОЛЬКО после того, как пользователь проверил/выбрал id в UI."""
    from services.keitaro_upload import rename_offer
    try:
        return rename_offer(sid, lid, body.offer_id, site_type=body.type,
                            adult=body.adult)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Keitaro: {e}")


def _resolve_lander_task(sid: str, lid: str):
    """Ленд → его задача. ВАЖНО для объединённых сессий: task_uid берётся из
    САМОГО ленда, поэтому вариант/ревью физически не могут уйти в чужую задачу.
    Возвращает (mgr, session, lander, task_uid)."""
    from services.session import get_manager
    mgr = get_manager()
    s = mgr.get(sid)
    ls = s.landers.get(lid) if s else None
    if ls is None:
        raise HTTPException(404, f"Ленд {lid} не найден в сессии {sid}")
    uid = ls.task_uid
    if not uid and len(s.tasks) == 1:
        uid = s.tasks[0].get("uid")
    if not uid:
        raise HTTPException(
            422, "У ленда нет привязки к задаче, а в сессии несколько задач — "
                 "не могу определить задачу для действия")
    return mgr, s, ls, uid


def _mark_task_landers(mgr, s, task_uid: str, **flags) -> None:
    """Ставит флаги в adapt_params ВСЕМ лендам указанной задачи (move/review —
    действия уровня задачи, статус должен отразиться на всех её лендах)."""
    default_uid = s.tasks[0].get("uid") if len(s.tasks) == 1 else None
    for l in s.landers.values():
        if (l.task_uid or default_uid) == task_uid:
            l.adapt_params = {**(l.adapt_params or {}), **flags}
    mgr._save(s)


@app.post("/api/sessions/{sid}/landers/{lid}/task-variant")
def task_add_variant(request: Request, sid: str, lid: str):
    """Добавляет залитый ленд ВАРИАНТОМ в его задачу AdRobot (Add variant).

    task_uid и offer_id определяются на сервере из самого ленда."""
    intake = _require_intake(request)
    mgr, s, ls, uid = _resolve_lander_task(sid, lid)
    offer_id = (ls.adapt_params or {}).get("keitaro_offer_id")
    if not offer_id:
        raise HTTPException(422, "Сначала залей ленд в Keitaro (нет id оффера)")
    try:
        intake.client.add_variant(uid, offer_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AdRobot: {e}")
    ls.adapt_params = {**(ls.adapt_params or {}),
                       "variant_added": True, "variant_task_uid": uid}
    mgr._save(s)
    return {"task_uid": uid, "offer_id": offer_id,
            "task_title": ls.task_title or ""}


class VariantsMoveBody(BaseModel):
    scope: str = Field(..., description="private | public")


@app.post("/api/sessions/{sid}/landers/{lid}/task-variants-move")
def task_move_variants(request: Request, sid: str, lid: str, body: VariantsMoveBody):
    """«Move all to private/public group» для ЗАДАЧИ этого ленда."""
    intake = _require_intake(request)
    mgr, s, ls, uid = _resolve_lander_task(sid, lid)
    try:
        intake.client.move_variants(uid, body.scope)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AdRobot: {e}")
    _mark_task_landers(mgr, s, uid, variants_moved=body.scope)
    return {"task_uid": uid, "scope": body.scope}


@app.post("/api/sessions/{sid}/landers/{lid}/task-review")
def task_submit_review(request: Request, sid: str, lid: str):
    """«Submit for review» для ЗАДАЧИ этого ленда (статус → REVIEW)."""
    intake = _require_intake(request)
    mgr, s, ls, uid = _resolve_lander_task(sid, lid)
    try:
        detail = intake.client.submit_review(uid)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"AdRobot: {e}")
    _mark_task_landers(mgr, s, uid, review_submitted=True)
    # обновим кэш задач — в списке статус сразу станет REVIEW
    try:
        intake.fetch_relevant()
    except Exception:  # noqa: BLE001
        pass
    return {"task_uid": uid, "status": "REVIEW",
            "task_title": detail.title or ls.task_title or ""}


@app.post("/api/sessions/{sid}/landers/{lid}/test-campaign")
def keitaro_test_campaign(sid: str, lid: str):
    """Создаёт тестовую кампанию (test mch …, группа Andrei AM) для залитого
    ленда и возвращает ссылку на неё."""
    from services.keitaro_upload import create_test_campaign
    try:
        return create_test_campaign(sid, lid)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Keitaro: {e}")


# ── История опубликованных лендов ────────────────────────────
@app.get("/api/published")
def published_list(period: str = "day"):
    """Сгруппированная история публикаций (period=day|week|month|all).
    Группы — свежие сверху, id внутри группы по возрастанию."""
    from services.publish_history import get_history
    h = get_history()
    return {"period": period, "total": h.total(), "groups": h.grouped(period)}


class PublishAddBody(BaseModel):
    id: int = Field(..., description="id опубликованного оффера")
    date: str | None = Field(None, description="дата YYYY-MM-DD (по умолчанию сегодня)")


@app.post("/api/published")
def published_add(body: PublishAddBody):
    """Добавить id публикации вручную (встанет на своё место по дате/возрастанию)."""
    from services.publish_history import get_history
    try:
        rec = get_history().add(body.id, date_str=body.date or "")
    except ValueError as e:
        raise HTTPException(422, str(e))
    return rec


@app.delete("/api/published/{offer_id}")
def published_delete(offer_id: int):
    """Удалить id из истории публикаций."""
    from services.publish_history import get_history
    if not get_history().remove(offer_id):
        raise HTTPException(404, "id не найден в истории")
    return {"ok": True}


@app.post("/api/sessions/{sid}/landers/{lid}/keitaro-upload/stream")
def keitaro_upload_stream(sid: str, lid: str, body: KeitaroUploadBody):
    """Заливка в Keitaro со стримом прогресса (SSE): выбираю страну, заполняю
    имя, выбираю сеть и т.д. События: step / done / error."""
    import json as _json
    import queue
    import threading
    from fastapi.responses import StreamingResponse
    from services.keitaro_upload import upload

    q: queue.Queue = queue.Queue()

    def _progress(msg: str) -> None:
        q.put({"type": "step", "message": msg})

    def _run() -> None:
        try:
            res = upload(sid, lid, execute=True, site_type=body.type,
                         network_override=body.network, adult=body.adult,
                         on_progress=_progress)
            q.put({"type": "done", "result": res})
        except Exception as e:  # noqa: BLE001
            q.put({"type": "error", "error": str(e)})
        finally:
            q.put(None)

    def gen():
        threading.Thread(target=_run, daemon=True, name=f"keitaro-upload-{sid}-{lid}").start()
        while True:
            ev = q.get()
            if ev is None:
                break
            yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Названия лендов из Keitaro (для подсказки при вводе ID) ──
class OfferNamesBody(BaseModel):
    ids: list[str] = Field(default_factory=list)


@app.post("/api/keitaro/offer-names")
def keitaro_offer_names(body: OfferNamesBody):
    """Названия офферов-доноров по ID из Keitaro (для подсказки при добавлении).

    Кэшируется в storage/keitaro/offer_names.json — повторные ID не дёргают
    браузер. Только отсутствующие в кэше тянутся из Keitaro одним сеансом.
    """
    import json as _json
    import re as _re
    ids = []
    for raw in body.ids:
        v = _re.sub(r"\D", "", str(raw))
        if v and v not in ids:
            ids.append(v)
    if not ids:
        return {}

    cache_path = STORAGE / "keitaro" / "offer_names.json"
    cache: dict = {}
    if cache_path.exists():
        try:
            cache = _json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cache = {}

    result = {i: cache[i] for i in ids if i in cache and cache[i]}
    missing = [i for i in ids if i not in result]

    if missing:
        from connectors.keitaro import client_from_env
        try:
            with client_from_env() as kt:
                fetched = kt.get_offer_names(missing)
        except Exception as e:  # noqa: BLE001
            # Keitaro недоступен — отдаём что есть из кэша, остальные null.
            for i in missing:
                result[i] = None
            return {"names": result, "error": f"Keitaro: {e}"}
        for i, name in fetched.items():
            result[i] = name
            if name:
                cache[i] = name
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(_json.dumps(cache, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    return {"names": result}


# ── Чат-агент по ленду (AITUNNEL / Kimi) ────────────────────
@app.get("/api/ai/status")
def ai_status():
    """Настроен ли AI-агент (ключ AITUNNEL и/или локальная модель) + баланс."""
    import os
    from connectors.aitunnel import (client_from_env, DEFAULT_MODEL,
                                     available_models, local_llm_info)
    client = client_from_env()
    local = local_llm_info()
    if client is None:
        return {"configured": False, "model": None, "balance": None,
                "models": [], "local": None}
    return {
        "configured": True,
        "model": os.getenv("AITUNNEL_MODEL", DEFAULT_MODEL),
        "balance": client.balance(),
        "models": available_models(),
        "local": local,  # {base_url, model} | null — локальный сервер
    }


@app.get("/api/sessions/{sid}/landers/{lid}/chat")
def lander_chat_history(sid: str, lid: str):
    """История чата по ленду."""
    from services.session import get_manager
    mgr = get_manager()
    s = mgr.get(sid)
    if s is None:
        raise HTTPException(404, "Сессия не найдена")
    ls = s.landers.get(lid)
    if ls is None:
        raise HTTPException(404, "Ленд не найден")
    return {"messages": ls.chat or []}


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1)
    model: str | None = Field(None, description="ID модели (kimi/qwen/deepseek); пусто — дефолт")


@app.post("/api/sessions/{sid}/landers/{lid}/chat")
def lander_chat_send(request: Request, sid: str, lid: str, body: ChatBody):
    """Отправить сообщение агенту. Гоняет tool-calling цикл, возвращает новые
    сообщения и обновлённое состояние ленда (адаптация могла измениться)."""
    from services.session import get_manager
    from services.agent import build_agent
    mgr = get_manager()
    intake = getattr(request.app.state, "intake", None)
    agent = build_agent(mgr, intake=intake)
    if agent is None:
        raise HTTPException(503, "AI-агент не настроен — задай AITUNNEL_API_KEY в .env")
    try:
        new_messages = agent.run(sid, lid, body.message.strip(), model=body.model or None)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Ошибка агента: {e}")
    from dataclasses import asdict
    s = mgr.get(sid)
    return {"new_messages": new_messages, "lander": asdict(s.landers[lid])}


@app.post("/api/sessions/{sid}/landers/{lid}/chat/stream")
def lander_chat_stream(request: Request, sid: str, lid: str, body: ChatBody):
    """Стриминговый чат (SSE): токены ответа + события tool-вызовов."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from services.session import get_manager
    from services.agent import build_agent
    mgr = get_manager()
    intake = getattr(request.app.state, "intake", None)
    agent = build_agent(mgr, intake=intake)
    if agent is None:
        raise HTTPException(503, "AI-агент не настроен — задай AITUNNEL_API_KEY в .env")

    def gen():
        try:
            for ev in agent.run_stream(sid, lid, body.message.strip(), model=body.model or None):
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.delete("/api/sessions/{sid}/landers/{lid}/chat")
def lander_chat_clear(sid: str, lid: str):
    """Очистить историю чата по ленду."""
    from services.session import get_manager
    mgr = get_manager()
    s = mgr.get(sid)
    if s is None:
        raise HTTPException(404, "Сессия не найдена")
    ls = s.landers.get(lid)
    if ls is None:
        raise HTTPException(404, "Ленд не найден")
    ls.chat = []
    mgr._save(s)
    return {"ok": True}


# ── Подключаем роуты обработки ──────────────────────────────
from api import router as processing_router
app.include_router(processing_router)
