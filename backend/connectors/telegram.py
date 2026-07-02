"""Отправка уведомлений в Telegram через Bot API (без сторонних либ, на requests)."""

from __future__ import annotations

import html
import logging
from typing import Optional

import requests

log = logging.getLogger("adrobot.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, timeout: int = 30):
        self.token = token
        self.chat_id = chat_id
        self.timeout = timeout

    def _call(self, method: str, payload: dict) -> dict:
        url = API.format(token=self.token, method=method)
        r = requests.post(url, json=payload, timeout=self.timeout)
        data = r.json()
        if not data.get("ok"):
            log.error("Telegram API error (%s): %s", method, data)
            raise RuntimeError(f"Telegram error: {data}")
        return data["result"]

    def send_message(
        self,
        text: str,
        url_button: Optional[tuple[str, str]] = None,
        url_buttons: Optional[list[tuple[str, str]]] = None,
        callback_buttons: Optional[list[tuple[str, str]]] = None,
    ) -> dict:
        """text — HTML. url_button — (подпись, url). url_buttons — несколько
        кнопок-ссылок [(подпись, url), …]. callback_buttons — кнопки-действия
        [(подпись, callback_data), …] — обрабатывает TaskIntake.run_telegram_forever."""
        if len(text) > MAX_LEN:
            text = text[: MAX_LEN - 20] + "\n…(обрезано)"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        buttons = list(url_buttons or [])
        if url_button:
            buttons.insert(0, url_button)
        # Только кнопки с непустым url (Telegram отвергнет пустые).
        rows = [[{"text": t, "url": u}] for (t, u) in buttons if u]
        for (t, d) in (callback_buttons or []):
            if d:
                rows.append([{"text": t, "callback_data": d[:64]}])
        if rows:
            payload["reply_markup"] = {"inline_keyboard": rows}
        return self._call("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str = "",
                        show_alert: bool = False) -> None:
        """Ответ на нажатие callback-кнопки (тост/алерт у пользователя)."""
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:200]
        if show_alert:
            payload["show_alert"] = True
        self._call("answerCallbackQuery", payload)

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        """Регистрирует команды бота (меню «/» в клиенте Telegram)."""
        self._call("setMyCommands", {
            "commands": [{"command": c, "description": d} for c, d in commands],
        })

    def get_updates(self, offset: Optional[int] = None) -> list[dict]:
        payload = {"timeout": 25}
        if offset is not None:
            payload["offset"] = offset
        url = API.format(token=self.token, method="getUpdates")
        r = requests.get(url, params=payload, timeout=self.timeout + 25)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram error: {data}")
        return data["result"]


def esc(s: str) -> str:
    return html.escape(s or "")
