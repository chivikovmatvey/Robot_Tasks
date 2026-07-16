"""AdaptationSession — ядро рабочего потока адаптации.

Жизненный цикл:
  1. create_from_task / create_manual — завести сессию по задаче (ID лендов + оффер).
  2. prepare() (в фоне) — для каждого ленда: скачать из Keitaro -> scan.
  3. (дальше) adapt / preview / правки / upload — отдельными шагами.

Хранилище: storage/sessions/<sid>.json (метаданные) и
storage/sessions/<sid>/<lander_id>.zip (скачанные архивы).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from utils import runners

log = logging.getLogger("session")

# Ключи adapt_params со статусом заливки/публикации ленда (Keitaro-оффер,
# тестовая кампания, вариант/ревью в AdRobot, имя VSL-видеоархива).
# ПЕРЕЖИВАЮТ повторную адаптацию и переустановку — по ним видно, заливался
# ли ленд; затираются только у дубля (копия НЕ заливалась).
PUBLISH_KEYS = (
    "keitaro_offer_id", "keitaro_name",
    "campaign_url", "campaign_name",
    "variant_added", "variant_task_uid", "variants_moved", "review_submitted",
    "vsl_archive_name",
)

BASE_DIR = Path(__file__).resolve().parents[1]
SESSIONS_DIR = BASE_DIR / "storage" / "sessions"

# Сессии в архиве хранятся 1 день с момента перемещения, потом стираются.
ARCHIVE_TTL_SECONDS = 24 * 60 * 60

# Медиа-ресурсы ленда, которые можно заменять (фото/гиф/видео).
_VIDEO_EXT = {".mp4", ".webm", ".mov", ".ogg"}
_MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif"} | _VIDEO_EXT
# Текстовые файлы, в которых ищем ссылки на медиа (определение «используется»).
_USAGE_TEXT_EXT = {".html", ".htm", ".php", ".css", ".js", ".json", ".blink"}

# Текстовые файлы адаптированного ленда, доступные агенту для правок.
# .blink — CSS из сохранённых Chrome-ом страниц (mhtml), обрабатываем как css.
_EDIT_TEXT_EXT = {".html", ".htm", ".php", ".css", ".js", ".json", ".txt", ".xml",
                  ".blink"}


# ── статусы ──────────────────────────────────────────────────────
class LanderStatus:
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    SCANNING = "scanning"
    READY = "ready"
    ADAPTING = "adapting"
    ADAPTED = "adapted"
    ERROR = "error"


# Модификаторы названия продукта, которые не являются самим продуктом.
# 'adult' — служебная пометка группы: она уходит в скобку [pl xx adult]
# и не должна попадать ни в название оффера, ни в видимый текст ленда.
_PRODUCT_MODIFIERS = {"low", "resell", "misslead", "mislead", "pro", "plus", "2", "3", "adult"}

# Код вертикали в имени группы оффера → ПОЛНОЕ название для скобки Keitaro
# [VERTICAL-GEO] (как в реальных офферах: [HYPERTENSION-CZ], [PROSTATITIS-CL-...]).
VERTICAL_CODE_TO_FULL = {
    "PT": "POTENCY", "PR": "PROSTATITIS", "JO": "JOINT", "HY": "HYPERTENSION",
    "PA": "PARASITES", "BE": "BEAUTY", "DI": "DIABETES", "VI": "VISION",
    "HE": "HEARING", "HA": "HAIR", "EN": "ENERGY", "SF": "SLIM",
    "BR": "VARICOSIS", "VA": "VARICOSIS", "CY": "CYSTITIS", "FU": "FUNGUS",
    "WL": "WEIGHT LOSS", "SW": "WHITENING", "GS": "GASTRITIS", "ME": "MENOPAUSE",
    "DG": "DIGESTION",
}
# Код вертикали → exclude_word адаптации (см. VERTICALS в scripts/inject.py).
VERTICAL_CODE_TO_EXCLUDE = {
    "PT": "pt ", "PR": "pr ", "JO": "jo ", "HY": "hy ", "PA": "pa ",
    "BE": "be ", "DI": "di ", "VI": "vi ", "HE": "he ", "HA": "ha ",
    "EN": "en ", "SF": "sf ", "BR": "br ", "VA": "va ", "CY": "cy ",
    "FU": "fu ", "WL": "wl ", "SW": "sw ", "GS": "gs ", "ME": "me ",
    "DG": "dg ",
}


class SessionStatus:
    PREPARING = "preparing"
    READY = "ready"
    ERROR = "error"


# ── парсинг задачи ───────────────────────────────────────────────
def extract_lander_ids(fields: dict[str, str]) -> list[str]:
    """Достаёт ID лендов-доноров из карточки задачи.

    Источники (по приоритету):
      - 'Reference lander' — обычно содержит '... (ID: 9224)';
      - 'Description'      — часто просто список чисел (9224, 14278).
    Возвращает уникальные id в порядке появления.
    """
    ids: list[str] = []

    def _add(val: str):
        if val and val not in ids:
            ids.append(val)

    ref = fields.get("Reference lander", "") or ""
    # Явный (ID: N) — самый надёжный источник, число любой длины.
    for m in re.findall(r"\(ID:\s*(\d+)\)", ref):
        _add(m)
    # Ведущее число "18525 Calmano ..." — id ленда это 4-5 цифр (отсев мусора:
    # цен «299», годов, слишком длинных чисел). См. также фронт NewSessionPage.
    for line in ref.splitlines():
        m = re.match(r"\s*(\d{4,5})\b", line)
        if m:
            _add(m.group(1))

    desc = fields.get("Description", "") or ""
    # Числа из описания (строки вида "18525" / "9224") — только 4-5 цифр.
    for m in re.findall(r"(?<!\d)(\d{4,5})(?!\d)", desc):
        _add(m)

    return ids


def offer_key(offer: str) -> str:
    """Канонический ключ оффера для группировки задач: строка с нормализованными
    пробелами и в верхнем регистре. 'VA Ultravix  Low mx' → 'VA ULTRAVIX LOW MX'.

    Решение пользователя: группируем по ТОЧНОЙ строке оффера (после нормализации),
    а не по product+geo — чтобы не слить разные модификации одного продукта.
    """
    return " ".join((offer or "").split()).upper()


def parse_target_offer(offer: str, geos: Optional[dict] = None) -> dict:
    """Разбирает целевой оффер вида '<VERTICAL> <PRODUCT..> <GEO>'.

    Пример: 'HY Pulsactive Resell CZ' →
      vertical='HY', vertical_full='HYPERTENSION', geo_id='CZ',
      product='Pulsactive Resell' (ПОЛНОЕ имя — для названия оффера в Keitaro),
      product_search='Pulsactive' (ядро без модификаторов — для поиска/адаптации),
      exclude_word='hy '.
    """
    tokens = (offer or "").split()
    out = {"vertical": "", "vertical_full": "", "product": "",
           "product_search": "", "geo_id": "", "exclude_word": "", "raw": offer}
    if not tokens:
        return out
    geo_keys = set(geos.keys()) if geos else None
    # ГЕО — последний токен, если похож на код (есть в geos / 2-3 заглавные буквы).
    last = tokens[-1].upper()
    if (geo_keys and last in geo_keys) or (re.fullmatch(r"[A-Z]{2,3}", last)):
        out["geo_id"] = last
        tokens = tokens[:-1]
    if tokens:
        first = tokens[0].upper()
        # Первый токен — код вертикали (известный код или 2 заглавные буквы).
        if first in VERTICAL_CODE_TO_FULL or re.fullmatch(r"[A-Z]{2}", first):
            out["vertical"] = first
            out["vertical_full"] = VERTICAL_CODE_TO_FULL.get(first, first)
            out["exclude_word"] = VERTICAL_CODE_TO_EXCLUDE.get(first, "")
            middle = tokens[1:]
        else:
            middle = tokens
        # ПОЛНЫЙ продукт (с модификаторами) — для названия оффера.
        # 'adult' вычищаем и отсюда: в названии он живёт в скобке, не в продукте.
        out["product"] = " ".join(t for t in middle if t.lower() != "adult")
        # Ядро бренда — непрерывный префикс до первого модификатора (Resell/Low/…),
        # чтобы оставался подстрокой для фильтра грида Keitaro и адаптации.
        core: list[str] = []
        for t in middle:
            if t.lower() in _PRODUCT_MODIFIERS:
                break
            core.append(t)
        out["product_search"] = " ".join(core) if core else out["product"]
    return out


def parse_donor_product(offer_name: str) -> str:
    """Из названия донора '9224 Calmano [VARICOSIS-PE-VA_0050] ...' → 'Calmano'."""
    if not offer_name:
        return ""
    head = offer_name.split("[")[0]
    head = re.sub(r"^\s*\d{2,7}\s*", "", head)  # убрать ведущий id
    return head.strip()


def split_price(s: str) -> tuple[str, str]:
    """'590 MXN' → ('590','MXN'); 'S/149' → ('149','S/'); '' → ('','')."""
    s = (s or "").strip()
    if not s:
        return "", ""
    m = re.search(r"[\d.,]+", s)
    if not m:
        return "", s
    num = m.group(0)
    cur = (s[: m.start()] + s[m.end():]).strip()
    return num, cur


def double_num(num: str) -> str:
    """Удваивает числовую цену, сохраняя «целочисленность»: '299'→'598', '12.5'→'25'."""
    n = (num or "").strip().replace(",", ".")
    if not n:
        return ""
    try:
        v = float(n) * 2
    except ValueError:
        return ""
    return str(int(v)) if v == int(v) else str(v)


# ── модель ───────────────────────────────────────────────────────
@dataclass
class LanderState:
    lander_id: str
    status: str = LanderStatus.QUEUED
    display_name: Optional[str] = None  # пользовательское имя вкладки (переименование)
    task_uid: Optional[str] = None      # из какой задачи пришёл ленд (для проверки)
    task_title: Optional[str] = None
    offer_override: Optional[str] = None  # ручная подмена «группы»/оффера для этого ленда
    zip_path: Optional[str] = None      # скачанный архив
    zip_name: Optional[str] = None
    size: Optional[int] = None
    offer_name: Optional[str] = None    # название оффера-донора в Keitaro
    scan: Optional[dict] = None         # результат run_scan_only
    output_name: Optional[str] = None   # имя адаптированного zip в storage/outputs
    output_url: Optional[str] = None    # /api/download/<name>
    adapt_params: Optional[dict] = None # параметры последней адаптации
    adapt_log: list[dict] = field(default_factory=list)  # лог последнего run_adapt
    error: Optional[str] = None
    chat: list[dict] = field(default_factory=list)  # история правок по ленду
    # История версий output-архива (снимки перед/после мутаций) — для отката.
    # Каждый элемент: {id, label, created_at, output_name, size}. См. _snapshot_output.
    history: list[dict] = field(default_factory=list)
    current_version: Optional[str] = None  # id текущей версии в history (для дропдауна)


# Источник-задача внутри сессии (одна сессия может покрывать несколько задач
# на ОДИН оффер — баер иногда заводит N задач по 1 ленду вместо 1 задачи на N).
@dataclass
class TaskRef:
    uid: str
    title: str = ""
    offer: str = ""
    url: str = ""
    fields: dict[str, str] = field(default_factory=dict)


@dataclass
class AdaptationSession:
    id: str
    task_uid: Optional[str]             # «первичная» задача (backward-compat)
    task_title: str
    offer: str                          # целевой оффер ("VA Ultravix Low MX")
    fields: dict[str, str] = field(default_factory=dict)  # поля первичной задачи
    tasks: list[dict] = field(default_factory=list)  # все задачи-источники (TaskRef)
    landers: dict[str, LanderState] = field(default_factory=dict)
    status: str = SessionStatus.PREPARING
    created_at: float = field(default_factory=time.time)
    archived_at: Optional[float] = None  # момент перемещения в архив (None = активна)
    is_vsl: bool = False                 # VSL-сессия: работа через config.php шаблона

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expires_at"] = (
            self.archived_at + ARCHIVE_TTL_SECONDS if self.archived_at else None
        )
        return d

    def task_fields(self, uid: Optional[str]) -> dict:
        """Поля конкретной задачи-источника (для per-ленд параметров)."""
        if uid:
            for t in self.tasks:
                if t.get("uid") == uid:
                    return t.get("fields") or {}
        return self.fields

    def task_offer(self, uid: Optional[str]) -> str:
        if uid:
            for t in self.tasks:
                if t.get("uid") == uid and t.get("offer"):
                    return t["offer"]
        return self.offer

    def lander_offer(self, ls: "LanderState") -> str:
        """Эффективный оффер/группа ленда: ручная подмена, иначе оффер задачи."""
        ov = (getattr(ls, "offer_override", None) or "").strip()
        return ov or self.task_offer(ls.task_uid)


# ── менеджер ─────────────────────────────────────────────────────
class SessionManager:
    def __init__(self, sessions_dir: Path = SESSIONS_DIR):
        self.dir = sessions_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, AdaptationSession] = {}
        self._lock = threading.Lock()
        self._load_all()

    # ── персистентность ──────────────────────────────────────────
    def _meta_path(self, sid: str) -> Path:
        return self.dir / f"{sid}.json"

    def _persist(self, s: AdaptationSession) -> None:
        self._meta_path(s.id).write_text(
            json.dumps(s.to_dict(), ensure_ascii=False, indent=2)
        )

    def _load_all(self) -> None:
        from dataclasses import fields as _fields
        known = {fld.name for fld in _fields(AdaptationSession)}
        for f in self.dir.glob("*.json"):
            try:
                raw = json.loads(f.read_text())
                landers = {
                    lid: LanderState(**ls) for lid, ls in raw.get("landers", {}).items()
                }
                raw["landers"] = landers
                # Отбрасываем вычисляемые/неизвестные ключи (напр. expires_at).
                raw = {k: v for k, v in raw.items() if k in known}
                self._sessions[raw["id"]] = AdaptationSession(**raw)
            except Exception:  # noqa: BLE001
                log.warning("Не загрузить сессию %s", f.name)

    # ── создание ─────────────────────────────────────────────────
    def create_manual(self, lander_ids: list[str], offer: str,
                      task_uid: Optional[str] = None,
                      task_title: str = "", fields: Optional[dict] = None,
                      task_url: str = "", is_vsl: bool = False) -> AdaptationSession:
        sid = uuid.uuid4().hex[:12]
        # VSL-сессия всегда начинается с эталонного шаблона (19201) — на его
        # основе делаются все VSL-ленды; доноры из задачи идут следом.
        if is_vsl:
            from services.vsl import VSL_TEMPLATE_ID
            if VSL_TEMPLATE_ID not in lander_ids:
                lander_ids = [VSL_TEMPLATE_ID, *lander_ids]
        landers = {
            lid: LanderState(lander_id=lid, task_uid=task_uid, task_title=task_title)
            for lid in lander_ids
        }
        tasks: list[dict] = []
        if task_uid:
            tasks.append(asdict(TaskRef(
                uid=task_uid, title=task_title, offer=offer,
                url=task_url, fields=fields or {},
            )))
        s = AdaptationSession(
            id=sid,
            task_uid=task_uid,
            task_title=task_title or (offer or "Адаптация"),
            offer=offer,
            fields=fields or {},
            tasks=tasks,
            landers=landers,
            status=SessionStatus.PREPARING if lander_ids else SessionStatus.READY,
            is_vsl=is_vsl,
        )
        with self._lock:
            self._sessions[sid] = s
            self._persist(s)
        log.info("Создана сессия %s: ленды=%s оффер=%s", sid, lander_ids, offer)
        return s

    @staticmethod
    def _detail_parts(detail) -> tuple[str, str, str, str, dict]:
        """Из TaskDetail (или dict) → (uid, title, offer, url, fields)."""
        fields = getattr(detail, "fields", None) or detail.get("fields", {}) or {}
        title = getattr(detail, "title", None) or detail.get("title", "") or ""
        uid = getattr(detail, "uid", None) or detail.get("uid")
        url = getattr(detail, "url", None) or detail.get("url", "") or ""
        offer = fields.get("Offer", "") or ""
        return uid, title, offer, url, fields

    def create_from_task(self, detail, is_vsl: bool = False) -> AdaptationSession:
        """detail — TaskDetail (или dict с .fields/.title/.uid)."""
        uid, title, offer, url, fields = self._detail_parts(detail)
        # ID может не распарситься — это ок: создаём сессию пустой, ленды
        # пользователь добавит вручную или загрузит архивом.
        ids = extract_lander_ids(fields)
        return self.create_manual(ids, offer, task_uid=uid, task_title=title,
                                  fields=fields, task_url=url, is_vsl=is_vsl)

    def create_from_tasks(self, details: list) -> AdaptationSession:
        """Создаёт ОДНУ сессию из нескольких задач на один оффер.

        Сценарий: баер завёл N задач (каждая на 1 ленд) вместо одной задачи на N
        лендов. Объединяем ленды в одну сессию, но КАЖДЫЙ ленд помечаем своей
        задачей (LanderState.task_uid) — чтобы потом отправлять на проверку
        отдельно по каждой задаче.
        """
        if not details:
            raise ValueError("Пустой список задач")
        if len(details) == 1:
            return self.create_from_task(details[0])

        sid = uuid.uuid4().hex[:12]
        primary_uid, primary_title, primary_offer, primary_url, primary_fields = \
            self._detail_parts(details[0])

        tasks: list[dict] = []
        landers: dict[str, LanderState] = {}
        for d in details:
            uid, title, offer, url, fields = self._detail_parts(d)
            tasks.append(asdict(TaskRef(
                uid=uid, title=title, offer=offer, url=url, fields=fields,
            )))
            for lid in extract_lander_ids(fields):
                if lid in landers:
                    continue  # ленд уже от другой задачи — оставляем первую привязку
                landers[lid] = LanderState(
                    lander_id=lid, task_uid=uid, task_title=title,
                )

        s = AdaptationSession(
            id=sid,
            task_uid=primary_uid,
            task_title=primary_title or (primary_offer or "Адаптация"),
            offer=primary_offer,
            fields=primary_fields,
            tasks=tasks,
            landers=landers,
            status=SessionStatus.PREPARING if landers else SessionStatus.READY,
        )
        with self._lock:
            self._sessions[sid] = s
            self._persist(s)
        log.info("Создана объединённая сессия %s: задач=%d ленды=%s оффер=%s",
                 sid, len(tasks), list(landers), primary_offer)
        return s

    def add_task(self, sid: str, detail) -> AdaptationSession:
        """Подмешивает ещё одну задачу того же оффера в существующую сессию.

        Полезно, когда N+1-я задача на тот же оффер пришла позже. Её ленды
        добавляются и помечаются её task_uid; запускается prepare.
        """
        s = self.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")
        uid, title, offer, url, fields = self._detail_parts(detail)
        if uid and not any(t.get("uid") == uid for t in s.tasks):
            s.tasks.append(asdict(TaskRef(
                uid=uid, title=title, offer=offer, url=url, fields=fields,
            )))
        added = False
        for lid in extract_lander_ids(fields):
            if lid not in s.landers:
                s.landers[lid] = LanderState(
                    lander_id=lid, task_uid=uid, task_title=title,
                )
                added = True
        if added:
            s.status = SessionStatus.PREPARING
        self._save(s)
        if added:
            self.prepare_async(sid)
        return s

    # ── доступ ───────────────────────────────────────────────────
    def get(self, sid: str) -> Optional[AdaptationSession]:
        self._cleanup_expired()
        return self._sessions.get(sid)

    def list(self, archived: bool = False) -> list[dict]:
        self._cleanup_expired()
        items = [
            s for s in self._sessions.values()
            if bool(s.archived_at) == archived
        ]
        # Активные — по дате создания, архивные — по дате архивации (свежие выше).
        items.sort(
            key=lambda x: (x.archived_at or x.created_at),
            reverse=True,
        )
        return [
            {
                "id": s.id,
                "task_title": s.task_title,
                "offer": s.offer,
                "status": s.status,
                "is_vsl": s.is_vsl,
                "created_at": s.created_at,
                "archived_at": s.archived_at,
                "expires_at": (s.archived_at + ARCHIVE_TTL_SECONDS
                               if s.archived_at else None),
                "task_count": len(s.tasks),
                "tasks": [
                    {"uid": t.get("uid"), "title": t.get("title"), "url": t.get("url")}
                    for t in s.tasks
                ],
                "landers": {
                    lid: {"lander_id": lid, "status": ls.status,
                          "task_uid": ls.task_uid}
                    for lid, ls in s.landers.items()
                },
            }
            for s in items
        ]

    # ── архив ────────────────────────────────────────────────────
    def archive(self, sid: str) -> AdaptationSession:
        s = self.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")
        s.archived_at = time.time()
        self._save(s)
        log.info("Сессия %s перемещена в архив (удалится через %dч)",
                 sid, ARCHIVE_TTL_SECONDS // 3600)
        return s

    def unarchive(self, sid: str) -> AdaptationSession:
        s = self.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")
        s.archived_at = None
        self._save(s)
        log.info("Сессия %s восстановлена из архива", sid)
        return s

    def delete(self, sid: str) -> None:
        """Полностью стирает сессию: метаданные и папку с архивами лендов."""
        import shutil
        with self._lock:
            self._sessions.pop(sid, None)
            self._meta_path(sid).unlink(missing_ok=True)
            shutil.rmtree(self.dir / sid, ignore_errors=True)
        log.info("Сессия %s полностью удалена", sid)

    def _cleanup_expired(self) -> None:
        """Удаляет архивные сессии старше ARCHIVE_TTL_SECONDS."""
        now = time.time()
        expired = [
            sid for sid, s in list(self._sessions.items())
            if s.archived_at and (now - s.archived_at) >= ARCHIVE_TTL_SECONDS
        ]
        for sid in expired:
            self.delete(sid)

    # ── подготовка (скачать + скан) ──────────────────────────────
    def _save(self, s: AdaptationSession) -> None:
        with self._lock:
            self._persist(s)

    def prepare(self, sid: str) -> None:
        """Синхронно: скачивает и сканирует все ленды сессии.

        Скачивание — через ОДИН Keitaro-браузер на всю сессию (быстрее).
        """
        s = self.get(sid)
        if s is None:
            raise KeyError(sid)
        s.status = SessionStatus.PREPARING
        self._save(s)

        sess_dir = self.dir / sid
        sess_dir.mkdir(parents=True, exist_ok=True)

        from connectors.keitaro import client_from_env

        had_error = False
        try:
            # Ленды, которые надо скачать из Keitaro (без уже готового zip,
            # напр. загруженных архивом).
            todo = {
                lid: ls for lid, ls in s.landers.items()
                if not (ls.zip_path and Path(ls.zip_path).exists())
                and ls.status not in (LanderStatus.READY, LanderStatus.ADAPTED)
            }
            if not todo:
                s.status = SessionStatus.READY
                self._save(s)
                return

            log.info("prepare[%s]: к скачиванию из Keitaro %d ленд(ов): %s",
                     sid, len(todo), list(todo))
            log.info("prepare[%s]: поднимаю браузер Keitaro…", sid)
            with client_from_env() as kt:
                log.info("prepare[%s]: браузер готов", sid)
                for lid, ls in todo.items():
                    try:
                        ls.status = LanderStatus.DOWNLOADING
                        ls.error = None
                        self._save(s)
                        log.info("prepare[%s]: скачиваю ленд %s…", sid, lid)

                        zip_path = kt.download_offer(lid, sess_dir)
                        ls.zip_path = str(zip_path)
                        ls.zip_name = zip_path.name
                        ls.size = zip_path.stat().st_size
                        # Название оффера-донора (best-effort, не критично).
                        try:
                            ls.offer_name = kt.get_offer_name(lid)
                        except Exception:  # noqa: BLE001
                            pass

                        ls.status = LanderStatus.SCANNING
                        self._save(s)

                        ls.scan = runners.run_scan_only(str(zip_path))
                        # VSL: обычный скан слеп к PHP-конфигу — данные из $config.
                        if s.is_vsl:
                            from services.vsl import overlay_scan
                            ls.scan = overlay_scan(ls.scan, zip_path)
                        ls.status = LanderStatus.READY
                        self._save(s)
                        log.info("Ленд %s готов (сессия %s)", lid, sid)
                    except Exception as e:  # noqa: BLE001
                        had_error = True
                        ls.status = LanderStatus.ERROR
                        ls.error = str(e)
                        self._save(s)
                        log.exception("Сбой подготовки ленда %s", lid)
        except Exception as e:  # noqa: BLE001
            had_error = True
            log.exception("Сбой Keitaro-сессии при подготовке %s", sid)
            for ls in s.landers.values():
                if ls.status in (LanderStatus.QUEUED, LanderStatus.DOWNLOADING):
                    ls.status = LanderStatus.ERROR
                    ls.error = ls.error or f"Keitaro: {e}"

        s.status = SessionStatus.ERROR if had_error else SessionStatus.READY
        self._save(s)

    def prepare_async(self, sid: str) -> None:
        threading.Thread(
            target=self.prepare, args=(sid,), daemon=True,
            name=f"session-prepare-{sid}",
        ).start()

    # ── добавление лендов в существующую сессию ──────────────────
    def add_landers(self, sid: str, lander_ids: list[str]) -> AdaptationSession:
        """Добавляет ленды по ID (будут скачаны из Keitaro). Запускает prepare."""
        s = self.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")
        added = False
        for lid in lander_ids:
            lid = str(lid).strip()
            if lid and lid not in s.landers:
                s.landers[lid] = LanderState(lander_id=lid)
                added = True
        if added:
            s.status = SessionStatus.PREPARING
            self._save(s)
            self.prepare_async(sid)
        return s

    def add_uploaded_lander(self, sid: str, data: bytes, filename: str,
                            lander_id: Optional[str] = None,
                            task_uid: Optional[str] = None,
                            task_title: Optional[str] = None) -> LanderState:
        """Создаёт ленд из загруженного ZIP-архива (без Keitaro) и сканирует его.

        task_uid — привязка к задаче (для изоляции медиа-замен). Если не задан,
        а в сессии ровно одна задача — берём её.
        """
        import zipfile
        s = self.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")

        if not task_uid and len(s.tasks) == 1:
            task_uid = s.tasks[0].get("uid")
            task_title = task_title or s.tasks[0].get("title")

        # id ленда: явный, иначе из имени файла, иначе по порядку.
        lid = (lander_id or "").strip() or Path(filename).stem
        lid = re.sub(r"[^\w.-]", "_", lid) or f"lander{len(s.landers) + 1}"
        base = lid
        i = 2
        while lid in s.landers:
            lid = f"{base}_{i}"
            i += 1

        sess_dir = self.dir / sid
        sess_dir.mkdir(parents=True, exist_ok=True)
        zip_path = sess_dir / f"{lid}.zip"
        zip_path.write_bytes(data)

        if not zipfile.is_zipfile(zip_path):
            zip_path.unlink(missing_ok=True)
            raise ValueError("Файл не является корректным ZIP-архивом")

        ls = LanderState(
            lander_id=lid,
            task_uid=task_uid,
            task_title=task_title,
            zip_path=str(zip_path),
            zip_name=zip_path.name,
            size=zip_path.stat().st_size,
            offer_name=f"(загружен) {filename}",
        )
        s.landers[lid] = ls
        self._save(s)

        try:
            ls.status = LanderStatus.SCANNING
            self._save(s)
            ls.scan = runners.run_scan_only(str(zip_path))
            if s.is_vsl:
                from services.vsl import overlay_scan
                ls.scan = overlay_scan(ls.scan, zip_path)
            ls.status = LanderStatus.READY
        except Exception as e:  # noqa: BLE001
            ls.status = LanderStatus.ERROR
            ls.error = f"scan: {e}"
            log.exception("Сбой scan загруженного ленда %s", lid)

        if all(l.status in (LanderStatus.READY, LanderStatus.ADAPTED)
               for l in s.landers.values()):
            s.status = SessionStatus.READY
        self._save(s)
        return ls

    def delete_lander(self, sid: str, lid: str) -> AdaptationSession:
        """Удаляет ленд из сессии и подчищает его файлы.

        Стирает скачанный/загруженный архив, выходной адаптированный zip и
        (если папкой замен не пользуется другой ленд той же задачи) папку
        изолированных медиа-замен.
        """
        import shutil
        s, ls = self._get_lander(sid, lid)

        # выходной архив в storage/outputs
        if ls.output_name:
            from utils.runners import STORAGE
            (STORAGE / "outputs" / ls.output_name).unlink(missing_ok=True)

        # скачанный/загруженный архив ленда
        if ls.zip_path:
            Path(ls.zip_path).unlink(missing_ok=True)

        # папка медиа-замен — только если её не делит другой ленд той же задачи
        bucket = self._replacement_bucket(ls)
        shared = any(
            other_id != lid and self._replacement_bucket(other) == bucket
            for other_id, other in s.landers.items()
        )
        if not shared:
            shutil.rmtree(self.dir / sid / "replacements" / bucket,
                          ignore_errors=True)

        # история версий ленда
        shutil.rmtree(self._history_dir(sid, lid), ignore_errors=True)

        s.landers.pop(lid, None)

        # пересчёт статуса сессии по оставшимся лендам
        if s.landers and all(
                l.status in (LanderStatus.READY, LanderStatus.ADAPTED)
                for l in s.landers.values()):
            s.status = SessionStatus.READY
        self._save(s)
        log.info("Ленд %s удалён из сессии %s", lid, sid)
        return s

    def reinstall_lander(self, sid: str, lid: str) -> AdaptationSession:
        """Переустановка ленда: стирает текущее состояние (скачанный архив,
        адаптированный output, историю версий, параметры) и заново скачивает
        ПЕРВОНАЧАЛЬНЫЙ ленд из Keitaro. Медиа-замены задачи не трогаются —
        они общие для задачи и пригодятся при повторной адаптации."""
        import shutil
        s, ls = self._get_lander(sid, lid)
        if not re.fullmatch(r"\d{4,5}", lid):
            raise ValueError(
                "Переустановка доступна только лендам, скачанным из Keitaro (числовой id)")

        if ls.output_name:
            from utils.runners import STORAGE
            (STORAGE / "outputs" / ls.output_name).unlink(missing_ok=True)
        if ls.zip_path:
            Path(ls.zip_path).unlink(missing_ok=True)
        shutil.rmtree(self._history_dir(sid, lid), ignore_errors=True)

        # свежее состояние с сохранением привязки к задаче; статус заливки
        # (оффер/кампания/AdRobot) — исторический факт, тоже сохраняем
        old_ap = ls.adapt_params or {}
        publish = {k: old_ap[k] for k in PUBLISH_KEYS if k in old_ap}
        s.landers[lid] = LanderState(
            lander_id=lid, task_uid=ls.task_uid, task_title=ls.task_title,
            offer_override=ls.offer_override,
            adapt_params=publish or None)
        s.status = SessionStatus.PREPARING
        self._save(s)
        self.prepare_async(sid)
        log.info("Ленд %s переустанавливается (сессия %s): скачиваю первоначальный", lid, sid)
        return s

    def rename_lander(self, sid: str, lid: str, name: str) -> LanderState:
        """Пользовательское имя вкладки ленда (пусто = вернуть id)."""
        s, ls = self._get_lander(sid, lid)
        ls.display_name = (name or "").strip() or None
        self._save(s)
        return ls

    def duplicate_lander(self, sid: str, lid: str) -> LanderState:
        """Дубль ленда: копия исходного архива, output-архива, параметров,
        медиа-замен (общие по задаче — уже общие) и журнала пост-правок.
        Дубль встаёт в списке сразу ПОСЛЕ оригинала."""
        import copy
        import shutil
        s, ls = self._get_lander(sid, lid)

        base = f"{lid}-2"
        new_lid, i = base, 2
        while new_lid in s.landers:
            i += 1
            new_lid = f"{lid}-{i}"

        sess_dir = self.dir / sid
        sess_dir.mkdir(parents=True, exist_ok=True)

        # исходный архив
        new_zip_path = None
        if ls.zip_path and Path(ls.zip_path).exists():
            new_zip_path = sess_dir / f"{new_lid}.zip"
            shutil.copy2(ls.zip_path, new_zip_path)

        # адаптированный output
        new_output_name = new_output_url = None
        if ls.output_name:
            from utils.files import output_relative_url
            from utils.runners import STORAGE
            src_out = STORAGE / "outputs" / ls.output_name
            if src_out.exists():
                new_output_name = f"{new_lid}__{ls.output_name}"
                dst_out = STORAGE / "outputs" / new_output_name
                shutil.copy2(src_out, dst_out)
                new_output_url = output_relative_url(dst_out)

        dup = LanderState(
            lander_id=new_lid,
            status=ls.status,
            display_name=(ls.display_name or lid) + " (копия)",
            task_uid=ls.task_uid,
            task_title=ls.task_title,
            offer_override=ls.offer_override,
            zip_path=str(new_zip_path) if new_zip_path else None,
            zip_name=new_zip_path.name if new_zip_path else None,
            size=ls.size,
            offer_name=ls.offer_name,
            scan=copy.deepcopy(ls.scan),
            output_name=new_output_name,
            output_url=new_output_url,
            # дубль НЕ заливался — статус заливки оригинала не наследуем,
            # иначе копия выглядела бы «уже залитой»
            adapt_params={k: v for k, v in copy.deepcopy(ls.adapt_params).items()
                          if k not in PUBLISH_KEYS} if ls.adapt_params else None,
            adapt_log=copy.deepcopy(ls.adapt_log),
            error=ls.error,
        )

        # журнал пост-правок — чтобы правки дубля пережили его переадаптацию
        jp = self._journal_path(sid, lid)
        if jp.exists():
            new_jp = self._journal_path(sid, new_lid)
            new_jp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(jp, new_jp)

        # вставка сразу после оригинала (dict хранит порядок)
        new_landers: dict[str, LanderState] = {}
        for k, v in s.landers.items():
            new_landers[k] = v
            if k == lid:
                new_landers[new_lid] = dup
        s.landers = new_landers
        self._save(s)

        if new_output_name:
            self._snapshot_output(sid, new_lid, "Дубль ленда")
        log.info("Ленд %s продублирован как %s (сессия %s)", lid, new_lid, sid)
        return dup

    def reorder_landers(self, sid: str, order: list[str]) -> AdaptationSession:
        """Переставляет ленды в сессии согласно списку id `order`.

        id из `order` идут первыми в указанном порядке; ленды, не попавшие
        в список (на случай рассинхрона), сохраняются в конце в прежнем порядке.
        """
        s = self.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")
        seen: set[str] = set()
        new: dict[str, LanderState] = {}
        for lid in order:
            lid = str(lid)
            if lid in s.landers and lid not in seen:
                new[lid] = s.landers[lid]
                seen.add(lid)
        for lid, ls in s.landers.items():  # хвост: не указанные в order
            if lid not in seen:
                new[lid] = ls
        s.landers = new
        self._save(s)
        return s

    def set_lander_group(self, sid: str, lid: str,
                         offer: Optional[str]) -> dict:
        """Переопределяет «группу»/оффер ленда (или сбрасывает при пустом).

        Возвращает свежее предзаполнение адаптации под новый оффер, чтобы UI
        мог сразу подставить продукт/гео/язык/цену.
        """
        s, ls = self._get_lander(sid, lid)
        ov = (offer or "").strip()
        ls.offer_override = ov or None
        self._save(s)
        log.info("Группа ленда %s/%s = %r", sid, lid, ls.offer_override)
        return self.suggest_adapt_params(sid, lid)

    # ── адаптация ────────────────────────────────────────────────
    def _get_lander(self, sid: str, lid: str) -> tuple[AdaptationSession, LanderState]:
        s = self.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")
        ls = s.landers.get(lid)
        if ls is None:
            raise KeyError(f"Ленд {lid} не найден в сессии {sid}")
        return s, ls

    def suggest_adapt_params(self, sid: str, lid: str) -> dict:
        """Best-effort предзаполнение параметров адаптации из scan + оффера.

        Это ЧЕРНОВИК для UI/агента — значения нужно проверять и дополнять
        (особенно старую цену price_old и карту картинок image_map).
        """
        s, ls = self._get_lander(sid, lid)
        geos = runners.load_geos()
        # VSL: скан пересчитывается из ТЕКУЩЕГО config.php (рабочая копия) —
        # обычный сканер PHP-конфиг не видит, а конфиг мог быть отредактирован.
        # Заодно чинит ленды, отсканированные до появления VSL-скана.
        if s.is_vsl:
            from services.vsl import refresh_scan
            try:
                refresh_scan(sid, lid)
            except Exception:  # noqa: BLE001
                log.exception("VSL-скан %s/%s не пересчитался", sid, lid)
        # Оффер/поля берём из ЗАДАЧИ конкретного ленда (в объединённой сессии
        # у лендов могут быть разные задачи-источники с разной ценой).
        lander_offer = s.lander_offer(ls)
        lander_fields = s.task_fields(ls.task_uid)
        target = parse_target_offer(lander_offer, geos)
        scan = ls.scan or {}

        # ГЕО: из целевого оффера, иначе из задачи.
        geo_id = target.get("geo_id", "")

        # Цена-цель: из поля задачи 'Lander price'.
        price_field = (lander_fields.get("Lander price", "") or "").strip()
        new_num, new_cur = split_price(price_field)
        if not new_cur and geo_id in geos:
            new_cur = geos[geo_id].get("currency", "")

        # Старая цена = 2× новой (правило техотдела).
        old_num = double_num(new_num)
        old_cur = new_cur

        # Исходная цена донора — из scan (для поиска в тексте).
        src_new_num, src_new_cur = split_price(scan.get("price_new_str", ""))
        src_old_num, src_old_cur = split_price(scan.get("price_old_str", ""))

        detected = scan.get("detected_country", {}) or {}
        prod_images = scan.get("prod_images", []) or []

        # Продукт-донор: из названия оффера Keitaro; для загруженных архивов
        # (offer_name = "(загружен) …") и при пустом результате — из scan.
        donor_product = parse_donor_product(ls.offer_name or "")
        if not donor_product or (ls.offer_name or "").startswith("(загружен)"):
            cands = scan.get("product_candidates", []) or []
            donor_product = scan.get("product") or (cands[0]["word"] if cands else "")
        # VSL: продукт-донор = pageTitle конфига (название оффера-шаблона нерелевантно).
        if s.is_vsl and scan.get("product"):
            donor_product = scan["product"]

        # exclude_word: приоритет — вертикаль из группы (надёжно), иначе из scan.
        # ВАЖНО: хвостовой пробел значим (напр. 'hy ') — не обрезаем значение.
        exclude_word = (target.get("exclude_word") or "") \
            or (detected.get("exclude_word", "") or "")

        return {
            "group": lander_offer,
            "geo_id": geo_id,
            "product_old": donor_product,
            # Для адаптации — ядро бренда (без Resell/Low), иначе полное имя.
            "product_new": target.get("product_search") or target.get("product", ""),
            "price_new": f"{new_num} {new_cur}".strip(),
            "price_old": f"{old_num} {old_cur}".strip(),
            "price_new_num": new_num,
            "price_new_cur": new_cur,
            "price_old_num": old_num,
            "price_old_cur": old_cur,
            "src_price_new_num": src_new_num,
            "src_price_new_cur": src_new_cur,
            "src_price_old_num": src_old_num,
            "src_price_old_cur": src_old_cur,
            "exclude_word": exclude_word,
            "image_map": {},
            "custom_replacements": "",
            "_hints": {
                "donor_offer_name": ls.offer_name,
                "target_parsed": target,
                "scanned_product": scan.get("product"),
                "product_candidates": scan.get("product_candidates", []),
                "prod_images": prod_images,
                "needs_review": ["price_old", "product_new", "image_map"],
            },
        }

    # ── медиа ленда и изолированные по задаче замены ─────────────
    def list_lander_media(self, sid: str, lid: str,
                          used_only: bool = True) -> list[dict]:
        """Медиа-ресурсы (фото/гиф/видео) исходного архива ленда.

        Включает ВИДЕО (mp4/webm). Помечает is_product по scan.prod_images и
        `used` — реально ли медиа упоминается в HTML/CSS/JS ленда (по basename).
        used_only=True (по умолчанию) — вернуть только используемые на ленде
        (в архивах часто лежат лишние/неиспользуемые картинки).
        """
        import zipfile
        s, ls = self._get_lander(sid, lid)
        if not ls.zip_path or not Path(ls.zip_path).exists():
            return []
        prod = set((ls.scan or {}).get("prod_images", []) or [])

        # Собираем текст всех HTML/CSS/JS, чтобы понять, какие медиа упоминаются.
        used_names: set[str] = set()
        text_blob = ""
        with zipfile.ZipFile(ls.zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if Path(info.filename).suffix.lower() in _USAGE_TEXT_EXT:
                    try:
                        text_blob += zf.read(info.filename).decode("utf-8", "replace") + "\n"
                    except Exception:  # noqa: BLE001
                        continue
            media_infos = [i for i in zf.infolist()
                           if not i.is_dir()
                           and Path(i.filename).suffix.lower() in _MEDIA_EXT]

        out: list[dict] = []
        for info in media_infos:
            name = Path(info.filename).name
            ext = Path(name).suffix.lower()
            kind = "video" if ext in _VIDEO_EXT else "image"
            is_product = name in prod or info.filename in prod
            # Использовано, если basename встречается в HTML/CSS/JS (или это фото
            # продукта по scan). Подстрока basename — терпимо к относит. путям.
            used = is_product or (name and name in text_blob)
            if used_only and not used:
                continue
            out.append({
                "path": info.filename,
                "name": name,
                "size": info.file_size,
                "kind": kind,
                "is_product": is_product,
                "used": used,
            })
        # Сначала фото продукта, потом по имени.
        out.sort(key=lambda m: (not m["is_product"], m["name"].lower()))
        return out

    def _replacement_bucket(self, ls: LanderState) -> str:
        """Имя папки изоляции замен: по task_uid ленда, иначе общий бакет сессии."""
        return ls.task_uid or "_session"

    def replacement_dir(self, sid: str, lid: str, create: bool = False) -> Path:
        """Папка изолированных по задаче медиа-замен для ленда."""
        s, ls = self._get_lander(sid, lid)
        d = self.dir / sid / "replacements" / self._replacement_bucket(ls)
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def save_replacement(self, sid: str, lid: str, data: bytes,
                         filename: str) -> str:
        """Сохраняет медиа-замену в изолированную по задаче папку. → имя файла."""
        d = self.replacement_dir(sid, lid, create=True)
        name = re.sub(r"[^\w.-]", "_", (filename or "media").strip()) or "media"
        target = d / name
        if target.exists():
            # Тот же контент — не плодим дубли; иначе уникализируем.
            if target.read_bytes() == data:
                return target.name
            stem, suf = target.stem, target.suffix
            i = 2
            while (d / f"{stem}_{i}{suf}").exists():
                i += 1
            target = d / f"{stem}_{i}{suf}"
        target.write_bytes(data)
        return target.name

    def list_replacements(self, sid: str, lid: str) -> list[dict]:
        """Список изолированных по задаче замен (имя + размер)."""
        d = self.replacement_dir(sid, lid)
        if not d.exists():
            return []
        return sorted(
            ({"name": p.name, "size": p.stat().st_size}
             for p in d.iterdir() if p.is_file()),
            key=lambda x: x["name"].lower(),
        )

    def replacement_file(self, sid: str, lid: str, name: str) -> Optional[Path]:
        """Путь к конкретной замене (для отдачи на превью)."""
        safe = re.sub(r"[^\w.-]", "_", name or "")
        if not safe:
            return None
        p = self.replacement_dir(sid, lid) / safe
        return p if p.exists() else None

    def delete_replacement(self, sid: str, lid: str, name: str) -> bool:
        """Удаляет файл замены из загруженных медиа задачи. Заодно вычищает его
        из image_map ленда (если он там был выбран). → True, если удалён."""
        safe = re.sub(r"[^\w.-]", "_", name or "")
        if not safe:
            return False
        p = self.replacement_dir(sid, lid) / safe
        if not p.exists():
            return False
        p.unlink()
        # Если эта замена была выбрана в image_map — убрать (иначе adapt не найдёт).
        s, ls = self._get_lander(sid, lid)
        im = (ls.adapt_params or {}).get("image_map") or {}
        changed = {k: v for k, v in im.items() if v != safe}
        if len(changed) != len(im):
            ls.adapt_params = {**(ls.adapt_params or {}), "image_map": changed}
            self._save(s)
        log.info("Замена %s удалена (ленд %s/%s)", safe, sid, lid)
        return True

    def persist_media_override(self, sid: str, lid: str, path: str) -> dict:
        """Сохраняет ТЕКУЩУЮ картинку из output-архива (напр. после нейро-правки)
        в папку изолированных замен и регистрирует её в image_map ленда.

        Зачем: повторная адаптация (adapt_lander) пересобирает output ИЗ ИСХОДНОГО
        архива по image_map — без этого нейро-правка терялась. Теперь правленая
        картинка лежит в replacements и подставляется при каждой адаптации.
        Возвращает {image_map_key, replacement} — фронт дописывает их в params.
        """
        data, name = self.read_output_media(sid, lid, path)
        s, ls = self._get_lander(sid, lid)
        # Повторная правка уже правленого файла: текущее имя в output —
        # neuro_<исходное>. Префикс НЕ наслаиваем (иначе neuro_neuro_… и битые
        # ссылки), ключ image_map — всегда имя из ИСХОДНОГО архива.
        base = name
        while base.startswith("neuro_"):
            base = base[len("neuro_"):]
        repl_name = re.sub(r"[^\w.-]", "_", f"neuro_{base}") or "neuro_media"
        d = self.replacement_dir(sid, lid, create=True)
        (d / repl_name).write_bytes(data)  # детерминированное имя — перезапись при повторной правке
        im = dict((ls.adapt_params or {}).get("image_map") or {})
        im.pop(repl_name, None)  # каскадная запись от старой версии (neuro_x → neuro_neuro_x)
        im[base] = repl_name
        ls.adapt_params = {**(ls.adapt_params or {}), "image_map": im}
        self._save(s)
        log.info("Нейро-правка %s закреплена как замена %s (ленд %s/%s)",
                 name, repl_name, sid, lid)
        return {"image_map_key": base, "replacement": repl_name}

    def read_output_media(self, sid: str, lid: str, path: str) -> tuple[bytes, str]:
        """Байты картинки из output-архива + её имя. Для нейро-редактора."""
        import zipfile
        norm = (path or "").replace("\\", "/").strip()
        if not norm or norm.startswith("/") or ".." in norm.split("/"):
            raise ValueError("Некорректный путь")
        p = self._output_zip(sid, lid)
        with zipfile.ZipFile(p, "r") as zf:
            names = zf.namelist()
            member = norm if norm in names else next(
                (n for n in names if n.replace("\\", "/") == norm), None)
            if member is None:
                raise KeyError(f"Картинка не найдена: {path}")
            return zf.read(member), Path(member).name

    def replace_output_media(self, sid: str, lid: str, path: str,
                             new_bytes: bytes) -> dict:
        """Заменяет картинку в output-архиве новым содержимым, подогнав РАЗМЕР
        и формат под оригинал (чтобы вёрстка не поехала). Атомарная пересборка."""
        import io
        import os
        import tempfile
        import zipfile
        from PIL import Image

        norm = (path or "").replace("\\", "/").strip()
        target = self._output_zip(sid, lid)
        with zipfile.ZipFile(target, "r") as zf:
            names = zf.namelist()
            member = norm if norm in names else next(
                (n for n in names if n.replace("\\", "/") == norm), None)
            if member is None:
                raise KeyError(f"Картинка не найдена: {path}")
            orig = zf.read(member)

        ext = Path(member).suffix.lower().lstrip(".") or "png"
        fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG",
               "webp": "WEBP", "gif": "GIF"}.get(ext, "PNG")
        try:
            ow, oh = Image.open(io.BytesIO(orig)).size
            new_img = Image.open(io.BytesIO(new_bytes))
            if new_img.size != (ow, oh):
                new_img = new_img.resize((ow, oh), Image.LANCZOS)
            if fmt == "JPEG" and new_img.mode in ("RGBA", "P"):
                new_img = new_img.convert("RGB")
            buf = io.BytesIO()
            new_img.save(buf, format=fmt)
            out_bytes = buf.getvalue()
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Не обработать изображение: {e}")

        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(target.parent))
        os.close(fd)
        try:
            with zipfile.ZipFile(target, "r") as zin, \
                 zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = out_bytes if item.filename == member else zin.read(item.filename)
                    zout.writestr(item, data)
            os.replace(tmp, target)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._snapshot_output(sid, lid, "Нейро-правка")
        return {"path": member, "size": len(out_bytes), "dimensions": f"{ow}x{oh}"}

    # ── история версий output-архива (откат «на шаг назад») ──────
    _HISTORY_MAX = 20

    def _history_dir(self, sid: str, lid: str, create: bool = False) -> Path:
        d = self.dir / sid / "history" / re.sub(r"[^\w.-]", "_", lid)
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def _snapshot_output(self, sid: str, lid: str, label: str) -> Optional[dict]:
        """Снимает копию ТЕКУЩЕГО output-архива ленда в историю (для отката).

        Вызывается после каждой мутации архива (адаптация / нейро-правка / правка
        кода / перевод), чтобы потом можно было вернуться на любой шаг. No-op, если
        ленд ещё не адаптирован или архив не найден."""
        import shutil
        try:
            s, ls = self._get_lander(sid, lid)
        except KeyError:
            return None
        if not ls.output_name:
            return None
        from utils.runners import STORAGE
        src = STORAGE / "outputs" / ls.output_name
        if not src.exists():
            return None
        d = self._history_dir(sid, lid, create=True)
        vid = uuid.uuid4().hex[:8]
        dst = d / f"{vid}.zip"
        try:
            shutil.copy2(src, dst)
        except Exception:  # noqa: BLE001
            log.exception("Не снять снимок версии ленда %s/%s", sid, lid)
            return None
        entry = {
            "id": vid,
            "label": label,
            "created_at": time.time(),
            "output_name": ls.output_name,
            "size": dst.stat().st_size,
        }
        ls.history.append(entry)
        ls.current_version = vid  # только что снятый снимок = текущее состояние
        # Ограничиваем историю: самые старые снимки (и их файлы) выкидываем.
        while len(ls.history) > self._HISTORY_MAX:
            old = ls.history.pop(0)
            (d / f"{old.get('id')}.zip").unlink(missing_ok=True)
        self._save(s)
        log.info("Снимок версии ленда %s/%s: «%s» (всего %d)",
                 sid, lid, label, len(ls.history))
        return entry

    def list_history(self, sid: str, lid: str) -> dict:
        """История версий ленда (без путей к файлам). Старые → новые (шаг 1..N).
        Возвращает {versions, current} — current = id текущей версии (для дропдауна).
        """
        s, ls = self._get_lander(sid, lid)
        d = self._history_dir(sid, lid)
        versions = [
            {
                "id": h["id"],
                "step": i + 1,
                "label": h.get("label", ""),
                "created_at": h.get("created_at"),
                "size": h.get("size"),
                "available": (d / f"{h['id']}.zip").exists(),
            }
            for i, h in enumerate(ls.history)
        ]
        return {"versions": versions, "current": ls.current_version}

    def restore_version(self, sid: str, lid: str, version_id: str) -> dict:
        """Откат: восстанавливает output-архив из снимка version_id.

        Текущее состояние перед откатом тоже снимается («Перед откатом») — откат
        обратим (можно вернуться обратно). Возвращает обновлённое состояние ленда.
        """
        import shutil
        from utils.files import output_relative_url
        from utils.runners import STORAGE
        s, ls = self._get_lander(sid, lid)
        entry = next((h for h in ls.history if h.get("id") == version_id), None)
        if entry is None:
            raise KeyError(f"Версия {version_id} не найдена")
        snap = self._history_dir(sid, lid) / f"{version_id}.zip"
        if not snap.exists():
            raise ValueError("Файл версии не найден (возможно, очищен при перезапуске)")

        # Перед откатом снимаем текущее состояние, чтобы откат можно было отменить.
        self._snapshot_output(sid, lid, "Перед откатом")

        out_name = ls.output_name or entry.get("output_name") or f"{lid}_restored.zip"
        dst = STORAGE / "outputs" / out_name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap, dst)
        ls.output_name = out_name
        ls.output_url = output_relative_url(dst)
        ls.status = LanderStatus.ADAPTED
        ls.error = None
        ls.current_version = version_id  # после отката текущая = выбранная версия
        self._save(s)
        log.info("Откат ленда %s/%s к версии %s («%s»)",
                 sid, lid, version_id, entry.get("label"))
        return {
            "success": True,
            "status": ls.status,
            "output_name": ls.output_name,
            "output_url": ls.output_url,
            "restored": {"id": version_id, "label": entry.get("label")},
        }

    def adapt_lander(self, sid: str, lid: str, params: dict) -> dict:
        """Выполняет run_adapt над скачанным лендом. Результат → storage/outputs.

        VSL-сессия: адаптируется РАБОЧАЯ КОПИЯ (с правками config.php), а не
        исходник — иначе адаптация стирала бы конфиг. Повторная адаптация
        применяется поверх предыдущей."""
        s, ls = self._get_lander(sid, lid)
        if not ls.zip_path or not Path(ls.zip_path).exists():
            raise ValueError(f"Ленд {lid} ещё не скачан (нет zip)")
        src_zip = ls.zip_path
        if getattr(s, "is_vsl", False):
            from services.vsl import ensure_output
            src_zip = str(ensure_output(sid, lid))
        if not params.get("geo_id"):
            raise ValueError("Не задан geo_id")
        if not params.get("product_new"):
            raise ValueError("Не задан product_new")

        prev_status = ls.status
        ls.status = LanderStatus.ADAPTING
        ls.error = None
        self._save(s)

        from utils.files import output_relative_url

        # Изолированные по задаче замены ищутся перед глобальной storage/assets/.
        repl_dir = self.replacement_dir(sid, lid)
        extra_dirs = [str(repl_dir)] if repl_dir.exists() else []

        try:
            # VSL: clean удалил бы config.php (он в правилах «чужих файлов»),
            # а inject-обвязка у эталонного шаблона уже своя — отключаем оба.
            is_vsl = getattr(s, "is_vsl", False)
            out_path, capture = runners.run_adapt(src_zip, params,
                                                  extra_asset_dirs=extra_dirs,
                                                  do_clean=not is_vsl,
                                                  do_inject=not is_vsl)
        except Exception as e:  # noqa: BLE001
            ls.status = LanderStatus.ERROR
            ls.error = str(e)
            self._save(s)
            raise

        # Статус заливки (оффер/кампания/AdRobot) не сбрасываем — параметры
        # формы не содержат этих ключей, а без них не понять, заливался ли ленд.
        old_ap = ls.adapt_params or {}
        ls.adapt_params = {
            **{k: old_ap[k] for k in PUBLISH_KEYS if k in old_ap}, **params}
        ls.adapt_log = capture.to_dicts()
        if out_path:
            ls.output_name = Path(out_path).name
            ls.output_url = output_relative_url(out_path)
            ls.status = LanderStatus.ADAPTED
        else:
            ls.status = LanderStatus.ERROR
            ls.error = "run_adapt не вернул результат (см. лог)"
        self._save(s)

        # VSL: продукт/цены/гео живут в config.php — текстовая адаптация их не
        # видит, применяем значения прямо в конфиг + пересчитываем VSL-скан.
        if out_path and is_vsl:
            from services.vsl import adapt_config, refresh_scan
            try:
                notes = adapt_config(sid, lid, params)
                ls.adapt_log = ls.adapt_log + [
                    {"text": n, "level": "success"} for n in notes]
            except Exception as e:  # noqa: BLE001
                log.exception("VSL: не удалось адаптировать config.php %s/%s", sid, lid)
                ls.adapt_log = ls.adapt_log + [
                    {"text": f"VSL config: ошибка адаптации конфига: {e}",
                     "level": "error"}]
            try:
                refresh_scan(sid, lid)
            except Exception:  # noqa: BLE001
                pass
            self._save(s)

        # Пост-правки (редактор/чат/перевод) — переприменяем к свежему архиву:
        # адаптация пересобирает output из ИСХОДНИКА и без этого стирала бы их.
        # VSL адаптируется поверх рабочей копии — правки уже внутри, replay
        # был бы вторым применением.
        if out_path and not is_vsl:
            try:
                notes = self.reapply_post_edits(sid, lid)
                if notes:
                    ls.adapt_log = ls.adapt_log + notes
                    self._save(s)
            except Exception as e:  # noqa: BLE001
                log.exception("Не переприменились пост-правки %s/%s", sid, lid)
                ls.adapt_log = ls.adapt_log + [
                    {"text": f"Пост-правки не переприменились: {e}",
                     "level": "error"}]
                self._save(s)

        # Снимок версии после адаптации — для отката (см. _snapshot_output).
        if out_path:
            self._snapshot_output(sid, lid, "Адаптация")

        return {
            "success": bool(out_path),
            "status": ls.status,
            "output_name": ls.output_name,
            "output_url": ls.output_url,
            "log": ls.adapt_log,
            "error": ls.error,
        }

    # ── правки файлов адаптированного ленда (для чата-агента) ─────
    def _output_zip(self, sid: str, lid: str) -> Path:
        s, ls = self._get_lander(sid, lid)
        if not ls.output_name:
            raise ValueError("Ленд ещё не адаптирован — нет выходного архива")
        from utils.runners import STORAGE
        p = STORAGE / "outputs" / ls.output_name
        if not p.exists():
            raise ValueError("Выходной архив не найден")
        return p

    def list_output_files(self, sid: str, lid: str) -> list[str]:
        """Текстовые файлы адаптированного ленда (для правок агентом)."""
        import zipfile
        p = self._output_zip(sid, lid)
        with zipfile.ZipFile(p, "r") as zf:
            return sorted(
                n for n in zf.namelist()
                if Path(n).suffix.lower() in _EDIT_TEXT_EXT
            )

    def read_output_file(self, sid: str, lid: str, path: str) -> str:
        import zipfile
        norm = (path or "").replace("\\", "/").strip()
        if not norm or norm.startswith("/") or ".." in norm.split("/"):
            raise ValueError("Некорректный путь")
        p = self._output_zip(sid, lid)
        with zipfile.ZipFile(p, "r") as zf:
            names = zf.namelist()
            member = norm if norm in names else next(
                (n for n in names if n.replace("\\", "/") == norm), None)
            if member is None:
                raise KeyError(f"Файл не найден: {path}")
            return zf.read(member).decode("utf-8", errors="replace")

    def replace_texts(self, sid: str, lid: str,
                      pairs: list[tuple[str, str]]) -> list[dict]:
        """Массовая замена find→replace по ВСЕМ текстовым файлам output-архива.

        Одна атомарная пересборка zip и один снимок истории на весь батч
        (edit_output_file на каждую пару делал бы N снимков). Для каждой пары
        отчёт: сколько вхождений заменено и в каких файлах — «не найдено» тоже
        результат (агент сообщит пользователю, что именно не совпало).
        """
        import os
        import tempfile
        import zipfile
        target = self._output_zip(sid, lid)

        with zipfile.ZipFile(target, "r") as zf:
            texts: dict[str, str] = {}
            for n in zf.namelist():
                if Path(n).suffix.lower() in _EDIT_TEXT_EXT:
                    texts[n] = zf.read(n).decode("utf-8", errors="replace")

        report: list[dict] = []
        changed: set[str] = set()
        for find, replace in pairs:
            if not find:
                report.append({"find": "", "replaced": 0, "files": [],
                               "error": "пустая строка поиска"})
                continue
            total = 0
            files: list[str] = []
            for name, text in texts.items():
                k = text.count(find)
                if k:
                    texts[name] = text.replace(find, replace)
                    total += k
                    files.append(name)
                    changed.add(name)
            report.append({"find": find[:80], "replaced": total, "files": files})

        if changed:
            fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(target.parent))
            os.close(fd)
            try:
                with zipfile.ZipFile(target, "r") as zin, \
                     zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                    for item in zin.infolist():
                        if item.filename in changed:
                            zout.writestr(item.filename,
                                          texts[item.filename].encode("utf-8"))
                        else:
                            zout.writestr(item, zin.read(item.filename))
                os.replace(tmp, target)
            except Exception:
                Path(tmp).unlink(missing_ok=True)
                raise
            self._snapshot_output(sid, lid, f"Замена текстов ({len(pairs)} пар)")
            self.journal_append(sid, lid, {
                "type": "replace_texts",
                "pairs": [{"find": f, "replace": r} for f, r in pairs]})
        return report

    def write_output_file(self, sid: str, lid: str, path: str, data: bytes,
                          *, label: str = "Правка файла",
                          snapshot: bool = True) -> dict:
        """Создаёт/заменяет файл path в output-архиве целиком (атомарно).

        В отличие от edit_output_file пишет весь файл (нужно VSL-конфигу и
        product.png). snapshot=False — без снимка версии (для серий правок,
        снимок снимает последняя)."""
        import os
        import tempfile
        import zipfile
        norm = (path or "").replace("\\", "/").strip()
        if not norm or norm.startswith("/") or ".." in norm.split("/"):
            raise ValueError("Некорректный путь")
        target = self._output_zip(sid, lid)

        with zipfile.ZipFile(target, "r") as zf:
            names = zf.namelist()
            member = norm if norm in names else next(
                (n for n in names if n.replace("\\", "/") == norm), None)

        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(target.parent))
        os.close(fd)
        try:
            with zipfile.ZipFile(target, "r") as zin, \
                 zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if member is not None and item.filename == member:
                        zout.writestr(item.filename, data)
                    else:
                        zout.writestr(item, zin.read(item.filename))
                if member is None:  # новый файл — добавляем в архив
                    zout.writestr(norm, data)
            os.replace(tmp, target)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        if snapshot:
            self._snapshot_output(sid, lid, label)
        return {"path": member or norm, "size": len(data)}

    def edit_output_file(self, sid: str, lid: str, path: str,
                         find: str, replace: str) -> dict:
        """Заменяет find→replace во всех вхождениях внутри файла output-архива.
        Пересобирает zip атомарно. Возвращает число замен."""
        import os
        import tempfile
        import zipfile
        if not find:
            raise ValueError("Пустая строка поиска")
        norm = (path or "").replace("\\", "/").strip()
        if not norm or norm.startswith("/") or ".." in norm.split("/"):
            raise ValueError("Некорректный путь")
        target = self._output_zip(sid, lid)

        with zipfile.ZipFile(target, "r") as zf:
            names = zf.namelist()
            member = norm if norm in names else next(
                (n for n in names if n.replace("\\", "/") == norm), None)
            if member is None:
                raise KeyError(f"Файл не найден: {path}")
            text = zf.read(member).decode("utf-8", errors="replace")

        count = text.count(find)
        if count == 0:
            return {"replaced": 0, "error": "строка поиска не найдена в файле"}
        new_text = text.replace(find, replace)

        fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(target.parent))
        os.close(fd)
        try:
            with zipfile.ZipFile(target, "r") as zin, \
                 zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename == member:
                        zout.writestr(item.filename, new_text.encode("utf-8"))
                    else:
                        zout.writestr(item, zin.read(item.filename))
            os.replace(tmp, target)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._snapshot_output(sid, lid, f"Правка кода: {Path(member).name}")
        self.journal_append(sid, lid, {
            "type": "file_edit", "path": member,
            "ops": [{"find": find, "replace": replace}]})
        return {"replaced": count, "path": member}

    # ── журнал пост-правок: правки редактора/чата/перевода переживают
    #    повторную адаптацию (re-apply после rebuild из исходника) ────
    _JOURNAL_MAX = 200

    def _journal_path(self, sid: str, lid: str) -> Path:
        return self.dir / sid / "edits" / f"{lid}.json"

    def _journal_load(self, sid: str, lid: str) -> list[dict]:
        p = self._journal_path(sid, lid)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            log.exception("Битый журнал правок %s/%s", sid, lid)
            return []

    def journal_append(self, sid: str, lid: str, op: dict) -> None:
        """Дописывает операцию в журнал пост-правок ленда."""
        try:
            journal = self._journal_load(sid, lid)
            op = {**op, "ts": time.time()}
            journal.append(op)
            while len(journal) > self._JOURNAL_MAX:
                dropped = journal.pop(0)
                mf = dropped.get("mapping_file")
                if mf:
                    Path(mf).unlink(missing_ok=True)
            p = self._journal_path(sid, lid)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(journal, ensure_ascii=False),
                         encoding="utf-8")
        except Exception:  # noqa: BLE001
            log.exception("Не записан журнал правок %s/%s", sid, lid)

    def find_lander_by_output(self, output_name: str) -> Optional[tuple[str, str]]:
        """(sid, lid) ленда по имени output-архива (для журналирования правок
        из превью-редактора, где известен только zip)."""
        if not output_name:
            return None
        for meta in self.list():
            sid = meta.get("id") or meta.get("sid")
            s = self.get(sid) if sid else None
            if s is None:
                continue
            for lid, ls in (s.landers or {}).items():
                if getattr(ls, "output_name", None) == output_name:
                    return sid, lid
        return None

    @staticmethod
    def _anchored_ops(old: str, new: str, ctx: int = 32) -> list[dict]:
        """Дифф old→new как список якорных замен {find, replace}: изменённые
        куски с контекстом вокруг, контекст расширяется до уникальности find
        в old. Близкие изменения сливаются, чтобы контексты не пересекались."""
        import difflib
        sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
        spans = [(a1, a2, b1, b2) for tag, a1, a2, b1, b2 in sm.get_opcodes()
                 if tag != "equal"]
        if not spans:
            return []
        # слить изменения с промежутком < 2*ctx (их контексты бы пересеклись)
        merged = [list(spans[0])]
        for a1, a2, b1, b2 in spans[1:]:
            if a1 - merged[-1][1] < 2 * ctx:
                merged[-1][1], merged[-1][3] = a2, b2
            else:
                merged.append([a1, a2, b1, b2])
        ops: list[dict] = []
        for a1, a2, b1, b2 in merged:
            c = ctx
            while True:
                lo, hi = max(0, a1 - c), min(len(old), a2 + c)
                find = old[lo:hi]
                if old.count(find) == 1 or (lo == 0 and hi == len(old)):
                    break
                c *= 2
            ops.append({"find": find,
                        "replace": old[lo:a1] + new[b1:b2] + old[a2:hi]})
        return ops

    def record_output_file_edit(self, sid: str, lid: str, path: str,
                                old_text: str, new_text: str) -> None:
        """Журналирует правку файла (напр. сохранение из превью-редактора)
        как якорные find→replace, чтобы повторить её после переадаптации."""
        if old_text == new_text:
            return
        ops = self._anchored_ops(old_text, new_text)
        if ops:
            self.journal_append(sid, lid, {"type": "file_edit", "path": path,
                                           "ops": ops})

    def reapply_post_edits(self, sid: str, lid: str) -> list[dict]:
        """Повторно применяет журнал пост-правок к СВЕЖЕМУ output-архиву
        (после адаптации). Возвращает заметки для adapt_log. Ненайденные
        якоря пропускаются с предупреждением (конфликт с новой адаптацией)."""
        import os
        import tempfile
        import zipfile
        journal = self._journal_load(sid, lid)
        if not journal:
            return []
        target = self._output_zip(sid, lid)
        with zipfile.ZipFile(target, "r") as zf:
            texts = {n: zf.read(n).decode("utf-8", "replace")
                     for n in zf.namelist()
                     if Path(n).suffix.lower() in _EDIT_TEXT_EXT}

        notes: list[dict] = []
        changed: set[str] = set()

        def _apply(pairs: list[tuple[str, str]], scope: Optional[str]) -> tuple[int, int]:
            hit = missed = 0
            for f, r in pairs:
                if not f:
                    continue
                names = ([scope] if scope in texts else list(texts))
                found = False
                for n in names:
                    t = texts.get(n, "")
                    if f in t:
                        texts[n] = t.replace(f, r)
                        changed.add(n)
                        found = True
                hit, missed = (hit + 1, missed) if found else (hit, missed + 1)
            return hit, missed

        for op in journal:
            kind = op.get("type")
            if kind == "file_edit":
                pairs = [(o.get("find", ""), o.get("replace", ""))
                         for o in op.get("ops", [])]
                h, m = _apply(pairs, op.get("path"))
                if m:
                    notes.append({"text": f"Повтор правки {op.get('path')}: "
                                          f"{m} из {h + m} замен не легли "
                                          "(конфликт с новой адаптацией)",
                                  "level": "warning"})
            elif kind == "replace_texts":
                pairs = [(p.get("find", ""), p.get("replace", ""))
                         for p in op.get("pairs", [])]
                h, m = _apply(pairs, None)
                if m:
                    notes.append({"text": f"Повтор замен текста: {m} из {h + m} "
                                          "пар не найдены", "level": "warning"})
            elif kind == "translation":
                mf = Path(op.get("mapping_file") or "")
                if not mf.exists():
                    continue
                try:
                    mapping = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    continue
                from services.translate import (TEXT_FILE_EXT, apply_to_text,
                                                ensure_rtl_html, is_rtl)
                cnt = 0
                # Только html/php: словарь перевода по js/css/json ломал
                # код (замена коротких блоков внутри классов/идентификаторов).
                for n in list(texts):
                    if Path(n).suffix.lower() not in TEXT_FILE_EXT:
                        continue
                    new_t, k = apply_to_text(texts[n], mapping)
                    if k:
                        texts[n] = new_t
                        changed.add(n)
                        cnt += k
                lang = op.get("lang", "")
                if is_rtl(lang):
                    for n in list(texts):
                        if Path(n).suffix.lower() in (".php", ".html", ".htm"):
                            new_t = ensure_rtl_html(texts[n], lang)
                            if new_t != texts[n]:
                                texts[n] = new_t
                                changed.add(n)
                notes.append({"text": f"Перевод ({lang}) переприменён из кэша: "
                                      f"{cnt} блоков", "level": "success"})

        if changed:
            fd, tmp = tempfile.mkstemp(suffix=".zip", dir=str(target.parent))
            os.close(fd)
            try:
                with zipfile.ZipFile(target, "r") as zin, \
                     zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                    for item in zin.infolist():
                        if item.filename in changed:
                            zout.writestr(item.filename,
                                          texts[item.filename].encode("utf-8"))
                        else:
                            zout.writestr(item, zin.read(item.filename))
                os.replace(tmp, target)
            except Exception:
                Path(tmp).unlink(missing_ok=True)
                raise
            notes.insert(0, {"text": "Пост-правки (редактор/чат/перевод) "
                                     f"переприменены: файлов {len(changed)}",
                             "level": "success"})
        return notes


# Синглтон менеджера.
_manager: Optional[SessionManager] = None


def get_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
