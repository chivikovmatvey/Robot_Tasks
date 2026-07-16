"""Хранилище прокси для скрапинга гео-защищённых лендов.

Прокси сохраняются в storage/proxies.json (gitignored, локально), чтобы не
вводить их каждый раз. Используются скрапером (Playwright proxy) для скачивания
лендов под нужным гео (резидентный/мобильный прокси страны).

Формат ввода гибкий:
  host:port
  host:port:user:pass
  user:pass@host:port
  http://user:pass@host:port  |  socks5://host:port
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("proxies")

BASE_DIR = Path(__file__).resolve().parents[1]
PROXIES_FILE = BASE_DIR / "storage" / "proxies.json"


def parse_proxy(raw: str) -> Optional[dict]:
    """Строку прокси → dict для Playwright {server, username?, password?}.
    None, если распарсить не удалось."""
    raw = (raw or "").strip()
    if not raw:
        return None
    scheme = "http"
    m = re.match(r"^(https?|socks5h?|socks4)://(.+)$", raw, re.I)
    if m:
        scheme = m.group(1).lower()
        rest = m.group(2)
    else:
        rest = raw

    user = pwd = None
    if "@" in rest:
        auth, hostport = rest.rsplit("@", 1)
        if ":" in auth:
            user, pwd = auth.split(":", 1)
        else:
            user = auth
        host, _, port = hostport.partition(":")
    else:
        parts = rest.split(":")
        if len(parts) == 4:          # host:port:user:pass
            host, port, user, pwd = parts
        elif len(parts) >= 2:        # host:port (+ игнор лишнего)
            host, port = parts[0], parts[1]
        else:
            return None

    host = (host or "").strip()
    port = (port or "").strip()
    if not host or not port.isdigit():
        return None
    proxy: dict = {"server": f"{scheme}://{host}:{port}"}
    if user:
        proxy["username"] = user
    if pwd is not None:
        proxy["password"] = pwd
    return proxy


def _safe_server(raw: str) -> str:
    """Сервер без логина/пароля — для показа в UI."""
    p = parse_proxy(raw)
    return p["server"] if p else raw


class ProxyStore:
    def __init__(self, path: Path = PROXIES_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                log.warning("Не прочитать %s", self.path)
        return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._items, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    # ── публичное ────────────────────────────────────────────────
    def list(self) -> list[dict]:
        """Список БЕЗ паролей — для UI."""
        return [{"id": it["id"], "label": it.get("label", ""),
                 "server": _safe_server(it.get("raw", "")),
                 "geo": it.get("geo", "")}
                for it in self._items]

    def _key(self, raw: str) -> str:
        """Ключ дедупликации: server + логин."""
        p = parse_proxy(raw) or {}
        return f"{p.get('server', raw)}|{p.get('username', '')}"

    def add(self, raw: str, label: str = "", geo: str = "") -> dict:
        if parse_proxy(raw) is None:
            raise ValueError("Не удалось распознать прокси")
        with self._lock:
            key = self._key(raw)
            for it in self._items:  # дубль — не плодим
                if self._key(it.get("raw", "")) == key:
                    return {"id": it["id"], "label": it.get("label", ""),
                            "server": _safe_server(it.get("raw", "")),
                            "geo": it.get("geo", ""), "duplicate": True}
            item = {"id": uuid.uuid4().hex[:8], "raw": raw.strip(),
                    "label": (label or "").strip(), "geo": (geo or "").strip().upper()}
            self._items.append(item)
            self._save()
        return {"id": item["id"], "label": item["label"],
                "server": _safe_server(item["raw"]), "geo": item["geo"]}

    def delete(self, proxy_id: str) -> None:
        with self._lock:
            self._items = [it for it in self._items if it["id"] != proxy_id]
            self._save()

    def get_raw(self, proxy_id: str) -> Optional[str]:
        """Raw-строка сохранённого прокси по id или label (без регистра)."""
        needle = (proxy_id or "").strip().lower()
        if not needle:
            return None
        for it in self._items:
            if it["id"] == proxy_id or it.get("label", "").strip().lower() == needle:
                return it.get("raw")
        return None

    def list_raw(self) -> list[tuple[str, str]]:
        """[(label|id, raw), ...] всех сохранённых прокси."""
        return [(it.get("label") or it["id"], it["raw"])
                for it in self._items if it.get("raw")]

    def get_parsed(self, proxy_id: str) -> Optional[dict]:
        """Playwright-proxy сохранённого прокси по id или label (без регистра)."""
        raw = self.get_raw(proxy_id)
        return parse_proxy(raw) if raw else None


_store: Optional[ProxyStore] = None


def get_store() -> ProxyStore:
    global _store
    if _store is None:
        _store = ProxyStore()
    return _store
