"""Клиент к AITUNNEL (https://api.aitunnel.ru/v1) — единый OpenAI-совместимый API.

Используется для чата-агента по адаптации лендов (модель Kimi K2.7 Code по
умолчанию). Реализован на requests (без зависимости openai SDK) — нам нужен
только /chat/completions с tool-calling и проверка баланса.

Формат запросов/ответов — OpenAI Chat Completions:
  POST /v1/chat/completions  {model, messages, tools?, tool_choice?, ...}
  ответ: choices[0].message {content, tool_calls?}, usage {cost_rub, balance}
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger("aitunnel")


def _is_transient(e: Exception) -> bool:
    """Транзиентный сетевой сбой, который имеет смысл повторить.

    api.aitunnel.ru периодически рвёт TLS на середине рукопожатия
    (SSLEOFError / 'UNEXPECTED_EOF_WHILE_READING'), а также бывают обрывы
    соединения и таймауты. Это сеть, не наш баг — повторяем запрос.
    """
    if isinstance(e, (requests.exceptions.SSLError,
                      requests.exceptions.ConnectionError,
                      requests.exceptions.Timeout,
                      requests.exceptions.ChunkedEncodingError)):
        return True
    msg = str(e).lower()
    return any(s in msg for s in (
        "unexpected_eof", "eof occurred", "connection reset",
        "connection aborted", "max retries exceeded", "ssl",
    ))

DEFAULT_BASE_URL = "https://api.aitunnel.ru/v1"
DEFAULT_MODEL = "qwen3.7-plus"

# Кол-во ретраев при транзиентных сетевых сбоях (SSL EOF, обрыв соединения).
# api.aitunnel.ru периодически рвёт TLS (SSLEOFError) — это сеть, не код;
# раньше помогал только перезапуск проекта. Теперь повторяем запрос сами.
NET_RETRIES = 3
RETRY_BACKOFF = 1.5  # секунды между попытками (растёт линейно)

# Модели для выбора в чате (id → подпись для UI). Все поддерживают tool-calling.
# Можно переопределить через AITUNNEL_MODELS="id1:Подпись1,id2:Подпись2".
# Цены за 1M токенов (вход/выход, AITUNNEL, июнь 2026):
#   qwen3.7-plus     — 76.8₽/307₽, мультимодальная (vision), баланс — ДЕФОЛТ
#   deepseek-v4-pro  — 83.5₽/167₽, дешёвый вывод, ризонер — для простых задач
#   kimi-k2.7-code   — флагман для кода (дороже всех)
DEFAULT_MODELS = [
    {"id": "qwen3.7-plus", "label": "Qwen3.7 Plus — vision (77/307₽)"},
    {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro — дёшево (83/167₽)"},
    {"id": "kimi-k2.7-code", "label": "Kimi K2.7 Code — сильная (дорого)"},
]


# ── локальная модель (Ollama / vLLM / llama.cpp — любой OpenAI-совм. сервер) ──
# Опциональна: если сервер отвечает — модель появляется в списке и становится
# дефолтом чата (бесплатно/приватно). Не отвечает — работаем только через AITUNNEL.
LOCAL_PREFIX = "local:"
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"  # Ollama

_local_cache: dict = {"ts": 0.0, "info": None}
_LOCAL_TTL = 30.0  # сек — не дёргать /models на каждый запрос статуса


def local_base_url() -> str:
    return (os.getenv("LOCAL_LLM_BASE_URL", "").strip()
            or DEFAULT_LOCAL_BASE_URL).rstrip("/")


def local_llm_info(force: bool = False) -> Optional[dict]:
    """Проверяет локальный OpenAI-совместимый сервер. → {base_url, model} | None.

    Модель: LOCAL_LLM_MODEL из .env, иначе первая из GET /models.
    Результат кэшируется на 30с (эндпоинт статуса зовётся часто)."""
    now = time.time()
    if not force and now - _local_cache["ts"] < _LOCAL_TTL:
        return _local_cache["info"]
    info = None
    base = local_base_url()
    try:
        r = requests.get(f"{base}/models", timeout=2)
        if r.ok:
            ids = [m.get("id") for m in (r.json().get("data") or []) if m.get("id")]
            model = os.getenv("LOCAL_LLM_MODEL", "").strip() or (ids[0] if ids else "")
            if model:
                info = {"base_url": base, "model": model}
    except Exception:  # noqa: BLE001 — сервер не поднят, это норма
        info = None
    _local_cache.update(ts=now, info=info)
    return info


def available_models() -> list[dict]:
    """Список моделей для выбора в UI (из AITUNNEL_MODELS или дефолт).

    Если доступен локальный сервер — его модель добавляется ПЕРВОЙ
    (id с префиксом 'local:', в чате она станет дефолтом)."""
    raw = os.getenv("AITUNNEL_MODELS", "").strip()
    if not raw:
        out = list(DEFAULT_MODELS)
    else:
        out = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            mid, _, label = part.partition(":")
            mid = mid.strip()
            if mid:
                out.append({"id": mid, "label": (label.strip() or mid)})
        out = out or list(DEFAULT_MODELS)
    local = local_llm_info()
    if local:
        out.insert(0, {"id": LOCAL_PREFIX + local["model"],
                       "label": f"Локальная — {local['model']} (бесплатно)"})
    return out


class AITunnelError(RuntimeError):
    pass


class AITunnelAuthError(AITunnelError):
    pass


class AITunnelClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = 180,
    ):
        if not api_key:
            raise AITunnelAuthError("Не задан AITUNNEL_API_KEY")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })

    def _route(self, model: Optional[str]) -> tuple[str, str]:
        """(модель, base_url) для запроса: id с префиксом 'local:' идёт на
        локальный сервер (Ollama/vLLM), остальное — на AITUNNEL."""
        mdl = (model or self.model or "").strip()
        if mdl.startswith(LOCAL_PREFIX):
            return mdl[len(LOCAL_PREFIX):], local_base_url()
        return mdl, self.base_url

    def _timeout_for(self, api_base: str) -> int:
        """Локальная модель на слабом GPU долго прожёвывает длинный промпт
        (первый запрос ещё и грузит веса в VRAM) — таймаут щедрее облачного."""
        if api_base == local_base_url():
            return int(os.getenv("LOCAL_LLM_TIMEOUT", "600") or "600")
        return self.timeout

    @staticmethod
    def _tune_local(payload: dict, api_base: str) -> None:
        """Локальные ризонеры (qwen3.5 в Ollama): без reasoning_effort весь
        лимит токенов уходит в размышления и content пустой. Дефолт 'none'
        (скорость важнее), переопределяется LOCAL_LLM_REASONING."""
        if api_base != local_base_url():
            return
        effort = os.getenv("LOCAL_LLM_REASONING", "none").strip() or "none"
        if effort != "off":  # LOCAL_LLM_REASONING=off — не слать параметр вовсе
            payload["reasoning_effort"] = effort

    # ── chat completions ─────────────────────────────────────────
    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        response_format: Optional[dict] = None,
    ) -> dict:
        """Один вызов /chat/completions. Возвращает (message, usage).

        message — dict ассистента {role, content, tool_calls?} как в OpenAI.
        response_format — напр. {"type": "json_object"} для строгого JSON.
        """
        mdl, api_base = self._route(model)
        payload: dict = {
            "model": mdl,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = response_format
        self._tune_local(payload, api_base)

        url = f"{api_base}/chat/completions"
        r = None
        last_err: Optional[Exception] = None
        for attempt in range(NET_RETRIES):
            try:
                r = self.session.post(url, json=payload,
                                      timeout=self._timeout_for(api_base))
                break
            except requests.RequestException as e:
                last_err = e
                if attempt < NET_RETRIES - 1 and _is_transient(e):
                    log.warning("AITUNNEL транзиентный сбой (попытка %d/%d): %s",
                                attempt + 1, NET_RETRIES, e)
                    time.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                raise AITunnelError(f"Сеть AITUNNEL: {e}") from e
        if r is None:  # на всякий случай — все попытки исчерпаны
            raise AITunnelError(f"Сеть AITUNNEL: {last_err}")

        if r.status_code == 401:
            raise AITunnelAuthError("Неверный AITUNNEL_API_KEY (401)")
        try:
            data = r.json()
        except ValueError:
            raise AITunnelError(f"AITUNNEL вернул не-JSON ({r.status_code}): {r.text[:200]}")

        if r.status_code >= 400 or "error" in data:
            err = data.get("error") if isinstance(data, dict) else None
            msg = (err or {}).get("message") if isinstance(err, dict) else r.text[:200]
            raise AITunnelError(f"AITUNNEL ошибка {r.status_code}: {msg}")

        choices = data.get("choices") or []
        if not choices:
            raise AITunnelError("AITUNNEL: пустой choices")
        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}
        return {"message": message, "usage": usage,
                "finish_reason": choices[0].get("finish_reason")}

    def chat_stream(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ):
        """Стриминговый /chat/completions (SSE). Yield-ит сырые chunk-словари
        (OpenAI-формат: choices[0].delta с content/tool_calls)."""
        import json
        mdl, api_base = self._route(model)
        payload: dict = {
            "model": mdl,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        self._tune_local(payload, api_base)

        url = f"{api_base}/chat/completions"
        r = None
        last_err: Optional[Exception] = None
        for attempt in range(NET_RETRIES):
            try:
                r = self.session.post(url, json=payload, stream=True,
                                      timeout=self._timeout_for(api_base))
                break
            except requests.RequestException as e:
                last_err = e
                if attempt < NET_RETRIES - 1 and _is_transient(e):
                    log.warning("AITUNNEL(stream) транзиентный сбой (попытка %d/%d): %s",
                                attempt + 1, NET_RETRIES, e)
                    time.sleep(RETRY_BACKOFF * (attempt + 1))
                    continue
                raise AITunnelError(f"Сеть AITUNNEL: {e}") from e
        if r is None:
            raise AITunnelError(f"Сеть AITUNNEL: {last_err}")

        if r.status_code == 401:
            raise AITunnelAuthError("Неверный AITUNNEL_API_KEY (401)")
        if r.status_code >= 400:
            raise AITunnelError(f"AITUNNEL ошибка {r.status_code}: {r.text[:200]}")

        # ВАЖНО: буферизуем СЫРЫЕ БАЙТЫ и сами режем SSE по '\n', декодируя
        # целые строки как UTF-8. Нельзя полагаться на requests decode_unicode:
        # для text/event-stream без charset он берёт ISO-8859-1 и ломает
        # кириллицу (mojibake). Разбивка по '\n' до decode — multibyte-safe
        # (символ не пересекает границу строки).
        buf = b""
        for chunk in r.iter_content(chunk_size=1024):
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line_b, buf = buf.split(b"\n", 1)
                line = line_b.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if line == "[DONE]":
                    return
                try:
                    yield json.loads(line)
                except ValueError:
                    continue

    # ── редактирование изображений (gpt-image-2) ─────────────────
    def edit_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        model: str = "gpt-image-2",
        size: str = "1024x1024",
        quality: str = "low",
        filename: str = "image.png",
        mime: str = "image/png",
        extra_images: Optional[list[tuple[bytes, str, str]]] = None,
        timeout: int = 240,
    ) -> bytes:
        """Редактирует изображение по промпту (/v1/images/edits). → PNG-байты.

        multipart-запрос (НЕ через self.session — там Content-Type json).
        mime — обязателен корректный (image/png|jpeg|webp), иначе API 400.
        extra_images — доп. референсы [(bytes, filename, mime), …]: тогда все
        изображения передаются как image[] (gpt-image поддерживает несколько
        входных картинок, напр. «возьми продукт со второго фото»).
        """
        import base64
        url = f"{self.base_url}/images/edits"
        all_imgs = [(image_bytes, filename, mime)] + list(extra_images or [])
        if len(all_imgs) == 1:
            files = [("image", (filename, image_bytes, mime))]
        else:
            # несколько входных картинок → поле image[] (как у OpenAI gpt-image)
            files = [("image[]", (fn, b, mt)) for (b, fn, mt) in all_imgs]
        data = {"model": model, "prompt": prompt, "size": size,
                "n": "1", "quality": quality}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            r = requests.post(url, headers=headers, files=files, data=data, timeout=timeout)
        except requests.RequestException as e:
            raise AITunnelError(f"Сеть AITUNNEL (image edit): {e}") from e
        if r.status_code == 401:
            raise AITunnelAuthError("Неверный AITUNNEL_API_KEY (401)")
        if not r.ok:
            raise AITunnelError(f"AITUNNEL image edit {r.status_code}: {r.text[:300]}")
        try:
            d = r.json()
        except ValueError:
            raise AITunnelError(f"image edit вернул не-JSON: {r.text[:200]}")
        item = (d.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            img = requests.get(item["url"], timeout=timeout)
            img.raise_for_status()
            return img.content
        raise AITunnelError(f"Нет изображения в ответе: {str(d)[:200]}")

    # ── баланс / модели (диагностика) ────────────────────────────
    def balance(self) -> Optional[float]:
        try:
            r = self.session.get(f"{self.base_url}/aitunnel/balance", timeout=30)
            if r.ok:
                return r.json().get("balance")
        except Exception:  # noqa: BLE001
            pass
        return None

    def ping(self) -> bool:
        """Лёгкая проверка ключа: запрос баланса (не тратит токены)."""
        try:
            r = self.session.get(f"{self.base_url}/aitunnel/balance", timeout=30)
            return r.ok
        except Exception:  # noqa: BLE001
            return False


def client_from_env() -> Optional[AITunnelClient]:
    """Создаёт клиент из .env. None, если нет ни ключа AITUNNEL, ни локальной
    модели. Без ключа, но с локальным сервером — работаем только локально."""
    key = os.getenv("AITUNNEL_API_KEY", "").strip()
    if not key:
        local = local_llm_info()
        if local is None:
            return None
        return AITunnelClient(
            api_key="local",  # локальным серверам ключ не нужен
            base_url=os.getenv("AITUNNEL_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
            model=LOCAL_PREFIX + local["model"],
        )
    return AITunnelClient(
        api_key=key,
        base_url=os.getenv("AITUNNEL_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL,
        model=os.getenv("AITUNNEL_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    )
