"""Клиент к AdRobot (https://adrobot.app).

Отвечает за:
  - логин по username/password (Django-форма с CSRF),
  - поддержание сессии и авто-переавторизацию при её протухании,
  - получение списка задач-офферов с фильтром по статусу/исполнителю,
  - парсинг карточки задачи в структуру (поля, варианты, активность).

С 2026-10 список и карточка задачи — Angular-SPA, всё берётся из JSON-API
/planning/api/kt_offer_tasks/. Меняющие действия (смена статуса, варианты)
— только явные методы change_status/add_variant/move_variants.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin, quote, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("adrobot.client")

LOGIN_PATH = "/accounts/login/"
TASKS_PATH = "/planning/tasks/offers/"
NOTIFICATIONS_PATH = "/common/notifications/"
# JSON-API списка задач (DRF-пагинация {count,next,previous,results}; сервер
# режет page_size до 100).
TASKS_API_PATH = "/planning/api/kt_offer_tasks/"
TASKS_API_PAGE_SIZE = 100
TASKS_API_MAX_PAGES = 10

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """«2026-09-30T13:43:34.235684» из API → datetime (None, если пусто/битое)."""
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


@dataclass
class TaskSummary:
    """Краткая строка из списка задач."""

    uid: str
    url: str
    title: str = ""
    created_by: str = ""
    assigned_to: str = ""
    status: str = ""
    offer: str = ""
    category: str = ""
    deadline: str = ""
    created: str = ""        # время постановки в формате «18:16 31.07»


@dataclass
class CommentAttachment:
    """Вложение в комментарии задачи (картинка / архив / файл)."""

    url: str
    filename: str = ""
    kind: str = "file"  # 'image' | 'archive' | 'file'


@dataclass
class Comment:
    """Комментарий в ленте Activity задачи."""

    author: str = ""
    time: str = ""
    text: str = ""
    attachments: list[CommentAttachment] = field(default_factory=list)


@dataclass
class TaskDetail:
    """Полная карточка задачи."""

    uid: str
    url: str
    title: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    variants: list[str] = field(default_factory=list)
    activity: list[dict[str, str]] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)
    # все вложения из комментариев одним списком (для удобного доступа из UI)
    attachments: list[CommentAttachment] = field(default_factory=list)
    # подписи доступных кнопок статуса (например "Start working", "Need details")
    actions: list[str] = field(default_factory=list)
    # те же кнопки как {код статуса: подпись} — что сейчас можно сделать с задачей
    transitions: dict[str, str] = field(default_factory=dict)


@dataclass
class Notification:
    """Одно уведомление из ленты /common/notifications/.

    kind:
      'status'  — смена статуса задачи (payload = код, напр. 'ACCEPTED');
      'comment' — комментарий к задаче (payload = текст комментария).
    """

    uid: str                 # стабильный id (из ссылки Archive) — для дедупликации
    kind: str = "status"
    payload: str = ""        # код статуса или текст комментария
    user: str = ""           # кто совершил действие
    task_title: str = ""     # название задачи
    task_url: str = ""       # ссылка на задачу
    time: str = ""           # человекочитаемое время ("20 Jun 15:11")


class AuthError(RuntimeError):
    pass


class AdRobotClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: int = 30,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._logged_in = False

    # ---------- low level ----------

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _csrf(self) -> str:
        token = self.session.cookies.get("csrftoken")
        if not token:
            # подтянуть страницу логина, чтобы получить cookie
            self.session.get(self._url(LOGIN_PATH), timeout=self.timeout)
            token = self.session.cookies.get("csrftoken", "")
        return token

    def _is_login_page(self, resp: requests.Response) -> bool:
        if LOGIN_PATH in resp.url:
            return True
        # форма логина содержит поле password и нет признаков приложения
        return ('name="password"' in resp.text and "csrfmiddlewaretoken" in resp.text
                and "Lander" not in resp.text)

    # ---------- auth ----------

    def login(self) -> None:
        login_url = self._url(LOGIN_PATH) + f"?next={TASKS_PATH}"
        page = self.session.get(login_url, timeout=self.timeout)
        soup = BeautifulSoup(page.text, "html.parser")
        token_input = soup.select_one('input[name="csrfmiddlewaretoken"]')
        token = token_input["value"] if token_input else self._csrf()

        resp = self.session.post(
            login_url,
            data={
                "csrfmiddlewaretoken": token,
                "username": self.username,
                "password": self.password,
                "next": TASKS_PATH,
            },
            headers={"Referer": login_url},
            timeout=self.timeout,
            allow_redirects=True,
        )
        # после успешного логина нас редиректит на TASKS_PATH (страница приложения)
        if self._is_login_page(resp):
            raise AuthError("Login failed: проверьте username/password")
        self._logged_in = True
        log.info("Logged in as %s", self.username)

    def _get(self, path: str, **kwargs) -> requests.Response:
        """GET с авто-переавторизацией при протухшей сессии."""
        if not self._logged_in:
            self.login()
        url = self._url(path)
        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
            except requests.RequestException as exc:
                last_err = exc
                log.warning("Сетевая ошибка (попытка %d/2): %s", attempt + 1, exc)
                self.session.close()
                self.session = requests.Session()
                self.session.headers.update({"User-Agent": USER_AGENT})
                self._logged_in = False
                self.login()
                continue
            if self._is_login_page(resp):
                log.info("Session expired, re-login")
                self._logged_in = False
                self.login()
                resp = self.session.get(url, timeout=self.timeout, **kwargs)
                if self._is_login_page(resp):
                    raise AuthError("Не удалось переавторизоваться")
            resp.raise_for_status()
            return resp
        raise last_err or RuntimeError("Не удалось выполнить GET-запрос")

    # ---------- tasks ----------

    def list_tasks(
        self,
        status: Optional[str] = None,
        assigned_to: Optional[str] = None,
        assigned_text: Optional[str] = None,
        assigned_any_of: Optional[list[str]] = None,
    ) -> list[TaskSummary]:
        """Список задач.

        assigned_to     — серверный фильтр по id исполнителя (или "ANY").
        assigned_text   — клиентский фильтр: оставить строки, где assigned_to
                          содержит подстроку (legacy, один needle).
        assigned_any_of — клиентский фильтр: оставить строки, где assigned_to
                          содержит ЛЮБУЮ из подстрок (напр. ["anyone", "mch"]).
        """
        # Список задач — Angular-SPA (с 2026-09 в HTML только <app-root>),
        # данные берём из того же JSON-API, что и фронт AdRobot.
        params: dict[str, str | int] = {"page_size": TASKS_API_PAGE_SIZE}
        any_status = not status or status.upper() == "ANY"
        if not any_status:
            params["status"] = status
        if assigned_to and assigned_to.upper() != "ANY":
            params["assigned_to"] = assigned_to
        # Без фильтра статуса это вся история (сотни задач) — хватит свежей
        # страницы, как раньше в HTML-списке; по статусу — листаем всё.
        max_pages = 1 if any_status else TASKS_API_MAX_PAGES
        tasks: list[TaskSummary] = []
        for page in range(1, max_pages + 1):
            params["page"] = page
            data = self._get(TASKS_API_PATH, params=params).json()
            tasks.extend(self._parse_list_json(data.get("results") or []))
            if not data.get("next"):
                break
        needles = [n.strip().lower() for n in (assigned_any_of or []) if n.strip()]
        if assigned_text and assigned_text.strip():
            needles.append(assigned_text.strip().lower())
        if needles:
            tasks = [
                t for t in tasks
                if any(n in t.assigned_to.lower() for n in needles)
            ]
        return tasks

    def _parse_list_json(self, results: list[dict]) -> list[TaskSummary]:
        """Строки из /planning/api/kt_offer_tasks/ → TaskSummary.

        Поля приводим к виду старого HTML-списка, чтобы не трогать потребителей:
        title «30.09 PR Solveex TR» (как <title> карточки), assigned_to —
        ник / «Anyone» / «Preferred assignees: nch» (на это завязан фильтр пула).
        """
        out: list[TaskSummary] = []
        for r in results:
            uid = r.get("uuid") or ""
            if not uid:
                continue
            group = (r.get("kt_offer_group") or {}).get("name") or ""
            created = _parse_iso(r.get("created_at"))
            deadline = _parse_iso(r.get("deadline"))
            if r.get("assigned_to"):
                assigned = r["assigned_to"]
            elif r.get("preferred_assignees"):
                assigned = "Preferred assignees: " + ", ".join(r["preferred_assignees"])
            else:
                assigned = "Anyone"
            out.append(TaskSummary(
                uid=uid,
                url=r.get("web_url") or self._url(f"{TASKS_PATH}{uid}/"),
                title=" ".join(x for x in (created.strftime("%d.%m") if created else "", group) if x),
                created_by=r.get("created_by") or "",
                assigned_to=assigned,
                status=r.get("status") or "",
                offer=group,
                category=r.get("request_category_display") or r.get("request_category") or "",
                deadline=deadline.strftime("%d.%m") if deadline else "",
                created=created.strftime("%H:%M %d.%m") if created else "",
            ))
        return out

    def get_task(self, uid_or_url: str) -> TaskDetail:
        if uid_or_url.startswith("http"):
            url = uid_or_url
            m = re.search(r"/offers/([0-9a-f-]{36})/", url)
            uid = m.group(1) if m else uid_or_url
            path = url[len(self.base_url):] if url.startswith(self.base_url) else url
        else:
            uid = uid_or_url.strip("/").split("/")[-1]
            url = self._url(f"{TASKS_PATH}{uid}/")
        # Карточка — Angular-SPA (с 2026-10 в HTML только <app-root>), данные
        # берём из JSON-API, которым пользуется сам фронт AdRobot.
        data = self._get(f"{TASKS_API_PATH}{uid}/").json()
        detail = self._parse_detail_json(data)
        detail.uid = data.get("uuid") or uid
        detail.url = url
        return detail

    def _post_api(self, path: str, payload: dict) -> dict:
        """POST в JSON-API AdRobot (Django REST + сессия).

        Angular шлёт CSRF стандартно; т.к. сервер выдаёт cookie `csrftoken`,
        кладём его и в Django-заголовок X-CSRFToken, и в Angular X-XSRF-TOKEN.
        Ошибка DRF (`{"detail": ...}` / `{"поле": [...]}`) → RuntimeError с текстом.
        """
        if not self._logged_in:
            self.login()
        url = self._url(path)
        for attempt in range(2):
            token = self._csrf()
            resp = self.session.post(
                url, json=payload, timeout=self.timeout,
                headers={"X-CSRFToken": token, "X-XSRF-TOKEN": token,
                         "Referer": self._url(TASKS_PATH),
                         "Accept": "application/json"},
            )
            if resp.status_code in (401, 403) and attempt == 0 \
                    and "csrf" not in resp.text.lower():
                # протухла сессия — перелогин и повтор
                self._logged_in = False
                self.login()
                continue
            break
        if resp.status_code >= 400:
            raise RuntimeError(self._api_error(resp))
        try:
            return resp.json()
        except ValueError:
            return {}

    @staticmethod
    def _api_error(resp: requests.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return f"HTTP {resp.status_code}: {resp.text[:200]}"
        if isinstance(data, dict):
            if data.get("detail"):
                return str(data["detail"])
            parts = []
            for k, v in data.items():
                msg = "; ".join(map(str, v)) if isinstance(v, list) else str(v)
                parts.append(msg if k == "non_field_errors" else f"{k}: {msg}")
            if parts:
                return " | ".join(parts)
        if isinstance(data, list) and data:
            return "; ".join(map(str, data))
        return f"HTTP {resp.status_code}"

    # ---------- смена статуса задачи (ЕДИНСТВЕННОЕ меняющее действие) ----------

    # Допустимые целевые статусы (видны на карточке как кнопки change-status).
    # REVIEW — кнопка «Submit for review» на карточке задачи.
    ALLOWED_STATUS_CHANGES = {"IN_PROCESS", "NEED_DETAILS", "REVIEW"}

    def change_status(self, uid: str, status: str) -> TaskDetail:
        """Меняет статус задачи (напр. PENDING → IN_PROCESS, кнопка «Start working»).

        Повторяет кнопку карточки: POST /planning/api/kt_offer_tasks/<uid>/change_status/
        {status}. Доступные переходы сервер отдаёт в possible_status_transitions —
        если нужного нет, не шлём (AdRobot всё равно отклонит). Возвращает
        обновлённую карточку.
        """
        status = (status or "").strip().upper()
        if status not in self.ALLOWED_STATUS_CHANGES:
            raise ValueError(f"Недопустимый статус: {status}")
        uid = uid.strip("/").split("/")[-1]
        current = self.get_task(uid)
        if status not in current.transitions:
            allowed = ", ".join(f"{k} ({v})" for k, v in current.transitions.items()) or "нет"
            raise RuntimeError(
                f"Переход в {status} сейчас недоступен "
                f"(статус {current.fields.get('Status', '?')}; можно: {allowed})")
        self._post_api(f"{TASKS_API_PATH}{uid}/change_status/", {"status": status})
        log.info("Задача %s → статус %s", uid, status)
        return self.get_task(uid)

    def start_working(self, uid: str) -> TaskDetail:
        """PENDING → IN_PROCESS («Start working»)."""
        return self.change_status(uid, "IN_PROCESS")

    # ---------- варианты задачи (Add variant / Move all / Review) ----------

    def add_variant(self, uid: str, offer_id: int | str) -> None:
        """Добавляет вариант (id залитого ленда Keitaro) к задаче.

        Повторяет «Add variant» карточки: POST .../<uid>/variants/ {kt_offer_id}.
        Ошибку валидации AdRobot (нет такого ленда и т.п.) поднимаем RuntimeError."""
        uid = uid.strip("/").split("/")[-1]
        try:
            kt_id = int(str(offer_id).strip())
        except ValueError:
            raise ValueError(f"id ленда Keitaro должен быть числом, а не {offer_id!r}")
        try:
            self._post_api(f"{TASKS_API_PATH}{uid}/variants/", {"kt_offer_id": kt_id})
        except RuntimeError as e:
            raise RuntimeError(f"AdRobot не принял вариант {kt_id}: {e}") from None
        log.info("Задача %s: добавлен вариант %s", uid, kt_id)

    def move_variants(self, uid: str, scope: str) -> None:
        """«Move all to private/public group» (scope: 'private' | 'public').

        Повторяет кнопку карточки: POST .../<uid>/variants/move_to_<scope>/."""
        scope = (scope or "").strip().lower()
        if scope not in ("private", "public"):
            raise ValueError(f"scope должен быть 'private' или 'public', а не {scope!r}")
        uid = uid.strip("/").split("/")[-1]
        self._post_api(f"{TASKS_API_PATH}{uid}/variants/move_to_{scope}/", {})
        log.info("Задача %s: варианты перемещены в %s group", uid, scope)

    def submit_review(self, uid: str) -> TaskDetail:
        """«Submit for review» — переводит задачу в статус REVIEW."""
        return self.change_status(uid, "REVIEW")

    # ---------- offer product images ----------

    OFFER_GROUPS_API_PATH = "/kt/api/offer_groups/"

    def get_offer_product_images(self, offer_name: str) -> list[str]:
        """URL фото продукта группы офферов (по названию оффера).

        Раньше страница /kt/offer_groups/ отдавала готовый HTML. Теперь это
        Angular-SPA (в теле только <app-root>), а данные грузятся из JSON-API
        /kt/api/offer_groups/?q=<term> — каждая группа несёт поле `image_url`
        (то самое, что рендерится в блок `<a class="ktogd__img-link">`).

        Возвращаем `image_url` групп: сначала с точным совпадением имени
        (без учёта регистра), иначе — всех найденных. Дедуп, порядок сохранён.
        """
        name = (offer_name or "").strip()
        if not name:
            return []
        path = self.OFFER_GROUPS_API_PATH + "?q=" + quote(name)
        resp = self._get(path)
        try:
            data = resp.json()
        except ValueError:
            return []
        # DRF-пагинация {count,next,previous,results:[...]} либо голый список.
        results = data.get("results") if isinstance(data, dict) else data
        if not isinstance(results, list):
            return []

        exact: list[str] = []
        other: list[str] = []
        for g in results:
            if not isinstance(g, dict):
                continue
            url = (g.get("image_url") or "").strip()
            if not url:
                continue
            if (g.get("name") or "").strip().lower() == name.lower():
                exact.append(url)
            else:
                other.append(url)

        urls: list[str] = []
        for url in (exact or other):
            if url not in urls:
                urls.append(url)
        return urls

    # ---------- notifications ----------

    _ARCHIVE_RE = re.compile(
        r"/common/notifications/([0-9a-f-]{36})/toggle_is_archived"
    )

    def list_notifications(self, archived: bool = False) -> list[Notification]:
        """Лента уведомлений залогиненного аккаунта (свежие сверху).

        Парсит /common/notifications/?archived=<bool>. Каждое уведомление —
        строка таблицы со ссылкой Archive (в ней лежит uid) и ячейкой текста
        вида «User <b>avp</b>, <a>задача</a> : ACCEPTED|текст комментария».
        """
        path = f"{NOTIFICATIONS_PATH}?archived={'true' if archived else 'false'}"
        resp = self._get(path)
        return self._parse_notifications(resp.text)

    # Хосты, с которых разрешено скачивать вложения (защита от SSRF).
    _ATTACHMENT_HOSTS = {"robotmediaassets.com"}

    def download_attachment(self, url: str) -> tuple[bytes, str, str]:
        """Скачивает вложение комментария через авторизованную сессию.

        Возвращает (содержимое, имя_файла, content_type). Разрешены только
        доверенные хосты (robotmediaassets.com и сам adrobot) — чтобы прокси
        нельзя было использовать для запросов к произвольным адресам (SSRF).
        """
        from urllib.parse import urlparse

        host = (urlparse(url).hostname or "").lower()
        allowed = set(self._ATTACHMENT_HOSTS)
        allowed.add((urlparse(self.base_url).hostname or "").lower())
        if not any(host == h or host.endswith("." + h) for h in allowed if h):
            raise ValueError(f"Хост вложения не разрешён: {host}")

        if not self._logged_in:
            self.login()
        r = self.session.get(url, timeout=max(self.timeout, 60))
        if self._is_login_page(r):
            self._logged_in = False
            self.login()
            r = self.session.get(url, timeout=max(self.timeout, 60))
        r.raise_for_status()
        filename = self._attachment_filename(url) or "attachment"
        ctype = r.headers.get("content-type", "application/octet-stream")
        return r.content, filename, ctype

    def _parse_notifications(self, html: str) -> list[Notification]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[Notification] = []
        for tr in soup.select("tr"):
            arch = tr.find("a", href=self._ARCHIVE_RE)
            if not arch:
                continue
            m = self._ARCHIVE_RE.search(arch.get("href", ""))
            if not m:
                continue
            uid = m.group(1)

            # Ячейка с текстом уведомления (read/unread — класс *_notification).
            cell = tr.find("td", class_=re.compile("notification"))
            if cell is None:
                continue

            time_el = cell.find(class_="grayish")
            time_txt = time_el.get_text(" ", strip=True) if time_el else ""

            user_el = cell.find("b")
            user = user_el.get_text(" ", strip=True) if user_el else ""

            link = cell.find("a", href=re.compile(r"/planning/tasks/"))
            task_title = link.get_text(" ", strip=True) if link else ""
            task_url = self._url(link["href"]) if link else ""

            payload = self._notif_payload(cell, link)
            # Код статуса (ACCEPTED/REVIEW/IN_PROCESS/...) — только заглавные/подчёрк.
            kind = "status" if re.fullmatch(r"[A-Z][A-Z_]*", payload) else "comment"

            out.append(Notification(
                uid=uid, kind=kind, payload=payload, user=user,
                task_title=task_title, task_url=task_url, time=time_txt,
            ))
        return out

    @staticmethod
    def _notif_payload(cell, link) -> str:
        """Текст после ссылки на задачу (статус или комментарий)."""
        if link is not None:
            parts: list[str] = []
            for n in link.next_siblings:
                parts.append(n if isinstance(n, str) else n.get_text(" "))
            tail = " ".join(parts)
        else:
            # Нет ссылки — берём всё после последнего двоеточия.
            tail = cell.get_text(" ", strip=True)
            tail = tail.rsplit(":", 1)[-1] if ":" in tail else ""
        tail = re.sub(r"\s+", " ", tail).strip()
        return tail.lstrip(":").strip()

    @staticmethod
    def _clean_text(value) -> str:
        """Текст из API: \r\n → \n, пробелы схлопнуты, пустые строки убраны."""
        lines = [re.sub(r"[ \t]+", " ", ln).strip()
                 for ln in str(value or "").replace("\r", "").split("\n")]
        return "\n".join(ln for ln in lines if ln)

    @staticmethod
    def _fmt_time(value: Optional[str]) -> str:
        """ISO из API → «02 Oct 22:25» (как раньше в карточке/ленте)."""
        dt = _parse_iso(value)
        return dt.strftime("%d %b %H:%M") if dt else (value or "")

    @staticmethod
    def _fmt_price(price, currency: str) -> str:
        if price in (None, ""):
            return ""
        if isinstance(price, float) and price.is_integer():
            price = int(price)
        return " ".join(x for x in (str(price), currency or "") if x)

    def _parse_detail_json(self, d: dict) -> TaskDetail:
        """JSON карточки /planning/api/kt_offer_tasks/<uid>/ → TaskDetail.

        Поля приводим к подписям старой серверной карточки («Offer»,
        «Reference lander», «Lander price»…) — на них завязаны session.py,
        task_intake.py и фронт (TasksPage/TaskDetailsModal/NewSessionPage).
        """
        detail = TaskDetail(uid=d.get("uuid") or "", url=d.get("web_url") or "")
        group = d.get("kt_offer_group") or {}
        ref = d.get("kt_offer") or {}
        created = _parse_iso(d.get("created_at"))
        deadline = _parse_iso(d.get("deadline"))

        # Заголовок как у <title> старой карточки и в списке: «04.10 DI GlucoZen CO».
        detail.title = " ".join(
            x for x in (created.strftime("%d.%m") if created else "",
                        group.get("name") or "") if x)

        audience = " ".join(x for x in (
            group.get("gender") or "",
            f"{group['minimum_age']}+" if group.get("minimum_age") else "") if x)
        ref_text = ""
        if ref.get("name"):
            ref_text = ref["name"]
            if ref.get("kt_id") and f"(ID: {ref['kt_id']})" not in ref_text:
                ref_text += f" (ID: {ref['kt_id']})"
        f = {
            "Created by": d.get("created_by") or "",
            "Offer": group.get("name") or "",
            "Reference lander": ref_text,
            "Category": d.get("request_category_display") or d.get("request_category") or "",
            "Target audience": audience,
            "Lander price": self._fmt_price(group.get("lander_price"), group.get("currency") or ""),
            "Promotions": self._clean_text(group.get("promotions")),
            "Comments": self._clean_text(group.get("comments")),
            "Status": d.get("status") or "",
            "Assigned to": d.get("assigned_to") or "",
            "Preferred assignees": ", ".join(d.get("preferred_assignees") or []),
            "Deadline": deadline.strftime("%d.%m.%Y") if deadline else "",
            "Description": self._clean_text(d.get("description")),
        }
        detail.fields = {k: v for k, v in f.items() if v}

        # Доступные кнопки статуса: «Start working», «Need details», «Submit for review»…
        for t in d.get("possible_status_transitions") or []:
            code = (t.get("status") or "").upper()
            if code:
                detail.transitions[code] = t.get("label") or code
                detail.actions.append(t.get("label") or code)

        for v in d.get("variants") or []:
            o = v.get("kt_offer") or {}
            txt = o.get("name") or (str(o["kt_id"]) if o.get("kt_id") else "")
            if txt:
                detail.variants.append(txt + (" · ACCEPTED" if v.get("accepted") else ""))

        # Лента событий (свежие сверху, как отдаёт API).
        for ev in d.get("events") or []:
            etype = (ev.get("event_type") or "").upper()
            author = ev.get("user_username") or ""
            ev_time = self._fmt_time(ev.get("created_at"))
            content = self._clean_text(ev.get("content"))
            att_url = (ev.get("attachment_url") or "").strip()
            if etype == "STATUS_CHANGE":
                text = f"сменил статус на {content}"
            elif etype == "COMMENT":
                text = content or ("[вложение]" if att_url else "")
            else:
                text = ": ".join(x for x in (etype.lower().replace("_", " "), content) if x)
            detail.activity.append({"author": author, "time": ev_time, "text": text})

            if etype != "COMMENT":
                continue
            comment = Comment(author=author, time=ev_time, text=content)
            urls = ([att_url] if att_url else []) + [
                m.group(0).rstrip(".,);]")
                for m in re.finditer(r"https?://[^\s<>\"')]+", content)]
            for href in urls:
                if any(x.url == href for x in comment.attachments):
                    continue
                att = CommentAttachment(
                    url=href,
                    filename=self._attachment_filename(href),
                    kind=("image" if href == att_url and ev.get("file_type") == "IMAGE"
                          else self._attachment_kind(href)),
                )
                comment.attachments.append(att)
                detail.attachments.append(att)
            if comment.text or comment.attachments:
                detail.comments.append(comment)

        # Облачные ссылки (Google Drive / Яндекс Диск) из Описания задачи —
        # ленд может быть залит туда, а не вложением.
        from connectors.cloud import extract_cloud_links
        desc = detail.fields.get("Description", "") or ""
        for link in extract_cloud_links(desc):
            if not any(a.url == link["url"] for a in detail.attachments):
                detail.attachments.append(CommentAttachment(
                    url=link["url"],
                    filename=f"архив ({'Google Drive' if link['kind'] == 'gdrive' else 'Яндекс Диск'})",
                    kind="archive",
                ))

        # Ссылки на сайты-лендинги (баер кидает URL вместо архива) — из Описания
        # и текста комментариев. Их можно скачать скрапером (kind=site).
        texts = [desc] + [c.text for c in detail.comments if c.text]
        for url in self._extract_site_urls(" \n".join(texts)):
            if not any(a.url == url for a in detail.attachments):
                host = urlparse(url).hostname or url
                detail.attachments.append(CommentAttachment(
                    url=url, filename=host, kind="site"))

        return detail

    # Хосты, которые НЕ являются сайтами-лендингами (вложения/облака/сам трекер).
    _NON_SITE_HOSTS = ("robotmediaassets.com", "adrobot.app",
                        "drive.google.com", "docs.google.com",
                        "disk.yandex", "yadi.sk")

    @classmethod
    def _extract_site_urls(cls, text: str) -> list[str]:
        """Находит http(s)-ссылки на сайты-лендинги в тексте (для скрапинга).

        Исключает вложения/облака/сам AdRobot и прямые ссылки на файлы
        (картинки/архивы — они уже обрабатываются как attachments)."""
        if not text:
            return []
        out: list[str] = []
        for m in re.finditer(r"https?://[^\s<>\"')]+", text):
            url = m.group(0).rstrip(".,);]")
            low = url.lower()
            if any(h in low for h in cls._NON_SITE_HOSTS):
                continue
            # прямые ссылки на файлы — это вложения, не сайты
            if re.search(r"\.(?:png|jpe?g|webp|gif|bmp|svg|zip|rar|7z|tar|gz|tgz|pdf|mp4)(?:\?|#|$)", low):
                continue
            if url not in out:
                out.append(url)
        return out

    @staticmethod
    def _attachment_filename(href: str, anchor=None) -> str:
        """Имя файла вложения: последний сегмент URL, иначе текст ссылки."""
        from urllib.parse import unquote, urlparse
        path = urlparse(href).path
        name = unquote(path.rsplit("/", 1)[-1]) if path else ""
        if not name and anchor is not None:
            name = anchor.get_text(" ", strip=True)
        return name.strip()

    @classmethod
    def _attachment_kind(cls, href: str) -> str:
        from connectors.cloud import cloud_kind
        if cloud_kind(href):  # Google Drive / Яндекс Диск — это архив ленда
            return "archive"
        m = re.search(r"\.([a-z0-9]{1,5})(?:\?|#|$)", href, re.I)
        ext = (m.group(1).lower() if m else "")
        if ext in {"png", "jpg", "jpeg", "webp", "gif", "bmp", "svg"}:
            return "image"
        if ext in {"zip", "rar", "7z", "tar", "gz", "tgz"}:
            return "archive"
        # Внешняя http-ссылка без расширения файла (не вложение/трекер) — сайт-ленд.
        low = (href or "").lower()
        if low.startswith("http") and not any(h in low for h in cls._NON_SITE_HOSTS):
            return "site"
        return "file"
