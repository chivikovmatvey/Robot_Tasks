"""Скачивание архивов с Google Drive и Яндекс Диска по публичным ссылкам.

Ссылки приходят в комментариях/описании задач AdRobot. Поддержка:
  - Google Drive:  drive.google.com/file/d/<id>/...  | open?id=<id> | uc?id=<id>
  - Яндекс Диск:   disk.yandex.ru/d/<id> | yadi.sk/d/<id> (публичные)

Авторизация не требуется (публичные ссылки). Хосты — фиксированный список.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import unquote, urlparse

import requests

log = logging.getLogger("cloud")

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_GDRIVE_HOSTS = {"drive.google.com", "drive.usercontent.google.com", "docs.google.com"}
_YANDEX_HOSTS = {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}

CLOUD_HOSTS = _GDRIVE_HOSTS | _YANDEX_HOSTS | {
    "cloud-api.yandex.net", "downloader.disk.yandex.ru"}

# Ссылки в тексте (комментарий/описание).
_GDRIVE_RE = re.compile(
    r"https?://(?:drive|docs)\.google\.com/[^\s\"'<>)]+", re.I)
_YANDEX_RE = re.compile(
    r"https?://(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/[^\s\"'<>)]+", re.I)


def cloud_kind(url: str) -> Optional[str]:
    host = (urlparse(url).hostname or "").lower()
    if host in _GDRIVE_HOSTS:
        return "gdrive"
    if host in _YANDEX_HOSTS:
        return "yandex"
    return None


def extract_cloud_links(text: str) -> list[dict]:
    """Все облачные ссылки из текста. → [{url, kind}] (уникальные)."""
    out: list[dict] = []
    seen: set[str] = set()
    for rx, kind in ((_GDRIVE_RE, "gdrive"), (_YANDEX_RE, "yandex")):
        for m in rx.finditer(text or ""):
            url = m.group(0).rstrip(".,);]")
            if url not in seen:
                seen.add(url)
                out.append({"url": url, "kind": kind})
    return out


def _filename_from_headers(resp: requests.Response, fallback: str) -> str:
    cd = resp.headers.get("content-disposition", "")
    # Предпочитаем filename*=UTF-8''<percent-encoded> — корректная кодировка.
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    m = re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        name = m.group(1).strip()
        # HTTP-заголовки requests читает как latin-1; имя могло быть UTF-8.
        try:
            name = name.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        return name
    return fallback


# ── Google Drive ─────────────────────────────────────────────────
def _gdrive_id(url: str) -> Optional[str]:
    for pat in (r"/file/d/([\w-]+)", r"[?&]id=([\w-]+)", r"/d/([\w-]+)"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def download_gdrive(url: str, timeout: int = 120) -> tuple[bytes, str]:
    file_id = _gdrive_id(url)
    if not file_id:
        raise ValueError("Не извлечь id файла Google Drive")
    sess = requests.Session()
    sess.headers.update({"User-Agent": USER_AGENT})
    dl = "https://drive.usercontent.google.com/download"
    r = sess.get(dl, params={"id": file_id, "export": "download", "confirm": "t"},
                 stream=True, timeout=timeout)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    # Большой файл → страница подтверждения с формой (uuid). Повторяем с её полями.
    if "text/html" in ctype:
        html = r.text
        params = dict(re.findall(r'name="(id|export|confirm|uuid)"\s+value="([^"]*)"', html))
        if params:
            r = sess.get(dl, params=params, stream=True, timeout=timeout)
            r.raise_for_status()
        if "text/html" in r.headers.get("content-type", ""):
            raise ValueError("Google Drive не отдал файл (нет доступа/приватный?)")
    return r.content, _filename_from_headers(r, f"{file_id}.zip")


# ── Яндекс Диск ──────────────────────────────────────────────────
def download_yandex(url: str, timeout: int = 120) -> tuple[bytes, str]:
    api = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
    meta = requests.get(api, params={"public_key": url}, timeout=timeout,
                        headers={"User-Agent": USER_AGENT})
    if not meta.ok:
        raise ValueError(f"Яндекс API {meta.status_code}: {meta.text[:160]}")
    href = meta.json().get("href")
    if not href:
        raise ValueError("Яндекс Диск не вернул ссылку на скачивание")
    r = requests.get(href, timeout=timeout, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    # Имя — из download-ответа или из meta ресурса.
    fallback = "yandex_download.zip"
    try:
        info = requests.get("https://cloud-api.yandex.net/v1/disk/public/resources",
                            params={"public_key": url}, timeout=30).json()
        fallback = info.get("name") or fallback
    except Exception:  # noqa: BLE001
        pass
    return r.content, _filename_from_headers(r, fallback)


def download_cloud(url: str, timeout: int = 120) -> tuple[bytes, str]:
    """Скачивает архив по облачной ссылке. → (bytes, filename)."""
    kind = cloud_kind(url)
    if kind == "gdrive":
        return download_gdrive(url, timeout)
    if kind == "yandex":
        return download_yandex(url, timeout)
    raise ValueError(f"Не облачная ссылка: {url}")
