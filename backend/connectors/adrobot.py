"""Клиент к AdRobot (https://adrobot.app).

Отвечает за:
  - логин по username/password (Django-форма с CSRF),
  - поддержание сессии и авто-переавторизацию при её протухании,
  - получение списка задач-офферов с фильтром по статусу/исполнителю,
  - парсинг карточки задачи в структуру (поля, варианты, активность).

Ничего не меняет на аккаунте: только GET-запросы на чтение
(переходы статуса вроде "Start working" здесь НЕ реализованы).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, quote

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("adrobot.client")

LOGIN_PATH = "/accounts/login/"
TASKS_PATH = "/planning/tasks/offers/"
NOTIFICATIONS_PATH = "/common/notifications/"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


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
        params = []
        if status and status.upper() != "ANY":
            params.append(("status", status))
        if assigned_to and assigned_to.upper() != "ANY":
            params.append(("assigned_to", assigned_to))
        query = "&".join(f"{k}={quote(str(v))}" for k, v in params)
        path = TASKS_PATH + (f"?{query}" if query else "")
        resp = self._get(path)
        tasks = self._parse_list(resp.text)
        needles = [n.strip().lower() for n in (assigned_any_of or []) if n.strip()]
        if assigned_text and assigned_text.strip():
            needles.append(assigned_text.strip().lower())
        if needles:
            tasks = [
                t for t in tasks
                if any(n in t.assigned_to.lower() for n in needles)
            ]
        return tasks

    def _parse_list(self, html: str) -> list[TaskSummary]:
        soup = BeautifulSoup(html, "html.parser")
        out: list[TaskSummary] = []
        for tr in soup.select("tr"):
            link = tr.find("a", href=re.compile(r"/planning/tasks/offers/[0-9a-f-]{36}/"))
            if not link:
                continue
            href = link["href"]
            m = re.search(r"/planning/tasks/offers/([0-9a-f-]{36})/", href)
            if not m:
                continue
            uid = m.group(1)
            tds = tr.find_all("td", recursive=False)
            ts = TaskSummary(uid=uid, url=self._url(href))
            ts.title = link.get_text(" ", strip=True)
            if tds:
                # колонка Task: дата + Deadline
                dl = re.search(r"Deadline:\s*([^<\n]+)", tds[0].get_text("\n", strip=True))
                if dl:
                    ts.deadline = dl.group(1).strip()
            # колонки: Task, Created by, Assigned to, Status, Offer, Category, ...
            def col(i):
                return tds[i].get_text(" ", strip=True) if i < len(tds) else ""
            ts.created_by = col(1)
            ts.assigned_to = col(2)
            ts.status = col(3)
            ts.offer = col(4)
            ts.category = col(5)
            out.append(ts)
        return out

    def get_task(self, uid_or_url: str) -> TaskDetail:
        if uid_or_url.startswith("http"):
            url = uid_or_url
            m = re.search(r"/offers/([0-9a-f-]{36})/", url)
            uid = m.group(1) if m else uid_or_url
            path = url[len(self.base_url):] if url.startswith(self.base_url) else url
        else:
            uid = uid_or_url.strip("/").split("/")[-1]
            path = f"{TASKS_PATH}{uid}/"
            url = self._url(path)
        resp = self._get(path)
        detail = self._parse_detail(resp.text)
        detail.uid = uid
        detail.url = url
        return detail

    # ---------- смена статуса задачи (ЕДИНСТВЕННОЕ меняющее действие) ----------

    # Допустимые целевые статусы (видны на карточке как кнопки change-status).
    ALLOWED_STATUS_CHANGES = {"IN_PROCESS", "NEED_DETAILS"}

    def change_status(self, uid: str, status: str) -> TaskDetail:
        """Меняет статус задачи (напр. PENDING → IN_PROCESS, кнопка «Start working»).

        ВНИМАНИЕ: это ЕДИНСТВЕННОЕ действие коннектора, изменяющее данные в
        AdRobot. Повторяет ссылку change-status с карточки. Возвращает
        обновлённую карточку.
        """
        status = (status or "").strip().upper()
        if status not in self.ALLOWED_STATUS_CHANGES:
            raise ValueError(f"Недопустимый статус: {status}")
        uid = uid.strip("/").split("/")[-1]
        next_path = f"{TASKS_PATH}{uid}/"
        path = (f"{TASKS_PATH}{uid}/change-status/"
                f"?status={status}&next={quote(next_path)}")
        self._get(path)  # редирект на карточку
        log.info("Задача %s → статус %s", uid, status)
        return self.get_task(uid)

    def start_working(self, uid: str) -> TaskDetail:
        """PENDING → IN_PROCESS («Start working»)."""
        return self.change_status(uid, "IN_PROCESS")

    def start_working_url(self, uid: str) -> str:
        """Абсолютный URL кнопки «Start working» (PENDING → IN_PROCESS).

        Для кнопки в Telegram-уведомлении о новой задаче: клик откроет эту
        ссылку в браузере (где есть сессия AdRobot) и переведёт задачу в работу,
        после чего редиректит на карточку — там видно, принята задача или нет.
        """
        uid = uid.strip("/").split("/")[-1]
        next_path = f"{TASKS_PATH}{uid}/"
        return self._url(f"{TASKS_PATH}{uid}/change-status/"
                         f"?status=IN_PROCESS&next={quote(next_path)}")

    # ---------- offer product images ----------

    OFFER_GROUPS_PATH = "/kt/offer_groups/extended/"

    def get_offer_product_images(self, offer_name: str) -> list[str]:
        """URL фото продукта со страницы оффера (по точному названию оффера).

        Парсит /kt/offer_groups/extended/?search_term=<offer> → блоки
        `.product_icon_wrapper a[href]` (изображение продукта на robotmediaassets).
        """
        if not offer_name or not offer_name.strip():
            return []
        path = self.OFFER_GROUPS_PATH + "?search_term=" + quote(offer_name.strip())
        resp = self._get(path)
        soup = BeautifulSoup(resp.text, "html.parser")
        urls: list[str] = []
        for wrap in soup.select(".product_icon_wrapper"):
            a = wrap.find("a", href=True) or wrap.find("img", src=True)
            url = (a.get("href") or a.get("src")) if a else ""
            if url and url not in urls:
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
    def _value_text(node) -> str:
        """Текст из .detail-value с сохранением переносов строк (<br>)."""
        for br in node.find_all("br"):
            br.replace_with("\n")
        raw = node.get_text("\n")
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in raw.split("\n")]
        return "\n".join(ln for ln in lines if ln)

    def _parse_detail(self, html: str) -> TaskDetail:
        soup = BeautifulSoup(html, "html.parser")
        detail = TaskDetail(uid="", url="")

        title_tag = soup.find("title")
        if title_tag:
            t = title_tag.get_text(strip=True)
            detail.title = re.sub(r"^Lander Task:\s*", "", t)

        # Поля карточки. Подпись лежит в .detail-label или .section-header,
        # значение — в следующем .detail-value (работает для .detail-field,
        # .status-section и блока Description с .section-header).
        def has_cls(el, name):
            return el.has_attr("class") and name in el["class"]

        targets = soup.find_all(
            lambda el: el.name == "div"
            and el.has_attr("class")
            and any(c in el["class"] for c in
                    ("detail-label", "section-header", "detail-value"))
        )
        last_label = None
        for el in targets:
            if has_cls(el, "detail-value"):
                if last_label:
                    detail.fields[last_label] = self._value_text(el)
                    last_label = None
            else:  # detail-label / section-header
                last_label = el.get_text(" ", strip=True)

        # Доступные действия (кнопки статуса), напр. "Start working" / "Need details"
        for b in soup.select(
            ".status-buttons button, .status-buttons a, "
            ".status-buttons input[type=submit]"
        ):
            label = b.get_text(" ", strip=True) or b.get("value", "")
            if label:
                detail.actions.append(label.strip())

        # Варианты: .variant-card
        for v in soup.select(".variant-card"):
            txt = re.sub(r"\s+", " ", v.get_text(" ", strip=True)).strip()
            if txt:
                detail.variants.append(txt)

        # активность: .event-item
        for ev in soup.select(".event-item"):
            author_el = ev.select_one(".event-author")
            time_el = ev.select_one(".event-time")
            author = author_el.get_text(" ", strip=True) if author_el else ""
            ev_time = time_el.get_text(" ", strip=True) if time_el else ""
            status_badge = ev.select_one(".event-status-badge")
            comment_el = ev.select_one(".event-comment, .event-text, .event-body")
            if status_badge:
                text = f"сменил статус на {status_badge.get_text(strip=True)}"
            elif comment_el:
                text = comment_el.get_text(" ", strip=True)
            else:
                sys_text = ev.select_one(".event-system-text")
                text = sys_text.get_text(" ", strip=True) if sys_text else \
                    re.sub(r"\s+", " ", ev.get_text(" ", strip=True))
                if author:
                    text = text.replace(author, "", 1).strip()
            detail.activity.append({"author": author, "time": ev_time, "text": text})

        # Комментарии с вложениями: .event-item.event-comment
        for ev in soup.select(".event-item.event-comment"):
            author_el = ev.select_one(".event-author")
            time_el = ev.select_one(".event-time")
            text_el = ev.select_one(".comment-text")
            comment = Comment(
                author=author_el.get_text(" ", strip=True) if author_el else "",
                time=time_el.get_text(" ", strip=True) if time_el else "",
                text=text_el.get_text(" ", strip=True) if text_el else "",
            )
            for a in ev.select(".comment-attachment a[href]"):
                href = a.get("href", "").strip()
                if not href:
                    continue
                att = CommentAttachment(
                    url=href,
                    filename=self._attachment_filename(href, a),
                    kind=self._attachment_kind(href),
                )
                comment.attachments.append(att)
                detail.attachments.append(att)
            # Также подхватим ссылки прямо в тексте комментария (напр. Google Drive).
            for a in ev.select(".comment-text a[href]"):
                href = a.get("href", "").strip()
                if href and not any(x.url == href for x in comment.attachments):
                    att = CommentAttachment(
                        url=href,
                        filename=self._attachment_filename(href, a),
                        kind=self._attachment_kind(href),
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
                from urllib.parse import urlparse
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
