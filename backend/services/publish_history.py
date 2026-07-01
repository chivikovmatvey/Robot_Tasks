"""История опубликованных в Keitaro лендов.

Хранит факт публикации (id оффера + дата + мета) в storage/published.json и
отдаёт сгруппированную по дням/неделям/месяцам/всего статистику для UI:

    27.06.2026 — 6 шт
    11099 20123 23020 ...   (id по возрастанию, слева направо)

Запись добавляется автоматически при подтверждении id заливки
(keitaro_upload.rename_offer) и вручную через UI.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("session")  # переиспользуем настроенный логгер

BASE_DIR = Path(__file__).resolve().parents[1]
_MONTHS_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
              "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]


class PublishHistory:
    def __init__(self, store_file: Optional[Path] = None):
        self.file = store_file or (BASE_DIR / "storage" / "published.json")
        self._lock = threading.Lock()
        self._items: list[dict] = self._load()

    # ── хранилище ────────────────────────────────────────────────
    def _load(self) -> list[dict]:
        if self.file.exists():
            try:
                data = json.loads(self.file.read_text("utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:  # noqa: BLE001
                log.warning("Не прочитать %s — начинаю с пустой истории", self.file)
        return []

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._items, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.file)

    # ── изменение ────────────────────────────────────────────────
    def add(self, offer_id: int | str, *, date_str: str = "",
            product: str = "", geo: str = "", session_id: str = "",
            name: str = "") -> dict:
        """Добавляет публикацию. Дедуп по id (повторный id обновляет запись).
        date_str — 'YYYY-MM-DD' (по умолчанию сегодня)."""
        try:
            oid = int(str(offer_id).strip())
        except (TypeError, ValueError):
            raise ValueError(f"Некорректный id: {offer_id!r}")
        d = (date_str or "").strip() or date.today().isoformat()
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"Дата должна быть YYYY-MM-DD, получено {d!r}")
        with self._lock:
            rec = next((r for r in self._items if int(r.get("id", 0)) == oid), None)
            if rec is None:
                rec = {"id": oid}
                self._items.append(rec)
            rec["date"] = d
            if product:    rec["product"] = product
            if geo:        rec["geo"] = geo
            if session_id: rec["session_id"] = session_id
            if name:       rec["name"] = name
            self._save()
        log.info("Публикация записана: id=%s дата=%s", oid, d)
        return rec

    def remove(self, offer_id: int | str) -> bool:
        try:
            oid = int(str(offer_id).strip())
        except (TypeError, ValueError):
            return False
        with self._lock:
            before = len(self._items)
            self._items = [r for r in self._items if int(r.get("id", 0)) != oid]
            if len(self._items) != before:
                self._save()
                return True
        return False

    # ── группировка ──────────────────────────────────────────────
    @staticmethod
    def _key_and_label(d: date, period: str) -> tuple[str, str]:
        if period == "week":
            iso = d.isocalendar()           # (year, week, weekday)
            # понедельник недели для подписи диапазона
            monday = date.fromisocalendar(iso[0], iso[1], 1)
            sunday = date.fromisocalendar(iso[0], iso[1], 7)
            key = f"{iso[0]}-W{iso[1]:02d}"
            label = (f"Неделя {iso[1]}, {iso[0]} "
                     f"({monday.strftime('%d.%m')}–{sunday.strftime('%d.%m')})")
            return key, label
        if period == "month":
            return f"{d.year}-{d.month:02d}", f"{_MONTHS_RU[d.month]} {d.year}"
        if period == "all":
            return "all", "Все"
        # day (по умолчанию)
        return d.isoformat(), d.strftime("%d.%m.%Y")

    def grouped(self, period: str = "day") -> list[dict]:
        """Группы публикаций по периоду. Группы — по убыванию даты (свежие
        сверху), id ВНУТРИ группы — по возрастанию (слева направо)."""
        period = period if period in ("day", "week", "month", "all") else "day"
        buckets: dict[str, dict] = {}
        with self._lock:
            items = list(self._items)
        for r in items:
            try:
                d = datetime.strptime(r.get("date", ""), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue
            key, label = self._key_and_label(d, period)
            b = buckets.setdefault(key, {"key": key, "label": label,
                                         "sort": key, "ids": []})
            try:
                b["ids"].append(int(r.get("id")))
            except (TypeError, ValueError):
                continue
        out = []
        for b in buckets.values():
            ids = sorted(set(b["ids"]))             # по возрастанию
            out.append({"key": b["key"], "label": b["label"],
                        "count": len(ids), "ids": ids,
                        "copy": " ".join(str(i) for i in ids)})  # без запятых, в строку
        # группы: свежие сверху (для 'all' одна группа)
        out.sort(key=lambda g: g["key"], reverse=True)
        return out

    def total(self) -> int:
        with self._lock:
            return len({int(r.get("id", 0)) for r in self._items})


_history: Optional[PublishHistory] = None


def get_history() -> PublishHistory:
    global _history
    if _history is None:
        _history = PublishHistory()
    return _history
