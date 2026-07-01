"""Импорт прокси из Dolphin Anty (Remote API dolphin-anty-api.com).

Local API (localhost:3001) требует отдельный session-токен; Remote API работает
с обычным API-токеном из кабинета Dolphin (Settings → API) и отдаёт библиотеку
прокси аккаунта с ПОЛНЫМИ кредами (host/port/login/password) — их и импортируем
в наше хранилище прокси, чтобы использовать в скрапере.

Токен — в .env: DOLPHIN_API_TOKEN.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

import requests

log = logging.getLogger("dolphin")

REMOTE_API = "https://dolphin-anty-api.com"


def _token() -> str:
    return os.getenv("DOLPHIN_API_TOKEN", "").strip()


def _geo_from_name(name: str) -> str:
    """Гео-код из имени прокси Dolphin (напр. 'Nsproxy_NI' → 'NI')."""
    m = re.search(r"[_\- ]([A-Za-z]{2})$", (name or "").strip())
    return m.group(1).upper() if m else ""


def _norm_proxy(p: dict, profile_name: str = "") -> Optional[dict]:
    host = p.get("host"); port = p.get("port")
    if not host or not port:
        return None
    name = p.get("name") or profile_name or ""
    country = ((p.get("lastCheck") or {}).get("country") or "")
    return {
        "name": name,
        "type": (p.get("type") or "http").lower(),
        "host": host,
        "port": str(port),
        "login": p.get("login") or "",
        "password": p.get("password") or "",
        "geo": _geo_from_name(name) or country.upper(),
    }


def list_proxies(token: Optional[str] = None, limit: int = 300) -> list[dict]:
    """Прокси из ПРОФИЛЕЙ Dolphin (прокси хранятся в профилях, не в библиотеке).

    Список профилей даёт proxy.id, но без port/пароля → берём детальный
    `GET /browser_profiles/{id}` (там полные креды). Дедуп по proxy.id, чтобы
    запрашивать детальный только по одному профилю на каждый уникальный прокси.
    → [{name, type, host, port, login, password, geo}].
    """
    tok = (token or _token()).strip()
    if not tok:
        raise ValueError("Не задан DOLPHIN_API_TOKEN в .env")
    h = {"Authorization": f"Bearer {tok}"}

    r = requests.get(f"{REMOTE_API}/browser_profiles", params={"limit": limit},
                     headers=h, timeout=30)
    if r.status_code == 401:
        raise ValueError("Dolphin API: неверный токен (401)")
    r.raise_for_status()
    profiles = (r.json() or {}).get("data") or []

    # Один профиль-представитель на каждый уникальный proxy.id (минимум запросов).
    rep: dict = {}
    for prof in profiles:
        px = prof.get("proxy") or {}
        pxid = px.get("id")
        if pxid and pxid not in rep:
            rep[pxid] = (prof.get("id"), prof.get("name", ""))

    out: list[dict] = []
    for pxid, (prof_id, prof_name) in rep.items():
        try:
            d = requests.get(f"{REMOTE_API}/browser_profiles/{prof_id}",
                             headers=h, timeout=30).json()
            proxy = (d.get("data") or d).get("proxy") or {}
            np = _norm_proxy(proxy, prof_name)
            if np:
                out.append(np)
        except Exception as e:  # noqa: BLE001
            log.warning("Не получить прокси профиля %s: %s", prof_id, e)
    return out


def proxy_to_raw(p: dict) -> str:
    """Прокси Dolphin → строка для нашего ProxyStore/parse_proxy."""
    scheme = p.get("type") or "http"
    if scheme not in ("http", "https", "socks5", "socks5h", "socks4"):
        scheme = "http"
    auth = ""
    if p.get("login"):
        auth = p["login"] + (f":{p['password']}" if p.get("password") else "") + "@"
    return f"{scheme}://{auth}{p['host']}:{p['port']}"
