"""Чат-агент по адаптации лендов (через AITUNNEL, модель Kimi K2.7 Code).

Привязан к конкретному ленду сессии. Умеет: смотреть контекст (scan/медиа/
параметры), подтягивать фото оффера в замены, запускать адаптацию с параметрами.
Работает по OpenAI tool-calling: модель предлагает вызов инструмента → мы
выполняем его над сессией → возвращаем результат → модель формулирует ответ.

История диалога хранится в LanderState.chat (формат OpenAI messages + поле ts).
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from connectors.aitunnel import AITunnelClient, AITunnelError

log = logging.getLogger("agent")

MAX_STEPS = 10  # предел итераций агентского цикла (защита от зацикливания)

SYSTEM_PROMPT = """\
Ты — ассистент по адаптации рекламных лендингов (Keitaro) в системе WorkSistem.
Ты работаешь с ОДНИМ конкретным лендом внутри сессии и помогаешь его адаптировать
под целевой оффер: сменить продукт, цены, гео, заменить фото/видео продукта.

Правила адаптации (важно):
- СТАРАЯ цена на ленде = РОВНО 2× от новой цены (правило техотдела). Всегда.
- Продукт-донора заменяем на новый продукт из целевого оффера.
- Гео берётся из целевого оффера; валюта — из гео.
- Плейсхолдеры/макросы Keitaro ({sub_id}, {clickid} и т.п.) НЕ трогать.
- Имена/города/валюту НЕ переводить произвольно — это отдельный шаг.

Как действовать:
- Сначала при необходимости вызови get_lander_context, чтобы увидеть scan,
  медиа, черновик параметров и доступные замены.
- Чтобы подтянуть официальное фото продукта со страницы оффера — import_offer_photos.
- Чтобы выполнить адаптацию — adapt_lander с параметрами. В image_map ключ это
  имя файла НА ленде, значение — имя файла-замены (из списка replacements/assets).
  Пустой image_map = медиа не меняем (остаётся оригинал).
- «Поменяй X на Y» (одна или НЕСКОЛЬКО пар текстов) → СРАЗУ вызывай
  replace_texts со ВСЕМИ парами из сообщения ОДНИМ вызовом. НЕ вызывай перед
  этим get_lander_context, list_files или read_file — они не нужны: find берётся
  ДОСЛОВНО из сообщения пользователя (точная копия, ничего не перефразируй и не
  сокращай). Инструмент сам найдёт файлы и вернёт по каждой паре число замен;
  пары с replaced=0 перечисли пользователю как «не найдено на ленде».
- Для остальных правок кода адаптированного ленда: list_files → read_file →
  edit_file (точечная замена find→replace). Перед edit_file ВСЕГДА читай файл,
  чтобы строка find точно совпадала. Правки идут в выходной архив, результат
  сразу виден в превью. Пример запроса: «поменяй form_sale на grid».
  ВАЖНО: по умолчанию все правки разметки/текста/скриптов делай в файле
  `index.php` (это главная страница ленда). Другой файл трогай ТОЛЬКО если
  пользователь явно его назвал или нужная строка точно в другом файле.
- Для смены языка ленда — translate_lander. Сначала preview=true (покажи дифф
  пользователю), применяй (preview=false) только по его подтверждению. После
  применения напомни вычитать дифф перед заливкой.
- Для правки картинки (перевести текст на фото, убрать надпись и т.п.) —
  edit_image с путём картинки (из get_lander_context → media) и промптом.
  Это платно — формулируй промпт точно, не дёргай зря. Промо-фото с текстом
  НЕ превращай в «голую банку» — сохраняй композицию, меняй только нужное.
- После действий кратко сообщи результат и что проверить в превью.

Формат ответа (СТРОГО):
- ВСЕГДА отвечай ТОЛЬКО по-русски (даже если контент ленда на другом языке).
- По-русски, предельно кратко. Экономь токены: никакой воды, вступлений
  («Конечно!», «Давайте…»), извинений, повторов вопроса пользователя.
- Структурируй markdown: **жирным** — ключевое (статусы, итоги, имена файлов),
  списками `-` — перечисления, `code` — имена файлов/значения/код.
- Не пересказывай результаты инструментов целиком — только вывод и следующий шаг.
- Закончил действие — дай короткий **итог** (1–3 пункта) и что проверить.
- Не выдумывай имена файлов — бери их из get_lander_context.
- Не хватает данных — задай ОДИН конкретный вопрос.
"""


# ── описания инструментов (OpenAI tool schema) ──────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_lander_context",
            "description": "Контекст ленда: scan-сводка (продукт-кандидаты, гео, "
                           "цены донора), список медиа (фото/видео), черновик "
                           "параметров адаптации, доступные замены (assets + задачи).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_offer_photos",
            "description": "Скачать официальные фото продукта со страницы целевого "
                           "оффера в изолированные замены этого ленда. Возвращает "
                           "имена добавленных файлов (их можно использовать в image_map).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Список текстовых файлов АДАПТИРОВАННОГО ленда (html/php/css/js). "
                           "Доступно только после adapt_lander.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать содержимое файла адаптированного ленда.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Путь файла в архиве, напр. index.php"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Точечная правка файла адаптированного ленда: заменить ВСЕ "
                           "вхождения строки find на replace. Перед правкой прочитай файл "
                           "(read_file), чтобы find точно совпадал. Пример: form_sale → grid.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "find": {"type": "string", "description": "Точная искомая строка"},
                    "replace": {"type": "string", "description": "Чем заменить"},
                },
                "required": ["path", "find", "replace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_texts",
            "description": "Массовая замена текстов на ленде: список пар "
                           "{find, replace}. Ищет по ВСЕМ файлам сам — не требует "
                           "read_file/list_files. find — ДОСЛОВНАЯ строка из запроса "
                           "пользователя. Возвращает по каждой паре: сколько "
                           "вхождений заменено и в каких файлах (0 = не найдено).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pairs": {
                        "type": "array",
                        "description": "Все пары замен из сообщения пользователя",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {"type": "string", "description": "Точный исходный текст"},
                                "replace": {"type": "string", "description": "Новый текст"},
                            },
                            "required": ["find", "replace"],
                        },
                    },
                },
                "required": ["pairs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": "Нейро-правка картинки адаптированного ленда (GPT Image 2): "
                           "по промпту меняет картинку (напр. «переведи текст на "
                           "картинке на польский», «убери надпись»), результат "
                           "подгоняется под размер оригинала и заменяет файл в ленде. "
                           "path бери из get_lander_context (media). Платное действие.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь картинки в ленде, напр. product2.png"},
                    "prompt": {"type": "string", "description": "Что изменить на картинке"},
                    "quality": {"type": "string", "enum": ["low", "medium", "high"], "description": "по умолчанию low"},
                },
                "required": ["path", "prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "translate_lander",
            "description": "Перевести видимый текст адаптированного ленда на целевой "
                           "язык (deepseek). preview=true — показать дифф без записи; "
                           "preview=false — применить в архив. Макросы/цены/бренды не "
                           "трогаются. После применения нужна вычитка диффа человеком.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_lang": {"type": "string", "description": "Код языка, напр. 'pt' (иначе по гео)"},
                    "preview": {"type": "boolean", "description": "true — только дифф, не применять (по умолчанию true)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "adapt_lander",
            "description": "Запустить адаптацию ленда с заданными параметрами. "
                           "Создаёт новый адаптированный архив и превью.",
            "parameters": {
                "type": "object",
                "properties": {
                    "geo_id": {"type": "string", "description": "Код гео, напр. MX"},
                    "product_old": {"type": "string", "description": "Продукт-донор (что искать)"},
                    "product_new": {"type": "string", "description": "Новый продукт"},
                    "price_new": {"type": "string", "description": "Новая цена, напр. '299 MXN'"},
                    "price_old": {"type": "string", "description": "Старая цена = 2× новой"},
                    "exclude_word": {"type": "string", "description": "Слово-исключение вертикали (необяз.)"},
                    "custom_replacements": {"type": "string", "description": "Доп. замены текста, по строке 'было=>стало' (необяз.)"},
                    "image_map": {
                        "type": "object",
                        "description": "Замена медиа: {имя_на_ленде: имя_замены}. Пусто = не менять.",
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["geo_id", "product_new", "price_new", "price_old"],
            },
        },
    },
]


_REPLACE_HEAD_RE = re.compile(r"^\s*(поменяй|замени|измени)\b[:\s]*", re.IGNORECASE)


def parse_replace_pairs(message: str) -> Optional[list[tuple[str, str]]]:
    """Детерминированный разбор сообщения «поменяй:\nX\nна\nY\n\nX2\nна\nY2…».

    Такие запросы выполняются БЕЗ нейросети: слабая локальная модель, чтобы
    вызвать инструмент, должна повторить весь текст в аргументах — на больших
    сообщениях это медленно и ненадёжно (обрезка токенами). Возвращает пары,
    если ВСЁ сообщение — это список замен (каждый блок содержит строку «на»),
    иначе None (обычный путь через LLM).
    """
    m = _REPLACE_HEAD_RE.match(message or "")
    if not m:
        return None
    body = message[m.end():].strip()
    if not body:
        return None
    pairs: list[tuple[str, str]] = []
    for block in re.split(r"\n\s*\n", body):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        # разделитель — строка, состоящая ровно из «на» (или "->"/"→")
        seps = [i for i, ln in enumerate(lines)
                if ln.strip().lower() in ("на", "->", "→")]
        if len(seps) != 1 or seps[0] == 0 or seps[0] == len(lines) - 1:
            return None  # блок не по формату — пусть разбирается модель
        find = "\n".join(lines[:seps[0]]).strip()
        repl = "\n".join(lines[seps[0] + 1:]).strip()
        if not find:
            return None
        pairs.append((find, repl))
    return pairs or None


def format_replace_report(report: list[dict]) -> str:
    """Человекочитаемый итог replace_texts для ответа в чате."""
    ok = [r for r in report if r.get("replaced")]
    miss = [r for r in report if not r.get("replaced")]
    lines = [f"**Заменено {len(ok)} из {len(report)} пар** (без нейросети, точным поиском)."]
    if ok:
        files = sorted({f for r in ok for f in r.get("files", [])})
        lines.append(f"- вхождений: {sum(r['replaced'] for r in ok)}, файлы: "
                     + ", ".join(f"`{f}`" for f in files))
    if miss:
        lines.append("**Не найдено на ленде** (текст отличается — проверь пробелы/теги):")
        for r in miss:
            lines.append(f"- `{r['find']}…`")
    lines.append("Проверь результат в превью.")
    return "\n".join(lines)


def _now() -> float:
    return time.time()


def _sanitize_for_api(messages: list[dict]) -> list[dict]:
    """Убирает нестандартные ключи (ts) перед отправкой в API."""
    out = []
    for m in messages:
        clean = {k: v for k, v in m.items() if k != "ts"}
        out.append(clean)
    return out


class LanderAgent:
    def __init__(self, client: AITunnelClient, manager, intake=None):
        self.client = client
        self.mgr = manager
        self.intake = intake  # для import_offer_photos (AdRobot client)

    # ── инструменты ──────────────────────────────────────────────
    def _tool_get_context(self, sid: str, lid: str) -> dict:
        s = self.mgr.get(sid)
        ls = s.landers.get(lid) if s else None
        if ls is None:
            return {"error": "ленд не найден"}
        scan = ls.scan or {}
        suggested = {}
        try:
            suggested = self.mgr.suggest_adapt_params(sid, lid)
            suggested.pop("_hints", None)
        except Exception as e:  # noqa: BLE001
            suggested = {"error": str(e)}
        media = self.mgr.list_lander_media(sid, lid)
        replacements = self.mgr.list_replacements(sid, lid)
        from utils.runners import STORAGE
        assets_dir = STORAGE / "assets"
        assets = sorted(
            f.name for f in assets_dir.iterdir()
            if f.is_file() and not f.name.startswith(".")
        ) if assets_dir.exists() else []
        return {
            "offer": s.task_offer(ls.task_uid),
            "donor_offer_name": ls.offer_name,
            "status": ls.status,
            "scan": {
                "product": scan.get("product"),
                "product_candidates": scan.get("product_candidates", []),
                "price_new_str": scan.get("price_new_str"),
                "price_old_str": scan.get("price_old_str"),
                "detected_country": scan.get("detected_country", {}),
                "prod_images": scan.get("prod_images", []),
            },
            "media": media,
            "suggested_params": suggested,
            "task_replacements": [r["name"] for r in replacements],
            "global_assets": assets,
            "last_adapt_params": ls.adapt_params,
        }

    def _tool_import_offer_photos(self, sid: str, lid: str) -> dict:
        if self.intake is None:
            return {"error": "AdRobot не настроен — нельзя подтянуть фото оффера"}
        s = self.mgr.get(sid)
        ls = s.landers.get(lid) if s else None
        if ls is None:
            return {"error": "ленд не найден"}
        offer = s.task_offer(ls.task_uid)
        if not offer:
            return {"error": "у ленда нет целевого оффера"}
        try:
            urls = self.intake.client.get_offer_product_images(offer)
        except Exception as e:  # noqa: BLE001
            return {"error": f"страница оффера недоступна: {e}"}
        import os
        import re
        base = re.sub(r"[^\w.-]+", "_", offer.strip()) or "offer"
        added = []
        existing = {r["name"] for r in self.mgr.list_replacements(sid, lid)}
        for i, url in enumerate(urls):
            ext = os.path.splitext(url.split("?")[0])[1] or ".png"
            name = f"{base}{ext}" if i == 0 else f"{base}_{i + 1}{ext}"
            if name in existing:
                added.append(name)
                continue
            try:
                data, _fn, _ct = self.intake.client.download_attachment(url)
                saved = self.mgr.save_replacement(sid, lid, data, name)
                added.append(saved)
            except Exception:  # noqa: BLE001
                continue
        return {"imported": added, "count": len(added)}

    def _tool_adapt(self, sid: str, lid: str, args: dict) -> dict:
        params = {
            "geo_id": args.get("geo_id", ""),
            "product_old": args.get("product_old", ""),
            "product_new": args.get("product_new", ""),
            "price_new": args.get("price_new", ""),
            "price_old": args.get("price_old", ""),
            "exclude_word": args.get("exclude_word", ""),
            "custom_replacements": args.get("custom_replacements", ""),
            "image_map": args.get("image_map", {}) or {},
        }
        # Разбор цены на num/cur (как делает фронт) — adapt ожидает оба формата.
        from services.session import split_price, double_num
        n_num, n_cur = split_price(params["price_new"])
        o_num, o_cur = split_price(params["price_old"])
        if not params["price_old"] and n_num:
            o_num, o_cur = double_num(n_num), n_cur
            params["price_old"] = f"{o_num} {o_cur}".strip()
        params.update({
            "price_new_num": n_num, "price_new_cur": n_cur,
            "price_old_num": o_num, "price_old_cur": o_cur,
        })
        try:
            res = self.mgr.adapt_lander(sid, lid, params)
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": str(e)}
        return {
            "success": res.get("success"),
            "status": res.get("status"),
            "output_name": res.get("output_name"),
            "preview_url": res.get("output_url"),
            "error": res.get("error"),
        }

    def _tool_list_files(self, sid: str, lid: str) -> dict:
        try:
            return {"files": self.mgr.list_output_files(sid, lid)}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def _tool_read_file(self, sid: str, lid: str, args: dict) -> dict:
        path = args.get("path", "")
        try:
            content = self.mgr.read_output_file(sid, lid, path)
        except KeyError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        # Ограничиваем объём, чтобы не раздувать контекст.
        if len(content) > 60000:
            return {"path": path, "truncated": True,
                    "content": content[:60000] + "\n…(обрезано)"}
        return {"path": path, "content": content}

    def _tool_edit_file(self, sid: str, lid: str, args: dict) -> dict:
        try:
            return self.mgr.edit_output_file(
                sid, lid, args.get("path", ""),
                args.get("find", ""), args.get("replace", ""))
        except KeyError as e:
            return {"error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def _tool_translate(self, sid: str, lid: str, args: dict) -> dict:
        from services.translate import translate_lander
        preview = args.get("preview", True)
        try:
            res = translate_lander(sid, lid, target_lang=args.get("target_lang"),
                                   execute=not preview)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}
        # Не раздуваем контекст: отдаём сводку + первые примеры диффа.
        diff = res.get("diff", [])
        return {
            "lang": res.get("lang"),
            "mode": res.get("mode"),
            "blocks_changed": len(diff),
            "applied_files": res.get("applied", 0),
            "sample": [{"original": d["original"][:80], "translated": d["translated"][:80]}
                       for d in diff[:8]],
        }

    def _tool_replace_texts(self, sid: str, lid: str, args: dict) -> dict:
        pairs_raw = args.get("pairs") or []
        pairs = [((p.get("find") or ""), (p.get("replace") or ""))
                 for p in pairs_raw if isinstance(p, dict)]
        if not pairs:
            return {"error": "пустой список pairs"}
        try:
            report = self.mgr.replace_texts(sid, lid, pairs)
        except ValueError as e:
            return {"error": str(e)}
        ok = sum(1 for r in report if r.get("replaced"))
        return {"pairs_total": len(report), "pairs_replaced": ok,
                "pairs_not_found": len(report) - ok, "report": report}

    def _tool_edit_image(self, sid: str, lid: str, args: dict) -> dict:
        from services.image_edit import edit_lander_media
        try:
            return edit_lander_media(sid, lid, args.get("path", ""),
                                     args.get("prompt", ""),
                                     quality=args.get("quality", "low"))
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    def _execute(self, sid: str, lid: str, name: str, args: dict) -> dict:
        if name == "get_lander_context":
            return self._tool_get_context(sid, lid)
        if name == "edit_image":
            return self._tool_edit_image(sid, lid, args)
        if name == "import_offer_photos":
            return self._tool_import_offer_photos(sid, lid)
        if name == "list_files":
            return self._tool_list_files(sid, lid)
        if name == "read_file":
            return self._tool_read_file(sid, lid, args)
        if name == "edit_file":
            return self._tool_edit_file(sid, lid, args)
        if name == "replace_texts":
            return self._tool_replace_texts(sid, lid, args)
        if name == "translate_lander":
            return self._tool_translate(sid, lid, args)
        if name == "adapt_lander":
            return self._tool_adapt(sid, lid, args)
        return {"error": f"неизвестный инструмент: {name}"}

    # ── агентский цикл ───────────────────────────────────────────
    def run(self, sid: str, lid: str, user_message: str,
            model: Optional[str] = None) -> list[dict]:
        """Гоняет цикл «модель → инструменты → модель». Сохраняет историю в
        LanderState.chat. Возвращает список НОВЫХ сообщений (для UI).

        model — выбранная модель (kimi/qwen/deepseek), иначе дефолт клиента."""
        s = self.mgr.get(sid)
        if s is None:
            raise KeyError(f"Сессия {sid} не найдена")
        ls = s.landers.get(lid)
        if ls is None:
            raise KeyError(f"Ленд {lid} не найден")

        history = list(ls.chat or [])
        new_messages: list[dict] = [{"role": "user", "content": user_message, "ts": _now()}]

        # «поменяй: X на Y (×N)» — детерминированный путь БЕЗ нейросети.
        pairs = parse_replace_pairs(user_message)
        if pairs:
            try:
                report = self.mgr.replace_texts(sid, lid, pairs)
                content = format_replace_report(report)
            except ValueError as e:
                content = f"Не получилось выполнить замены: {e}"
            new_messages.append({"role": "assistant", "content": content, "ts": _now()})
            ls.chat = history + new_messages
            self.mgr._save(s)
            return new_messages

        # Контекст: system + прошлая история + новое сообщение.
        convo = [{"role": "system", "content": SYSTEM_PROMPT}] + history + new_messages

        for _ in range(MAX_STEPS):
            # 8192: аргументы tool-вызова могут быть большими (replace_texts с
            # десятком длинных пар повторяет весь текст) — 4096 обрезал вызов.
            resp = self.client.chat(_sanitize_for_api(convo), tools=TOOLS,
                                    model=model, max_tokens=8192)
            msg = resp["message"]
            usage = resp.get("usage") or {}
            assistant_msg = {
                "role": "assistant",
                "content": msg.get("content"),
                "ts": _now(),
            }
            if msg.get("tool_calls"):
                assistant_msg["tool_calls"] = msg["tool_calls"]
            if usage.get("cost_rub") is not None:
                assistant_msg["cost_rub"] = usage.get("cost_rub")
            convo.append(assistant_msg)
            new_messages.append(assistant_msg)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break  # финальный ответ — цикл завершён

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    # Аргументы обрезаны лимитом токенов / битый JSON — скажем
                    # модели явно, чтобы она повторила вызов меньшими частями.
                    args = None
                log.info("agent tool %s args=%s", name, args)
                if args is None:
                    result = {"error": "аргументы вызова обрезаны или невалидный "
                                       "JSON — повтори вызов; если данных много, "
                                       "разбей на несколько вызовов"}
                else:
                    result = self._execute(sid, lid, name, args)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.get("id"),
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False),
                    "ts": _now(),
                }
                convo.append(tool_msg)
                new_messages.append(tool_msg)
        else:
            # Достигли лимита шагов без финального ответа.
            stop = {"role": "assistant",
                    "content": "Прервал цикл: слишком много шагов. Уточни задачу.",
                    "ts": _now()}
            new_messages.append(stop)

        # Сохраняем расширенную историю и перечитываем ленд (adapt мог обновить).
        s = self.mgr.get(sid)
        ls = s.landers.get(lid)
        ls.chat = history + new_messages
        self.mgr._save(s)
        return new_messages

    # ── стриминговый вариант (SSE-события для UI) ─────────────────
    def run_stream(self, sid: str, lid: str, user_message: str,
                   model: Optional[str] = None):
        """Генератор событий агентского цикла со стримингом текста.

        model — выбранная модель (иначе дефолт клиента).
        Типы событий: token / tool_call / tool_result / assistant_message /
        done / error. Текст финального ответа приходит по кускам (token).
        """
        from dataclasses import asdict
        s = self.mgr.get(sid)
        if s is None or s.landers.get(lid) is None:
            yield {"type": "error", "error": "ленд не найден"}
            return
        ls = s.landers[lid]

        history = list(ls.chat or [])
        new_messages: list[dict] = [{"role": "user", "content": user_message, "ts": _now()}]

        # «поменяй: X на Y (×N)» — детерминированный путь БЕЗ нейросети
        # (мгновенно и точно; слабой локальной модели такой объём не по зубам).
        pairs = parse_replace_pairs(user_message)
        if pairs:
            yield {"type": "tool_call", "name": "replace_texts"}
            try:
                report = self.mgr.replace_texts(sid, lid, pairs)
                yield {"type": "tool_result", "name": "replace_texts",
                       "content": json.dumps(
                           {"pairs_total": len(report),
                            "pairs_replaced": sum(1 for r in report if r.get("replaced"))},
                           ensure_ascii=False)}
                content = format_replace_report(report)
            except ValueError as e:
                content = f"Не получилось выполнить замены: {e}"
            final = {"role": "assistant", "content": content, "ts": _now()}
            new_messages.append(final)
            yield {"type": "assistant_message", "message": final}
            ls.chat = history + new_messages
            self.mgr._save(s)
            yield {"type": "done", "lander": asdict(s.landers[lid])}
            return

        convo = [{"role": "system", "content": SYSTEM_PROMPT}] + history + new_messages

        try:
            for _ in range(MAX_STEPS):
                acc_content = ""
                acc_tools: dict[int, dict] = {}
                for chunk in self.client.chat_stream(_sanitize_for_api(convo), tools=TOOLS,
                                                     model=model, max_tokens=8192):
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    if delta.get("content"):
                        acc_content += delta["content"]
                        yield {"type": "token", "text": delta["content"]}
                    for tcd in (delta.get("tool_calls") or []):
                        idx = tcd.get("index", 0)
                        slot = acc_tools.setdefault(idx, {"id": None, "name": "", "args": ""})
                        if tcd.get("id"):
                            slot["id"] = tcd["id"]
                        fn = tcd.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]

                tool_calls = None
                if acc_tools:
                    tool_calls = [
                        {"id": s["id"] or f"call_{i}", "type": "function",
                         "function": {"name": s["name"], "arguments": s["args"]}}
                        for i, s in sorted(acc_tools.items())
                    ]
                assistant_msg = {"role": "assistant", "content": acc_content or None, "ts": _now()}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                convo.append(assistant_msg)
                new_messages.append(assistant_msg)
                yield {"type": "assistant_message", "message": assistant_msg}

                if not tool_calls:
                    break

                for tc in tool_calls:
                    name = tc["function"]["name"]
                    yield {"type": "tool_call", "name": name}
                    try:
                        args = json.loads(tc["function"]["arguments"] or "{}")
                    except ValueError:
                        args = None
                    log.info("agent(stream) tool %s args=%s", name, args)
                    if args is None:
                        result = {"error": "аргументы вызова обрезаны или "
                                           "невалидный JSON — повтори вызов; если "
                                           "данных много, разбей на несколько вызовов"}
                    else:
                        result = self._execute(sid, lid, name, args)
                    content = json.dumps(result, ensure_ascii=False)
                    tool_msg = {"role": "tool", "tool_call_id": tc["id"],
                                "name": name, "content": content, "ts": _now()}
                    convo.append(tool_msg)
                    new_messages.append(tool_msg)
                    yield {"type": "tool_result", "name": name, "content": content}
            else:
                stop = {"role": "assistant",
                        "content": "Прервал цикл: слишком много шагов.", "ts": _now()}
                new_messages.append(stop)
                yield {"type": "assistant_message", "message": stop}
        except Exception as e:  # noqa: BLE001
            log.exception("Сбой стрим-агента")
            yield {"type": "error", "error": str(e)}

        # Сохраняем историю и отдаём финальное состояние ленда.
        s = self.mgr.get(sid)
        ls = s.landers.get(lid)
        if ls is not None:
            ls.chat = history + new_messages
            self.mgr._save(s)
            yield {"type": "done", "lander": asdict(ls)}


# ── фабрика ──────────────────────────────────────────────────────
def build_agent(manager, intake=None) -> Optional[LanderAgent]:
    """Создаёт агента, если задан AITUNNEL_API_KEY. Иначе None."""
    from connectors.aitunnel import client_from_env
    client = client_from_env()
    if client is None:
        return None
    return LanderAgent(client, manager, intake=intake)
