"""VSL-ленды: работа с config.php (парсинг/сериализация PHP-массива) и
рабочей копией эталонного ленда.

Весь VSL строится на конфиге: эталонный ленд (id 19201) скачивается из Keitaro,
копируется в рабочий output-архив, дальше все правки (конфиг, product.png,
ссылки на видео) идут в эту копию. Структуру конфига см. Документация/Часть 3.
"""

from __future__ import annotations

import logging
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("session")

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUTS = BASE_DIR / "storage" / "outputs"

# Эталонный VSL-ленд в Keitaro — основа всех новых VSL.
VSL_TEMPLATE_ID = "19201"

# Хост CDN для ссылок на видео, если в конфиге ссылка нераспознаваема/пустая.
DEFAULT_VIDEO_HOST = "https://cdn.fedllad.store"


# ── парсер PHP-массива ───────────────────────────────────────────
class PhpParseError(ValueError):
    pass


class _PhpParser:
    """Мини-парсер литерала PHP-массива из config.php.

    Поддерживает ровно то, что встречается в VSL-конфигах: вложенные [...] ,
    'key' => value, строки в одинарных/двойных кавычках, числа, true/false/null,
    комментарии // # /* */. Этого достаточно — конфиг генерируется, а не пишется
    руками в экзотическом стиле.
    """

    def __init__(self, text: str, pos: int = 0):
        self.s = text
        self.i = pos
        self.n = len(text)

    def _err(self, msg: str) -> PhpParseError:
        line = self.s.count("\n", 0, self.i) + 1
        return PhpParseError(f"config.php: {msg} (строка {line})")

    def skip_ws(self) -> None:
        while self.i < self.n:
            c = self.s[self.i]
            if c in " \t\r\n":
                self.i += 1
            elif self.s.startswith("//", self.i) or c == "#":
                nl = self.s.find("\n", self.i)
                self.i = self.n if nl == -1 else nl + 1
            elif self.s.startswith("/*", self.i):
                end = self.s.find("*/", self.i + 2)
                if end == -1:
                    raise self._err("незакрытый комментарий /*")
                self.i = end + 2
            else:
                return

    def parse_value(self) -> Any:
        self.skip_ws()
        if self.i >= self.n:
            raise self._err("неожиданный конец файла")
        c = self.s[self.i]
        if c == "[":
            self.i += 1
            return self.parse_array()
        if c in "'\"":
            return self.parse_string(c)
        # число (в т.ч. отрицательное / float)
        m = re.match(r"-?\d+\.\d+|-?\d+", self.s[self.i:])
        if m:
            raw = m.group(0)
            self.i += len(raw)
            return float(raw) if "." in raw else int(raw)
        # true / false / null (регистронезависимо)
        m = re.match(r"(true|false|null)\b", self.s[self.i:], re.IGNORECASE)
        if m:
            word = m.group(1).lower()
            self.i += len(word)
            return {"true": True, "false": False, "null": None}[word]
        raise self._err(f"неожиданный символ {c!r}")

    def parse_string(self, quote: str) -> str:
        # PHP: в '...' значимы только \' и \\; в "..." — стандартные эскейпы.
        self.i += 1
        out: list[str] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < self.n:
                nxt = self.s[self.i + 1]
                if quote == "'":
                    if nxt in ("'", "\\"):
                        out.append(nxt)
                        self.i += 2
                        continue
                    out.append(c)
                    self.i += 1
                    continue
                mapping = {"n": "\n", "t": "\t", "r": "\r",
                           '"': '"', "\\": "\\", "$": "$", "'": "'"}
                out.append(mapping.get(nxt, "\\" + nxt))
                self.i += 2
                continue
            if c == quote:
                self.i += 1
                return "".join(out)
            out.append(c)
            self.i += 1
        raise self._err("незакрытая строка")

    def parse_array(self) -> Any:
        """После '['. Возвращает dict (есть ключи) или list (последовательный)."""
        items: list[tuple[Optional[Any], Any]] = []
        while True:
            self.skip_ws()
            if self.i >= self.n:
                raise self._err("незакрытый массив")
            if self.s[self.i] == "]":
                self.i += 1
                break
            first = self.parse_value()
            self.skip_ws()
            if self.s.startswith("=>", self.i):
                self.i += 2
                val = self.parse_value()
                items.append((first, val))
            else:
                items.append((None, first))
            self.skip_ws()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
        if any(k is not None for k, _ in items):
            return {str(k): v for k, v in items}
        return [v for _, v in items]


def php_serialize(value: Any, indent: int = 1) -> str:
    """Значение → литерал PHP (одинарные кавычки, 4 пробела на уровень)."""
    pad = "    " * indent
    pad_close = "    " * (indent - 1)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value == int(value):
            return str(int(value))
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if isinstance(value, dict):
        if not value:
            return "[]"
        rows = [f"{pad}{php_serialize(str(k), indent)} => {php_serialize(v, indent + 1)},"
                for k, v in value.items()]
        return "[\n" + "\n".join(rows) + f"\n{pad_close}]"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        # Короткие списки скаляров — в одну строку (как в исходнике [20,40,60]).
        if all(isinstance(v, (int, float, bool)) or v is None for v in value) and len(value) <= 8:
            return "[" + ", ".join(php_serialize(v, indent) for v in value) + "]"
        rows = [f"{pad}{php_serialize(v, indent + 1)}," for v in value]
        return "[\n" + "\n".join(rows) + f"\n{pad_close}]"
    raise ValueError(f"Не сериализуемый тип для PHP: {type(value)}")


_CONFIG_RE = re.compile(r"\$config\s*=\s*\[")


def parse_config_php(text: str) -> dict:
    """Достаёт и парсит массив $config из текста config.php."""
    m = _CONFIG_RE.search(text)
    if not m:
        raise PhpParseError("В config.php не найден '$config = ['")
    p = _PhpParser(text, m.end())
    cfg = p.parse_array()
    if not isinstance(cfg, dict):
        raise PhpParseError("$config должен быть ассоциативным массивом")
    return cfg


def replace_config_php(text: str, config: dict) -> str:
    """Пересобирает текст config.php: новый $config, остальное (шапка/echo) как было."""
    m = _CONFIG_RE.search(text)
    if not m:
        raise PhpParseError("В config.php не найден '$config = ['")
    p = _PhpParser(text, m.end())
    p.parse_array()  # двигаем указатель до закрывающей ']'
    prefix = text[:m.end() - 1]  # всё до '[' включая '$config = '
    suffix = text[p.i:]          # всё после ']' (';', echo и т.д.)
    return prefix + php_serialize(config, 1) + suffix


# ── рабочая копия VSL-ленда ──────────────────────────────────────
def _mgr():
    from services.session import get_manager
    return get_manager()


def ensure_output(sid: str, lid: str) -> Path:
    """Гарантирует рабочий output-архив VSL-ленда (копия исходного).

    Все правки VSL идут в копию — исходный шаблон остаётся нетронутым,
    переустановка вернёт эталон."""
    from services.session import LanderStatus
    from utils.files import output_relative_url
    mgr = _mgr()
    s, ls = mgr._get_lander(sid, lid)
    if ls.output_name:
        p = OUTPUTS / ls.output_name
        if p.exists():
            return p
    if not ls.zip_path or not Path(ls.zip_path).exists():
        raise ValueError("Ленд ещё не скачан — нет исходного архива")
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    out = OUTPUTS / ("vsl_%s_%s.zip" % (sid, re.sub(r"[^\w.-]", "_", lid)))
    shutil.copy2(ls.zip_path, out)
    ls.output_name = out.name
    ls.output_url = output_relative_url(out)
    ls.status = LanderStatus.ADAPTED
    mgr._save(s)
    mgr._snapshot_output(sid, lid, "VSL: рабочая копия шаблона")
    log.info("VSL %s/%s: создана рабочая копия %s", sid, lid, out.name)
    return out


def _zip_for_read(sid: str, lid: str) -> Path:
    """Архив для чтения конфига: output, если есть, иначе исходный."""
    mgr = _mgr()
    s, ls = mgr._get_lander(sid, lid)
    if ls.output_name and (OUTPUTS / ls.output_name).exists():
        return OUTPUTS / ls.output_name
    if ls.zip_path and Path(ls.zip_path).exists():
        return Path(ls.zip_path)
    raise ValueError("Архив ленда не найден")


def _find_config_member(zf: zipfile.ZipFile) -> str:
    """Путь config.php внутри архива (корень приоритетнее вложенных)."""
    cands = [n for n in zf.namelist()
             if n.replace("\\", "/").split("/")[-1] == "config.php"]
    if not cands:
        raise ValueError("В архиве ленда нет config.php — это не VSL-ленд")
    cands.sort(key=lambda n: n.count("/"))
    return cands[0]


def read_config(sid: str, lid: str) -> dict:
    """Читает $config из config.php ленда → {config, member, product_image}."""
    p = _zip_for_read(sid, lid)
    with zipfile.ZipFile(p, "r") as zf:
        member = _find_config_member(zf)
        text = zf.read(member).decode("utf-8", errors="replace")
    cfg = parse_config_php(text)
    return {"config": cfg, "member": member,
            "product_image": ((cfg.get("orderForm") or {}).get("productImage") or "")}


def write_config(sid: str, lid: str, config: dict) -> dict:
    """Записывает $config в config.php рабочей копии (создаёт её при нужде)."""
    p = ensure_output(sid, lid)
    with zipfile.ZipFile(p, "r") as zf:
        member = _find_config_member(zf)
        text = zf.read(member).decode("utf-8", errors="replace")
    new_text = replace_config_php(text, config)
    mgr = _mgr()
    mgr.write_output_file(sid, lid, member, new_text.encode("utf-8"),
                          label="VSL: конфиг")
    return {"member": member}


def set_product_image(sid: str, lid: str, data: bytes) -> dict:
    """Кладёт фото продукта в архив КАК product.png (правило VSL) и
    прописывает его в config.orderForm.productImage."""
    import io
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Не удалось прочитать изображение: {e}")

    p = ensure_output(sid, lid)
    with zipfile.ZipFile(p, "r") as zf:
        member = _find_config_member(zf)
        text = zf.read(member).decode("utf-8", errors="replace")
    cfg = parse_config_php(text)
    order = cfg.setdefault("orderForm", {})
    order["productImage"] = "product.png"
    new_text = replace_config_php(text, cfg)

    # product.png — рядом с config.php (корень ленда).
    root = member.rsplit("/", 1)[0] + "/" if "/" in member else ""
    mgr = _mgr()
    mgr.write_output_file(sid, lid, root + "product.png", png,
                          label="VSL: product.png", snapshot=False)
    mgr.write_output_file(sid, lid, member, new_text.encode("utf-8"),
                          label="VSL: product.png + конфиг")
    return {"name": root + "product.png", "size": len(png)}


def update_video_links(sid: str, lid: str, archive_name: str) -> dict:
    """Меняет ТОЛЬКО имя папки (архива на сервере) в ссылках video.src/poster.

    'https://cdn.../old_name/promo/master.m3u8' → 'https://cdn.../<name>/promo/master.m3u8'.
    Хост и хвосты ссылок не трогаются (их менять не нужно)."""
    p = ensure_output(sid, lid)
    with zipfile.ZipFile(p, "r") as zf:
        member = _find_config_member(zf)
        text = zf.read(member).decode("utf-8", errors="replace")
    cfg = parse_config_php(text)
    video = cfg.setdefault("video", {})

    def _swap(url: str, tail: str) -> str:
        m = re.match(r"^(https?://[^/]+)/([^/]+)(/.*)$", url or "")
        host = m.group(1) if m else DEFAULT_VIDEO_HOST
        return f"{host}/{archive_name}{tail}"

    video["src"] = _swap(video.get("src", ""), "/promo/master.m3u8")
    video["poster"] = _swap(video.get("poster", ""), "/video-poster.webp")
    new_text = replace_config_php(text, cfg)
    _mgr().write_output_file(sid, lid, member, new_text.encode("utf-8"),
                             label=f"VSL: ссылки видео → {archive_name}")
    return {"src": video["src"], "poster": video["poster"]}


# ── VSL-скан и адаптация значений конфига ────────────────────────
# Обычный сканер/адаптер пропускает содержимое <?php ... ?> целиком, поэтому
# для VSL продукт/цены/гео читаются и меняются ПРЯМО в $config.

def _config_from_zip(zip_path: Path) -> Optional[dict]:
    """$config из config.php архива (None, если это не VSL-архив)."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            member = _find_config_member(zf)
            text = zf.read(member).decode("utf-8", errors="replace")
        return parse_config_php(text)
    except Exception:  # noqa: BLE001
        return None


def overlay_scan(scan: Optional[dict], zip_path: Path) -> Optional[dict]:
    """Накладывает VSL-данные из config.php поверх обычного результата скана.

    Обычный скан на VSL слепой (всё в PHP-конфиге) — продукт, цены, гео,
    exclude_word и фото продукта берём из $config. Возвращает обновлённый
    scan (или исходный, если конфиг не прочитан)."""
    cfg = _config_from_zip(zip_path)
    if cfg is None:
        return scan
    settings = cfg.get("settings") or {}
    order = cfg.get("orderForm") or {}
    from services.session import split_price
    _num, cur = split_price(order.get("newPrice", "") or "")

    out = dict(scan or {})
    product = (cfg.get("pageTitle") or "").strip()
    if product:
        out["product"] = product
        out["product_candidates"] = [{"word": product, "count": 1}]
    out["price_new_str"] = order.get("newPrice", "") or ""
    out["price_old_str"] = order.get("oldPrice", "") or ""
    if cur:
        out["cur_sym"] = cur
    if order.get("productImage"):
        out["prod_images"] = [order["productImage"]]
    country = (settings.get("country") or "").strip()
    language = (settings.get("language") or "").strip()
    out["detected_country"] = {
        "data_country": [country] if country else [],
        "data_language": [language] if language else [],
        "input_country": [country] if country else [],
        "input_language": [language] if language else [],
        "lang_html": language.lower() or None,
        "exclude_word": settings.get("excludeWord") or None,
    }
    out["vsl"] = True
    return out


def refresh_scan(sid: str, lid: str) -> Optional[dict]:
    """Пересчитывает VSL-скан ленда из ТЕКУЩЕГО архива (рабочая копия, иначе
    исходник), сохраняет в состояние ленда и возвращает его."""
    mgr = _mgr()
    s, ls = mgr._get_lander(sid, lid)
    try:
        zip_path = _zip_for_read(sid, lid)
    except ValueError:
        return ls.scan
    merged = overlay_scan(ls.scan, zip_path)
    if merged is not ls.scan:
        ls.scan = merged
        mgr._save(s)
    return merged


def _walk_strings(value, fn):
    """Применяет fn ко всем строковым значениям конфига (кроме URL)."""
    if isinstance(value, str):
        return value if value.startswith(("http://", "https://")) else fn(value)
    if isinstance(value, dict):
        return {k: _walk_strings(v, fn) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_strings(v, fn) for v in value]
    return value


def adapt_config(sid: str, lid: str, params: dict) -> list[str]:
    """Адаптирует ЗНАЧЕНИЯ config.php под параметры адаптации.

    Заменяет продукт и цены во всех текстах конфига (заголовок, комментарии,
    форма, дисклеймеры), затем выставляет структурные поля: pageTitle,
    settings.country/language/excludeWord, orderForm.newPrice/oldPrice.
    → список строк-заметок для лога адаптации."""
    p = ensure_output(sid, lid)
    with zipfile.ZipFile(p, "r") as zf:
        member = _find_config_member(zf)
        text = zf.read(member).decode("utf-8", errors="replace")
    cfg = parse_config_php(text)
    notes: list[str] = []

    # 1) строковые замены (продукт + цены) по всем текстам конфига
    reps: list[tuple[re.Pattern, str]] = []
    po = (params.get("product_old") or "").strip()
    pn = (params.get("product_new") or "").strip()
    if po and pn and po.lower() != pn.lower():
        reps.append((re.compile(re.escape(po), re.IGNORECASE), pn))
    for s_num, s_cur, t_num, t_cur in (
        (params.get("src_price_new_num"), params.get("src_price_new_cur"),
         params.get("price_new_num"), params.get("price_new_cur")),
        (params.get("src_price_old_num"), params.get("src_price_old_cur"),
         params.get("price_old_num"), params.get("price_old_cur")),
    ):
        s_num, t_num = (s_num or "").strip(), (t_num or "").strip()
        if s_num and t_num and s_num != t_num:
            # голое число с границами; проценты не трогаем (скидка «-50%»)
            reps.append((re.compile(
                rf"(?<![\w.,-]){re.escape(s_num)}(?![\w%])"), t_num))
    s_cur = (params.get("src_price_new_cur") or "").strip()
    t_cur = (params.get("price_new_cur") or "").strip()
    if s_cur and t_cur and s_cur != t_cur:
        if s_cur.isalpha():
            reps.append((re.compile(rf"(?<!\w){re.escape(s_cur)}(?!\w)"), t_cur))
        else:  # символьные валюты ($, S/.) — точная замена
            reps.append((re.compile(re.escape(s_cur)), t_cur))

    if reps:
        counter = {"n": 0}

        def _apply(sv: str) -> str:
            out = sv
            for pat, repl in reps:
                out, k = pat.subn(repl, out)
                counter["n"] += k
            return out

        cfg = _walk_strings(cfg, _apply)
        notes.append(f"VSL config: строковых замен {counter['n']} "
                     f"(продукт/цены в текстах)")

    # 2) структурные поля — поверх строковых замен
    if pn:
        cfg["pageTitle"] = pn
        notes.append(f"VSL config: pageTitle = {pn}")
    settings = cfg.setdefault("settings", {})
    geo = (params.get("geo_id") or "").strip().upper()
    lang = ""
    if geo:
        settings["country"] = geo
        from utils import runners
        lang = ((runners.load_geos().get(geo) or {}).get("lang_html") or "").upper()
        if lang:
            settings["language"] = lang
        notes.append(f"VSL config: settings.country={geo}"
                     + (f", language={lang}" if lang else ""))
    if (params.get("exclude_word") or "").strip():
        settings["excludeWord"] = params["exclude_word"]
        notes.append(f"VSL config: excludeWord={params['exclude_word']!r}")
    order = cfg.setdefault("orderForm", {})
    if (params.get("price_new") or "").strip():
        order["newPrice"] = params["price_new"].strip()
    if (params.get("price_old") or "").strip():
        order["oldPrice"] = params["price_old"].strip()
    if params.get("price_new") or params.get("price_old"):
        notes.append(f"VSL config: цены {order.get('newPrice')} / {order.get('oldPrice')}")

    # 3) процент скидки под новые цены (донорский «-75%» при ×2 неверен)
    pct = _discount_percent(order.get("newPrice", ""), order.get("oldPrice", ""))
    disc = order.get("discountText") or ""
    if pct and re.search(r"-?\d+\s*%", disc):
        order["discountText"] = re.sub(r"-?\d+\s*%", f"-{pct}%", disc, count=1)
        notes.append(f"VSL config: discountText → {order['discountText']!r}")

    # 4) колбек-виджет: баеры практически всегда просят выключать (чеклист §6.3)
    cb = cfg.setdefault("callbackWidget", {})
    if cb.get("enabled", False):
        cb["enabled"] = False
        notes.append("VSL config: callbackWidget.enabled=false (правило: выключен, "
                     "если баер не просил)")

    # 5) подсветка заголовка: пустые фразы и фразы не из заголовка — мусор
    title = cfg.get("title") or {}
    hp = title.get("highlightPhrases")
    if isinstance(hp, list):
        txt = title.get("text") or ""
        cleaned = [p for p in hp if isinstance(p, str) and p.strip()
                   and (not txt or p in txt)]
        if cleaned != hp:
            title["highlightPhrases"] = cleaned
            cfg["title"] = title
        if not cleaned and title.get("enabled", False):
            notes.append("VSL config: title.highlightPhrases пуст — задай фразу "
                         "ИЗ текста заголовка (подсветка не работает)")

    new_text = replace_config_php(text, cfg)
    _mgr().write_output_file(sid, lid, member, new_text.encode("utf-8"),
                             label="VSL: адаптация конфига", snapshot=False)

    # 6) обвязка вне конфига: api.php (дефолты гео/языка) и index.php
    #    (Backfix + JSON-макросы Keitaro) — чеклист §6.3 AGENT.md
    notes += _patch_api_php(sid, lid, geo, lang)
    notes += _patch_index_php(sid, lid)
    notes.append("VSL: тексты конфига остаются на языке ДОНОРА — для смены языка "
                 "нажми «Перевод» (переведёт и config.php)")
    return notes


def _discount_percent(price_new: str, price_old: str) -> Optional[int]:
    """Процент скидки из пары цен ('149 PEN', '298 PEN' → 50). None — не счесть."""
    def _num(s: str) -> Optional[float]:
        m = re.search(r"\d+(?:[.,]\d+)?", (s or "").replace(" ", ""))
        if not m:
            return None
        try:
            return float(m.group(0).replace(",", "."))
        except ValueError:
            return None
    new, old = _num(price_new), _num(price_old)
    if not new or not old or old <= new:
        return None
    return round((1 - new / old) * 100)


def _find_member(zf: zipfile.ZipFile, basename: str) -> Optional[str]:
    """Путь файла `basename` внутри архива (корень приоритетнее вложенных)."""
    cands = [n for n in zf.namelist()
             if n.replace("\\", "/").split("/")[-1] == basename]
    cands.sort(key=lambda n: n.count("/"))
    return cands[0] if cands else None


def _read_output_member(sid: str, lid: str, basename: str) -> Optional[tuple[str, str]]:
    """(member, text) файла из output-архива VSL-ленда, None — файла нет."""
    p = ensure_output(sid, lid)
    with zipfile.ZipFile(p, "r") as zf:
        member = _find_member(zf, basename)
        if member is None:
            return None
        return member, zf.read(member).decode("utf-8", errors="replace")


def _patch_api_php(sid: str, lid: str, geo: str, lang: str) -> list[str]:
    """Дефолты страны/языка в api.php под целевое гео (у донора остаются его:
    `$country = … : 'MX'` слал бы MX на страницу «Спасибо» при пустом POST)."""
    found = _read_output_member(sid, lid, "api.php")
    if not found:
        return []
    member, text = found
    notes: list[str] = []
    new_text = text
    for var, val in (("country", geo), ("language", lang)):
        if not val:
            continue
        pat = re.compile(
            rf"(\$\s*{var}\s*=\s*isset\(\$_POST\['{var}'\]\)\s*\?\s*"
            rf"\$_POST\['{var}'\]\s*:\s*')[^']*(')")
        new_text, n = pat.subn(rf"\g<1>{val}\g<2>", new_text)
        if n:
            notes.append(f"VSL api.php: дефолт {var}='{val}'")
    if new_text != text:
        _mgr().write_output_file(sid, lid, member, new_text.encode("utf-8"),
                                 label="VSL: api.php дефолты гео", snapshot=False)
    return notes


# Обязательный Backfix в <head> index.php (регламент §6.3; макрос Keitaro
# {_from_file:backfix_file_path} подставляется шаблонизатором при заливке).
_BACKFIX_BLOCK = """\
<!-- Backfix START -->
<script
    data-click-data='<?= $clickJson ?>'
    id="backfix"
    src="{_from_file:backfix_file_path}"
    type="module"
></script>
<!-- Backfix END -->
"""

# JSON-макросы Keitaro, обязательные в $macros index.php (шаблон 19201 без них).
_REQUIRED_MACROS = [
    ("offer_id", "{offer_id}"),
    ("subid", "{subid}"),
    ("country", "{country}"),
    ("language", "{language}"),
]


def _patch_index_php(sid: str, lid: str) -> list[str]:
    """Обвязка index.php по чеклисту VSL: Backfix перед </head>, недостающие
    JSON-макросы в $macros, относительный favicon. Идемпотентно."""
    found = _read_output_member(sid, lid, "index.php")
    if not found:
        return []
    member, text = found
    notes: list[str] = []
    new_text = text

    if 'id="backfix"' not in new_text and re.search(r"</head>", new_text, re.I):
        new_text = re.sub(r"</head>", "\n" + _BACKFIX_BLOCK + "</head>",
                          new_text, count=1, flags=re.I)
        notes.append("VSL index.php: добавлен Backfix в <head>")

    m = re.search(r"\$macros\s*=\s*\[(.*?)\];", new_text, re.S)
    if m:
        body = m.group(1)
        missing = [(k, v) for k, v in _REQUIRED_MACROS if f"'{k}'" not in body]
        if missing:
            head = new_text[:m.end(1)].rstrip()
            # последний элемент массива мог быть без хвостовой запятой
            if not head.endswith((",", "[")):
                head += ","
            add = "".join(f"    '{k}' => '{v}',\n" for k, v in missing)
            new_text = head + "\n" + add + new_text[m.end(1):].lstrip("\n")
            notes.append("VSL index.php: макросы " +
                         ", ".join(k for k, _ in missing) + " добавлены в $macros")

    fixed, n = re.subn(r'(href=["\'])/((?:\w|[-.])+\.ico["\'])', r"\1./\2", new_text)
    if n:
        new_text = fixed
        notes.append("VSL index.php: favicon → относительный путь")

    if new_text != text:
        _mgr().write_output_file(sid, lid, member, new_text.encode("utf-8"),
                                 label="VSL: обвязка index.php", snapshot=False)
    return notes


# ── перевод строк конфига (вызывается из services.translate) ────
# Ключи, значения которых переводить нельзя: пути к файлам, коды стран/языков.
_CFG_SKIP_KEYS = {"productimage", "imagesrc", "src", "poster", "avatar",
                  "country", "language", "currency", "excludeword",
                  "pagetitle"}  # pageTitle = название продукта, не переводится
_CFG_PATHY_RE = re.compile(
    r"\.(png|jpe?g|webp|gif|svg|ico|css|js|php|json|m3u8|mp4|woff2?)$", re.I)


def _cfg_translatable(key: str, val: str) -> bool:
    v = (val or "").strip()
    if key.lower() in _CFG_SKIP_KEYS or len(v) < 2:
        return False
    if v.startswith(("http://", "https://")) or _CFG_PATHY_RE.search(v):
        return False
    if re.fullmatch(r"[A-Z]{2,3}", v):    # коды 'PE'/'ES'/'PEN'
        return False
    return bool(re.search(r"[^\W\d_]", v))


def config_translatable_strings(cfg: dict) -> list[str]:
    """Переводимые строковые значения конфига (тексты формы, уведомления,
    комментарии fakeChat и т.д.) — уникальные, длинные первыми."""
    out: dict[str, None] = {}

    def walk(val, key: str = "") -> None:
        if isinstance(val, str):
            if _cfg_translatable(key, val):
                out.setdefault(val.strip(), None)
        elif isinstance(val, dict):
            for k, x in val.items():
                walk(x, str(k))
        elif isinstance(val, list):
            for x in val:
                walk(x, key)

    walk(cfg)
    return sorted(out, key=len, reverse=True)


def config_apply_translations(cfg: dict, mapping: dict[str, str]):
    """Возвращает копию конфига с применённым словарём перевода (те же
    правила обхода, что и при сборе блоков)."""
    def walk(val, key: str = ""):
        if isinstance(val, str):
            if _cfg_translatable(key, val):
                tr = mapping.get(val.strip())
                if tr and tr.strip() and tr.strip() != val.strip():
                    return tr
            return val
        if isinstance(val, dict):
            return {k: walk(x, str(k)) for k, x in val.items()}
        if isinstance(val, list):
            return [walk(x, key) for x in val]
        return val

    return walk(cfg)


def _task_creator(s, ls) -> str:
    """Логин автора задачи ленда ('Created by' карточки AdRobot) — для имени
    видеоархива. Нет задачи/поля → 'mch' (исполнитель)."""
    tasks = getattr(s, "tasks", None) or []
    uid = ls.task_uid or (tasks[0].get("uid") if len(tasks) == 1 else None)
    for t in tasks:
        if t.get("uid") == uid:
            m = re.search(r"[A-Za-z][\w.-]*", (t.get("fields") or {}).get("Created by") or "")
            if m:
                return m.group(0).lower()
    return "mch"


def suggest_archive_name(sid: str, lid: str) -> str:
    """Автоимя архива видео: вертикаль_язык_гео_продукт_<автор задачи>
    (см. Часть 3; автор = Created by задачи, напр. kim, а не исполнитель)."""
    from services.session import parse_target_offer
    from utils import runners
    mgr = _mgr()
    s, ls = mgr._get_lander(sid, lid)
    geos = runners.load_geos()
    parsed = parse_target_offer(s.lander_offer(ls), geos)
    geo = (parsed.get("geo_id") or "").lower()
    lang = (geos.get(parsed.get("geo_id") or "", {}) or {}).get("lang_html", "") or ""
    vert = (parsed.get("vertical") or "").lower()
    product = re.sub(r"[^a-z0-9]+", "_",
                     (parsed.get("product_search") or parsed.get("product") or "").lower()).strip("_")
    parts = [x for x in (vert, lang.lower(), geo, product, _task_creator(s, ls)) if x]
    return "_".join(parts) or "vsl_video"
