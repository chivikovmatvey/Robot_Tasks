"""Приём задач из AdRobot + уведомления в Telegram.

Чинит недочёт старого бота: теперь видны и задачи общего пула
(Assigned to = Anyone), и личные задачи исполнителя (mch) с любым активным
статусом, а не только PENDING/Anyone.

Используется и как фоновый поллер (run_forever), и как источник данных для
API (list_cached / poll_once / get_detail).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from connectors.adrobot import (
    AdRobotClient, AuthError, Notification, TaskDetail, TaskSummary,
)
from connectors.telegram import TelegramNotifier, esc

log = logging.getLogger("intake")

BASE_DIR = Path(__file__).resolve().parents[1]

# Поля карточки в человекочитаемом виде (для Telegram).
FIELD_ORDER = [
    ("Created by", "👤 От кого"),
    ("Assigned to", "🎯 Назначено"),
    ("Status", "📌 Статус"),
    ("Category", "🗂 Категория"),
    ("Offer", "📦 Оффер"),
    ("Reference lander", "🔗 Референс"),
    ("Target audience", "👥 ЦА"),
    ("Lander price", "💲 Цена"),
    ("Promotions", "🎁 Промо"),
    ("Comments", "💬 Комментарий"),
]


def format_task(detail: TaskDetail) -> str:
    lines: list[str] = ["🆕 <b>Новая задача</b>"]
    if detail.title:
        lines.append(f"<b>{esc(detail.title)}</b>")
    lines.append("")

    used = set()
    for key, label in FIELD_ORDER:
        val = detail.fields.get(key)
        if val and val != "-":
            lines.append(f"{label}: <b>{esc(val)}</b>")
            used.add(key)

    desc = detail.fields.get("Description")
    if desc and desc != "-":
        used.add("Description")
        lines.append("")
        lines.append("📝 <b>Описание:</b>")
        lines.append(esc(desc))

    for key, val in detail.fields.items():
        if key in used or key == "Description":
            continue
        if val and val != "-":
            lines.append(f"{esc(key)}: {esc(val)}")

    return "\n".join(lines).strip()


# Человекочитаемые подписи статусов для уведомлений.
_STATUS_LABELS = {
    "ACCEPTED": "✅ Задача принята",
    "REVIEW": "👀 Задача на проверке",
    "IN_PROCESS": "🔄 Задача возвращена к работе",
    "REJECTED": "❌ Задача отклонена",
    "DONE": "🏁 Задача завершена",
}


def format_notification(n: Notification) -> str:
    """Telegram-сообщение по уведомлению (статус/комментарий)."""
    lines: list[str] = []
    if n.kind == "comment":
        lines.append("💬 <b>Новый комментарий к задаче</b>")
    else:
        head = _STATUS_LABELS.get(n.payload, f"🔔 Статус: {esc(n.payload)}")
        lines.append(f"<b>{head}</b>")
    if n.task_title:
        lines.append(esc(n.task_title))
    meta = []
    if n.user:
        meta.append(f"от <b>{esc(n.user)}</b>")
    if n.time:
        meta.append(esc(n.time))
    if meta:
        lines.append(" · ".join(meta))
    if n.kind == "comment" and n.payload:
        lines.append("")
        lines.append(f"«{esc(n.payload)}»")
    return "\n".join(lines).strip()


def _cfg(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


class TaskIntake:
    def __init__(
        self,
        client: AdRobotClient,
        notifier: Optional[TelegramNotifier] = None,
        *,
        me: str = "mch",
        my_id: str = "",
        pool_status: str = "PENDING",
        include_anyone: bool = True,
        exclude_statuses: Optional[set[str]] = None,
        state_file: Optional[Path] = None,
        notify_statuses: Optional[set[str]] = None,
        notify_comments: bool = True,
        notif_state_file: Optional[Path] = None,
    ):
        self.client = client
        self.notifier = notifier
        self.me = me.strip()
        self.my_id = my_id.strip()
        # Пул может тянуть несколько статусов (через запятую), напр.
        # "PENDING,IN_PROCESS" — чтобы взятые в работу задачи не пропадали.
        self.pool_statuses = [
            s.strip().upper() for s in pool_status.split(",") if s.strip()
        ] or ["PENDING"]
        self.include_anyone = include_anyone
        self.exclude_statuses = {
            s.upper() for s in (exclude_statuses or
                                {"DONE", "CLOSED", "CANCELLED", "CANCELED",
                                 "REJECTED", "ARCHIVED"})
        }
        self.state_file = state_file or (BASE_DIR / "storage" / "tasks_state.json")
        self.seen: set[str] = self._load_state()
        # Уведомления ленты AdRobot: статусы (ACCEPTED и др.) + комментарии.
        self.notify_statuses = {
            s.upper() for s in (notify_statuses or {"ACCEPTED", "IN_PROCESS"})
        }
        self.notify_comments = notify_comments
        self.notif_state_file = notif_state_file or (
            BASE_DIR / "storage" / "notifications_state.json")
        self.seen_notifs: set[str] = self._load_notif_state()
        # Кэш последних релевантных задач — для отдачи в UI без лишних запросов.
        self._cache: list[TaskSummary] = []
        self._notif_cache: list[Notification] = []
        self._lock = threading.Lock()

    # ── состояние ────────────────────────────────────────────────
    def _load_state(self) -> set[str]:
        if self.state_file.exists():
            try:
                return set(json.loads(self.state_file.read_text()).get("seen", []))
            except Exception:  # noqa: BLE001
                log.warning("Не прочитать %s, начинаю с чистого состояния", self.state_file)
        return set()

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps({"seen": sorted(self.seen)}, ensure_ascii=False, indent=2)
        )

    def _load_notif_state(self) -> set[str]:
        if self.notif_state_file.exists():
            try:
                return set(json.loads(self.notif_state_file.read_text()).get("seen", []))
            except Exception:  # noqa: BLE001
                log.warning("Не прочитать %s, начинаю с чистого состояния",
                            self.notif_state_file)
        return set()

    def _save_notif_state(self) -> None:
        self.notif_state_file.parent.mkdir(parents=True, exist_ok=True)
        self.notif_state_file.write_text(
            json.dumps({"seen": sorted(self.seen_notifs)}, ensure_ascii=False, indent=2)
        )

    # ── сбор задач (ИСПРАВЛЕННАЯ логика) ─────────────────────────
    def fetch_relevant(self) -> list[TaskSummary]:
        merged: dict[str, TaskSummary] = {}

        # 1) Общий пул: и «Anyone», и «Preferred assignees: <me>».
        #    Тянем по каждому статусу из pool_statuses (PENDING + IN_PROCESS) —
        #    чтобы взятые в работу задачи (статус сменился на IN_PROCESS) тоже
        #    оставались видны в списке.
        #    ВАЖНО: только чтение списка, задачи НЕ берём (no Start working).
        if self.include_anyone:
            pool_needles = ["anyone"]
            if self.me:
                pool_needles.append(self.me)
            for st in self.pool_statuses:
                try:
                    pool = self.client.list_tasks(
                        status=st,
                        assigned_to="ANY",
                        assigned_any_of=pool_needles,
                    )
                    for t in pool:
                        merged[t.uid] = t
                except AuthError:
                    raise
                except Exception as e:  # noqa: BLE001
                    # adrobot.app иногда рвёт TLS/таймаутит — это сеть, не баг;
                    # коротким warning, без огромного трейсбека.
                    log.warning("Не удалось получить пул (%s): %s", st, e)

        # 2) Личные задачи (mch) — любой активный статус, не только PENDING.
        if self.my_id or self.me:
            try:
                if self.my_id:
                    # Серверный фильтр по id исполнителя — надёжнее.
                    mine = self.client.list_tasks(status="ANY", assigned_to=self.my_id)
                else:
                    # Фолбэк: широкий список + клиентский фильтр по имени.
                    mine = self.client.list_tasks(
                        status="ANY", assigned_to="ANY", assigned_any_of=[self.me]
                    )
                for t in mine:
                    if t.status.upper() in self.exclude_statuses:
                        continue
                    merged[t.uid] = t
            except AuthError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("Не удалось получить личные задачи (%s)", self.me)

        tasks = list(merged.values())
        with self._lock:
            self._cache = tasks
        return tasks

    # ── один проход поллера ──────────────────────────────────────
    def poll_once(self, *, notify: bool = True, mark_seen: bool = True) -> list[TaskSummary]:
        """Возвращает НОВЫЕ задачи (которых не было в seen). Шлёт уведомления."""
        tasks = self.fetch_relevant()
        new = [t for t in tasks if t.uid not in self.seen]
        if not new:
            return []

        for t in new:
            if notify and self.notifier:
                try:
                    detail = self.client.get_task(t.url)
                    # «Принять задачу» — callback-кнопка: бот САМ активирует
                    # работу (change-status) и отвечает результатом, без
                    # перехода на сайт. Плюс список задач одной кнопкой.
                    self.notifier.send_message(
                        format_task(detail),
                        url_buttons=[("🔗 Открыть задачу", detail.url)],
                        callback_buttons=[
                            ("▶️ Принять задачу", f"accept:{detail.uid}"),
                            ("📋 Список задач", "tasks"),
                        ],
                    )
                    log.info("Уведомление: %s → %s", t.assigned_to, detail.title or t.uid)
                except Exception:  # noqa: BLE001
                    log.exception("Сбой уведомления по задаче %s", t.uid)
            if mark_seen:
                self.seen.add(t.uid)
        if mark_seen:
            self._save_state()
        return new

    # ── уведомления (ACCEPTED + комментарии) ─────────────────────
    def _is_relevant_notif(self, n: Notification) -> bool:
        if n.kind == "comment":
            return self.notify_comments
        return n.payload.upper() in self.notify_statuses

    def fetch_notifications(self) -> list[Notification]:
        notes = self.client.list_notifications(archived=False)
        with self._lock:
            self._notif_cache = notes
        return notes

    def poll_notifications(self, *, notify: bool = True,
                           mark_seen: bool = True) -> list[Notification]:
        """Новые релевантные уведомления (ACCEPTED + комментарии). Шлёт в Telegram."""
        notes = self.fetch_notifications()
        new = [
            n for n in notes
            if n.uid not in self.seen_notifs and self._is_relevant_notif(n)
        ]
        # Помечаем как просмотренные ВСЕ уведомления страницы (а не только
        # релевантные) — иначе нерелевантные будут пересчитываться каждый раз;
        # дедуп по uid этого не допускает, но seen хранит только то, что видели.
        if not new:
            if mark_seen:
                changed = False
                for n in notes:
                    if n.uid not in self.seen_notifs:
                        self.seen_notifs.add(n.uid)
                        changed = True
                if changed:
                    self._save_notif_state()
            return []

        for n in new:
            if notify and self.notifier:
                try:
                    btn = ("🔗 Открыть задачу", n.task_url) if n.task_url else None
                    self.notifier.send_message(format_notification(n), url_button=btn)
                    log.info("Уведомление [%s] %s — %s",
                             n.kind, n.payload[:30], n.task_title or n.uid)
                except Exception:  # noqa: BLE001
                    log.exception("Сбой Telegram-уведомления %s", n.uid)
        if mark_seen:
            for n in notes:
                self.seen_notifs.add(n.uid)
            self._save_notif_state()
        return new

    def list_notifications_cached(self) -> list[dict]:
        with self._lock:
            return [asdict(n) for n in self._notif_cache]

    # ── данные для UI ────────────────────────────────────────────
    def list_cached(self) -> list[dict]:
        with self._lock:
            return [asdict(t) for t in self._cache]

    def group_duplicates(self) -> list[dict]:
        """Находит кластеры задач на ОДИН оффер (>1 задачи).

        Баер иногда заводит несколько задач на один и тот же оффер (по 1 ленду
        в каждой) вместо одной задачи на несколько лендов. Эти кластеры можно
        обработать за один проход (объединить в одну сессию), сохранив привязку
        каждого ленда к своей задаче. Группируем по точной строке оффера.
        """
        from services.session import offer_key

        with self._lock:
            tasks = list(self._cache)

        clusters: dict[str, list] = {}
        for t in tasks:
            offer = (t.offer or "").strip()
            if not offer:
                continue  # без оффера группировать не по чему
            clusters.setdefault(offer_key(offer), []).append(t)

        out: list[dict] = []
        for items in clusters.values():
            if len(items) < 2:
                continue
            out.append({
                "offer": items[0].offer,
                "offer_key": offer_key(items[0].offer),
                "count": len(items),
                "tasks": [asdict(t) for t in items],
            })
        # Самые крупные кластеры — выше.
        out.sort(key=lambda c: c["count"], reverse=True)
        return out

    def get_detail(self, uid_or_url: str) -> dict:
        d = self.client.get_task(uid_or_url)
        return asdict(d)

    # ── интерактив Telegram: /tasks, кнопки «принять»/«список» ──
    _STATUS_EMOJI = {"REVIEW": "🔎", "ACCEPTED": "✅", "IN_PROCESS": "🔄",
                     "PENDING": "⏳", "NEED_DETAILS": "❓"}

    @staticmethod
    def _norm_status(s: str) -> str:
        return (s or "").strip().upper().replace(" ", "_")

    def _my_tasks(self) -> list[TaskSummary]:
        """Мои задачи всех статусов (в порядке сайта — свежие сверху)."""
        if self.my_id:
            return self.client.list_tasks(status="ANY", assigned_to=self.my_id)
        return self.client.list_tasks(status="ANY", assigned_text=self.me)

    def send_tasks_summary(self) -> None:
        """Последние 5 задач коротко + статистика статусов по последним 30."""
        from collections import Counter
        tasks = self._my_tasks()
        lines = ["📋 <b>Последние 5 задач</b>"]
        if not tasks:
            lines.append("— задач не найдено")
        for i, t in enumerate(tasks[:5], 1):
            em = self._STATUS_EMOJI.get(self._norm_status(t.status), "▫️")
            title = t.title or t.offer or t.uid
            lines.append(f"{i}. {em} {esc(title)} — {esc(t.status or '?')}")
        last30 = tasks[:30]
        if last30:
            c = Counter(self._norm_status(t.status) for t in last30)
            rev, acc, inp = c.get("REVIEW", 0), c.get("ACCEPTED", 0), c.get("IN_PROCESS", 0)
            stat = (f"📊 Из последних {len(last30)}: 🔎 на ревью: {rev} · "
                    f"✅ принято: {acc} · 🔄 в процессе: {inp}")
            other = len(last30) - rev - acc - inp
            if other:
                stat += f" · ▫️ прочее: {other}"
            lines += ["", stat]
        self.notifier.send_message(
            "\n".join(lines),
            callback_buttons=[("🔄 Обновить", "tasks")],
        )

    def _handle_tg_update(self, upd: dict) -> None:
        msg = upd.get("message") or {}
        cb = upd.get("callback_query") or {}
        chat_id = str((msg.get("chat") or {}).get("id")
                      or ((cb.get("message") or {}).get("chat") or {}).get("id") or "")
        if chat_id != str(self.notifier.chat_id):
            return  # чужой чат — игнор

        if cb:
            data = cb.get("data") or ""
            try:
                if data == "tasks":
                    self.notifier.answer_callback(cb["id"])
                    self.send_tasks_summary()
                elif data.startswith("accept:"):
                    uid = data.split(":", 1)[1]
                    detail = self.client.start_working(uid)  # сам активирует работу
                    self.notifier.answer_callback(cb["id"], "Задача принята в работу ✅")
                    self.notifier.send_message(
                        f"▶️ <b>Принята в работу</b>: {esc(detail.title or uid)}",
                        url_button=("🔗 Открыть задачу", detail.url),
                    )
                    log.info("Задача %s принята в работу через Telegram", uid)
            except Exception as exc:  # noqa: BLE001
                log.exception("Сбой обработки callback %r", data)
                try:
                    self.notifier.answer_callback(
                        cb["id"], f"Ошибка: {exc}"[:190], show_alert=True)
                except Exception:  # noqa: BLE001
                    pass
            return

        text = (msg.get("text") or "").strip()
        if text.split("@")[0] in ("/tasks", "/start"):
            try:
                self.send_tasks_summary()
            except Exception:  # noqa: BLE001
                log.exception("Сбой /tasks")

    def run_telegram_forever(self, stop: threading.Event) -> None:
        """Long-poll getUpdates: команды и callback-кнопки бота."""
        if not self.notifier:
            return
        try:
            self.notifier.set_commands([("tasks", "Список задач и статистика")])
        except Exception:  # noqa: BLE001
            log.warning("Не удалось зарегистрировать команды бота", exc_info=True)
        offset = None
        # Дренаж бэклога: не переигрываем старые нажатия после рестарта.
        try:
            upds = self.notifier.get_updates()
            if upds:
                offset = upds[-1]["update_id"] + 1
        except Exception:  # noqa: BLE001
            pass
        log.info("Telegram-листенер запущен (кнопки/команды бота)")
        while not stop.is_set():
            try:
                upds = self.notifier.get_updates(offset)
                for u in upds:
                    offset = u["update_id"] + 1
                    self._handle_tg_update(u)
            except Exception as exc:  # noqa: BLE001
                log.warning("Ошибка Telegram-листенера: %s", exc)
                stop.wait(5)

    # ── фоновый цикл ─────────────────────────────────────────────
    def run_forever(self, stop: threading.Event, interval: int = 60,
                    notify_first_run: bool = False) -> None:
        first_run = not self.state_file.exists()
        notif_first_run = not self.notif_state_file.exists()
        log.info(
            "Поллер задач: me=%s my_id=%s pool_statuses=%s anyone=%s interval=%ss "
            "first_run=%s notify_statuses=%s comments=%s",
            self.me, self.my_id or "-", self.pool_statuses, self.include_anyone,
            interval, first_run, sorted(self.notify_statuses), self.notify_comments,
        )
        while not stop.is_set():
            try:
                if first_run and not notify_first_run:
                    tasks = self.fetch_relevant()
                    self.seen |= {t.uid for t in tasks}
                    self._save_state()
                    log.info("Первый запуск: запомнил %d задач без уведомлений", len(tasks))
                    first_run = False
                else:
                    new = self.poll_once()
                    if new:
                        log.info("Новых задач: %d", len(new))
                    first_run = False
            except AuthError:
                log.exception("Ошибка авторизации AdRobot, повтор через интервал")
            except Exception as exc:  # noqa: BLE001
                log.warning("Ошибка цикла опроса задач: %s", exc)

            # Уведомления (ACCEPTED + комментарии) — отдельный мягкий блок,
            # чтобы сбой здесь не ронял опрос задач и наоборот.
            try:
                if notif_first_run and not notify_first_run:
                    notes = self.fetch_notifications()
                    self.seen_notifs |= {n.uid for n in notes}
                    self._save_notif_state()
                    log.info("Первый запуск: запомнил %d уведомлений без отправки",
                             len(notes))
                    notif_first_run = False
                else:
                    new_n = self.poll_notifications()
                    if new_n:
                        log.info("Новых уведомлений: %d", len(new_n))
                    notif_first_run = False
            except AuthError:
                log.exception("Ошибка авторизации AdRobot (уведомления)")
            except Exception as exc:  # noqa: BLE001
                log.warning("Ошибка цикла опроса уведомлений: %s", exc)

            stop.wait(interval)


# ── фабрика из окружения ─────────────────────────────────────────
def build_from_env() -> Optional[TaskIntake]:
    """Создаёт TaskIntake из .env. None, если AdRobot не настроен."""
    user = _cfg("ADROBOT_USERNAME")
    pwd = _cfg("ADROBOT_PASSWORD")
    if not user or not pwd:
        log.info("AdRobot не настроен (нет ADROBOT_USERNAME/PASSWORD) — приём задач выключен")
        return None

    client = AdRobotClient(
        base_url=_cfg("ADROBOT_BASE_URL", "https://adrobot.app"),
        username=user,
        password=pwd,
    )
    notifier = None
    tg_token, tg_chat = _cfg("TELEGRAM_BOT_TOKEN"), _cfg("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        notifier = TelegramNotifier(tg_token, tg_chat)

    exclude = _cfg("ADROBOT_EXCLUDE_STATUSES")
    notify_statuses = _cfg("ADROBOT_NOTIFY_STATUSES", "ACCEPTED,IN_PROCESS")
    return TaskIntake(
        client,
        notifier,
        me=_cfg("ADROBOT_ME", "mch"),
        my_id=_cfg("ADROBOT_MY_ID"),
        pool_status=_cfg("ADROBOT_STATUS", "PENDING,IN_PROCESS"),
        include_anyone=_cfg("ADROBOT_INCLUDE_ANYONE", "true").lower() == "true",
        exclude_statuses=set(s for s in exclude.split(",") if s.strip()) or None,
        notify_statuses={s.strip().upper() for s in notify_statuses.split(",") if s.strip()} or None,
        notify_comments=_cfg("ADROBOT_NOTIFY_COMMENTS", "true").lower() == "true",
    )


# ── CLI: python -m services.task_intake [list|poll|detail UID] ───
def _main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass

    intake = build_from_env()
    if intake is None:
        sys.exit("AdRobot не настроен — заполни ADROBOT_USERNAME/PASSWORD в .env")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        tasks = intake.fetch_relevant()
        print(f"Релевантных задач: {len(tasks)} "
              f"(пул Anyone={intake.include_anyone}, личные={intake.me}/{intake.my_id or '-'})")
        for t in tasks:
            print(f"  [{t.status}] {t.title} — {t.offer} ({t.category}) "
                  f"от {t.created_by} → {t.assigned_to}")
    elif cmd == "poll":
        new = intake.poll_once(notify=("--notify" in sys.argv))
        print(f"Новых задач: {len(new)}")
        for t in new:
            print(f"  + {t.title} → {t.assigned_to}")
    elif cmd == "detail":
        if len(sys.argv) < 3:
            sys.exit("Использование: python -m services.task_intake detail <UID|URL>")
        import json as _json
        print(_json.dumps(intake.get_detail(sys.argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "notif":
        notes = intake.fetch_notifications()
        relevant = [n for n in notes if intake._is_relevant_notif(n)]
        print(f"Уведомлений: {len(notes)} (релевантных: {len(relevant)}; "
              f"статусы={sorted(intake.notify_statuses)} comments={intake.notify_comments})")
        for n in relevant:
            tag = n.payload if n.kind == "status" else f"💬 {n.payload[:60]}"
            print(f"  [{n.time}] {n.user} · {n.task_title} → {tag}")
    elif cmd == "notif-poll":
        new = intake.poll_notifications(notify=("--notify" in sys.argv))
        print(f"Новых уведомлений: {len(new)}")
        for n in new:
            print(f"  + [{n.kind}] {n.task_title} → {n.payload[:60]}")
    else:
        sys.exit("Команды: list | poll [--notify] | detail <UID> | "
                 "notif | notif-poll [--notify]")


if __name__ == "__main__":
    _main()
