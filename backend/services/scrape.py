"""Скачивание лендинга по ссылке в самодостаточный ZIP (как webscrapbook).

Использует Playwright: загружает страницу (с JS-рендерингом), перехватывает ВСЕ
реально загруженные ресурсы (css/js/img/шрифты/видео), сохраняет их с локальной
структурой и переписывает ссылки в HTML и CSS на относительные локальные пути.
На выходе — ZIP с index.html, готовый к адаптации.

Ограничения: защитные/гео-ленды (требующие antidetect/прокси) могут отдать не тот
контент — это вне MVP (см. AGENT.md про Dolphin Anty).
"""

from __future__ import annotations

import io
import logging
import os
import posixpath
import re
import zipfile
from datetime import datetime
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

log = logging.getLogger("scrape")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Атрибуты со ссылками на ресурсы, которые переписываем на локальные пути.
_URL_ATTRS = ("src", "href", "poster", "data-src", "data-lazy-src",
              "data-bg", "data-background", "data-product-image")
_SKIP_PREFIX = ("data:", "javascript:", "mailto:", "tel:", "#", "{", "<?")
_MAX_BYTES = 250 * 1024 * 1024


def _local_path(resource_url: str, main_host: str, base_dir: str = "") -> str:
    """Локальный путь ресурса в архиве ОТНОСИТЕЛЬНО папки документа.

    index.html кладётся в корень архива, а ленд на сайте может лежать в подпапке
    (/lander/<slug>/). Ресурсы того же домена считаем относительно base_dir
    (папки документа) — общий префикс убираем, чтобы 'images/x.jpg' в HTML
    указывало на 'images/x.jpg' в архиве, БЕЗ лишних папок lander/<slug>.
    Внешние домены → _ext/<host>/<path>. Пути выше base_dir (../) → _up/."""
    p = urlparse(resource_url)
    host = (p.hostname or "").lower()
    path = unquote(p.path).lstrip("/")
    if not path or path.endswith("/"):
        path = (path + "index.html")
    if p.query:
        stem, dot, ext = path.rpartition(".")
        h = format(abs(hash(p.query)) % 0xFFFF, "x")
        path = f"{stem}.{h}.{ext}" if dot else f"{path}.{h}"
    path = re.sub(r"[?#].*$", "", path)

    if host and host != main_host:
        path = re.sub(r"[^\w./-]", "_", path)
        return f"_ext/{re.sub(r'[^a-z0-9.-]', '_', host)}/{path}"

    # Тот же домен — относительно папки документа.
    base = (base_dir or "").strip("/")
    if base:
        rel = posixpath.relpath("/" + path, "/" + base)
        # Внутри base_dir — относит. путь чистый; выше — начинается с '../'.
        path = ("_up/" + rel.replace("../", "")) if rel.startswith("..") else rel
    return re.sub(r"[^\w./-]", "_", path)


def _abs(base: str, val: str) -> Optional[str]:
    """Абсолютный URL ресурса из значения атрибута/css; None — если не ресурс."""
    val = (val or "").strip().strip("'\"")
    if not val or val.startswith(_SKIP_PREFIX):
        return None
    try:
        u = urljoin(base, val).split("#")[0]
    except Exception:  # noqa: BLE001
        return None
    return u if u.startswith(("http://", "https://")) else None


def collect_html_urls(html: str, base_url: str) -> set[str]:
    """Все абсолютные URL ресурсов, на которые ссылается HTML (атрибуты,
    srcset, inline style, <style>) — чтобы докачать то, что браузер не запросил."""
    urls: set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    # rel у <link>, которые НЕ ресурсы (не качать).
    _SKIP_REL = {"canonical", "alternate", "dns-prefetch", "preconnect", "prefetch"}
    for tag in soup.find_all(True):
        for attr in _URL_ATTRS:
            v = tag.get(attr)
            if not isinstance(v, str):
                continue
            # href — ресурс только у <link rel=stylesheet/icon...>, НЕ у <a>/<area>
            if attr == "href":
                if tag.name in ("a", "area", "base"):
                    continue
                rel = " ".join(tag.get("rel") or []).lower()
                if tag.name == "link" and any(r in _SKIP_REL for r in rel.split()):
                    continue
            u = _abs(base_url, v)
            if u:
                urls.add(u)
        ss = tag.get("srcset")
        if isinstance(ss, str):
            for item in ss.split(","):
                bits = item.strip().split()
                if bits:
                    u = _abs(base_url, bits[0])
                    if u:
                        urls.add(u)
        st = tag.get("style")
        if isinstance(st, str):
            for m in re.finditer(r"url\(\s*([^)]+?)\s*\)", st):
                u = _abs(base_url, m.group(1))
                if u:
                    urls.add(u)
    for style in soup.find_all("style"):
        if style.string:
            urls |= collect_css_urls(style.string, base_url)
    return urls


def collect_css_urls(css: str, base_url: str) -> set[str]:
    """URL ресурсов из CSS-текста (url(), @import)."""
    urls: set[str] = set()
    for m in re.finditer(r"url\(\s*([^)]+?)\s*\)", css):
        u = _abs(base_url, m.group(1))
        if u:
            urls.add(u)
    for m in re.finditer(r"@import\s+(?:url\()?\s*([^;)]+?)\s*\)?\s*;", css):
        u = _abs(base_url, m.group(1))
        if u:
            urls.add(u)
    return urls


def _rewrite_css(css: str, css_local_path: str, css_abs_url: str,
                 urlmap: dict[str, str]) -> str:
    """Переписывает url(...) и @import в CSS на относительные локальные пути."""
    css_dir = posixpath.dirname(css_local_path)

    def repl_url(m):
        raw = m.group(1).strip().strip("'\"")
        if raw.startswith(_SKIP_PREFIX):
            return m.group(0)
        abs_u = urljoin(css_abs_url, raw)
        local = urlmap.get(abs_u.split("#")[0])
        if not local:
            return m.group(0)
        rel = posixpath.relpath(local, css_dir or ".")
        return f"url('{rel}')"

    css = re.sub(r"url\(\s*([^)]+?)\s*\)", repl_url, css)

    def repl_import(m):
        raw = m.group(1).strip().strip("'\"")
        abs_u = urljoin(css_abs_url, raw)
        local = urlmap.get(abs_u.split("#")[0])
        if not local:
            return m.group(0)
        rel = posixpath.relpath(local, css_dir or ".")
        return f"@import '{rel}'"

    css = re.sub(r"@import\s+(?:url\()?\s*([^;)]+?)\s*\)?", repl_import, css)
    return css


def _build_zip(html: str, final_url: str,
               resources: dict[str, tuple[bytes, str]]) -> bytes:
    soup = BeautifulSoup(html, "html.parser")

    # Эффективный base: <base href> переопределяет URL документа. Относительные
    # ссылки и реальные пути ресурсов резолвятся браузером ИМЕННО от него
    # (поэтому ресурсы приходят с /lander/<slug>/). Берём его для base_dir и
    # резолва ссылок, а сам тег <base> из локального архива УДАЛЯЕМ (иначе
    # сломает относительные пути в zip).
    base_url = final_url
    base_tag = soup.find("base", href=True)
    if base_tag:
        base_url = urljoin(final_url, base_tag["href"])
        base_tag.decompose()

    main_host = (urlparse(base_url).hostname or "").lower()
    base_dir = posixpath.dirname(urlparse(base_url).path).strip("/")
    # Если base заканчивается на '/', dirname срежет последний сегмент — вернём.
    if urlparse(base_url).path.endswith("/"):
        base_dir = urlparse(base_url).path.strip("/")

    # 1) Карта абсолютный_url → локальный путь.
    urlmap: dict[str, str] = {}
    used: set[str] = set()
    for res_url in resources:
        lp = _local_path(res_url, main_host, base_dir)
        base, ext = posixpath.splitext(lp)
        i = 1
        while lp in used:
            lp = f"{base}_{i}{ext}"
            i += 1
        used.add(lp)
        urlmap[res_url.split("#")[0]] = lp

    # 2) Переписать ссылки в HTML.
    def to_local(val: str) -> Optional[str]:
        if not val or val.startswith(_SKIP_PREFIX):
            return None
        abs_u = urljoin(base_url, val).split("#")[0]
        return urlmap.get(abs_u)

    for tag in soup.find_all(True):
        for attr in _URL_ATTRS:
            val = tag.get(attr)
            if isinstance(val, str):
                local = to_local(val)
                if local:
                    tag[attr] = local
        # srcset
        ss = tag.get("srcset")
        if isinstance(ss, str):
            parts = []
            for item in ss.split(","):
                bits = item.strip().split()
                if bits:
                    local = to_local(bits[0])
                    if local:
                        bits[0] = local
                    parts.append(" ".join(bits))
            tag["srcset"] = ", ".join(parts)
        # inline style url()
        st = tag.get("style")
        if isinstance(st, str) and "url(" in st:
            tag["style"] = re.sub(
                r"url\(\s*([^)]+?)\s*\)",
                lambda m: (lambda lp: f"url('{lp}')" if lp else m.group(0))(
                    to_local(m.group(1).strip().strip("'\""))),
                st)

    # <style> блоки
    for style in soup.find_all("style"):
        if style.string:
            style.string = _rewrite_css(style.string, "index.html", final_url, urlmap)

    new_html = str(soup)

    # 3) Собрать ZIP.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.html", new_html)
        for res_url, (data, ctype) in resources.items():
            lp = urlmap.get(res_url.split("#")[0])
            if not lp or lp == "index.html":
                continue
            # CSS — переписать внутренние ссылки.
            if "css" in ctype.lower() or lp.lower().endswith(".css"):
                try:
                    css = data.decode("utf-8", errors="replace")
                    data = _rewrite_css(css, lp, res_url, urlmap).encode("utf-8")
                except Exception:  # noqa: BLE001
                    pass
            try:
                zf.writestr(lp, data)
            except Exception:  # noqa: BLE001
                continue
    return buf.getvalue()


def scrape_site(url: str, *, proxy: Optional[dict] = None, timeout_ms: int = 60000,
                wait_extra_ms: int = 2500, headless: bool = True) -> tuple[bytes, str]:
    """Скачивает лендинг по ссылке → (zip_bytes, suggested_filename).

    proxy — Playwright-proxy {server, username?, password?} для обхода гео-защиты
    (резидентный прокси нужной страны). Если None — прямое подключение.
    """
    from playwright.sync_api import sync_playwright

    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    resources: dict[str, tuple[bytes, str]] = {}
    total = 0
    main_html = ""
    final_url = url

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx_kwargs: dict = {"user_agent": USER_AGENT,
                            "viewport": {"width": 1366, "height": 900}}
        if proxy and proxy.get("server"):
            ctx_kwargs["proxy"] = proxy
            log.info("Скрапинг через прокси %s", proxy.get("server"))
        ctx = browser.new_context(**ctx_kwargs)
        page = ctx.new_page()

        def on_response(resp):
            nonlocal total
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if "text/html" in ct and resp.url.split("#")[0] in (url, final_url):
                    return  # основной документ берём из page.content()
                if not resp.ok:
                    return
                body = resp.body()
                if not body or total + len(body) > _MAX_BYTES:
                    return
                total += len(body)
                resources[resp.url] = (body, ct)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout_ms)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(wait_extra_ms)
            # Прокрутить — триггерим ленивую загрузку картинок.
            try:
                page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1500)
                page.evaluate("() => window.scrollTo(0, 0)")
                page.wait_for_timeout(800)
            except Exception:  # noqa: BLE001
                pass
            final_url = page.url
            main_html = page.content()

            # Докачиваем ресурсы, которые браузер НЕ запросил при рендере
            # (lazy-load, preload, media-queries, ссылки в CSS) — через тот же
            # контекст (прокси/куки). Иначе стили/фото будут битыми.
            # base href переопределяет резолв относительных ссылок.
            _bm = re.search(r'<base\b[^>]*\bhref\s*=\s*["\']([^"\']+)', main_html, re.I)
            doc_base = urljoin(final_url, _bm.group(1)) if _bm else final_url
            want = collect_html_urls(main_html, doc_base)
            for body, ct in list(resources.values()):  # + ссылки из собранных CSS
                if "css" in ct.lower():
                    try:
                        want |= collect_css_urls(body.decode("utf-8", errors="replace"), final_url)
                    except Exception:  # noqa: BLE001
                        pass
            for _ in range(2):  # 2 прохода: CSS может тянуть новые ресурсы
                missing = [u for u in want if u not in resources]
                if not missing:
                    break
                new_css = []
                for u in missing:
                    if total > _MAX_BYTES:
                        break
                    try:
                        r = ctx.request.get(u, timeout=30000)
                        if not r.ok:
                            continue
                        b = r.body()
                        if not b:
                            continue
                        ct = (r.headers or {}).get("content-type", "")
                        resources[u] = (b, ct)
                        total += len(b)
                        if "css" in ct.lower() or u.lower().endswith(".css"):
                            new_css.append((b, u))
                    except Exception:  # noqa: BLE001
                        continue
                for b, base in new_css:  # ссылки из новых CSS — в следующий проход
                    try:
                        want |= collect_css_urls(b.decode("utf-8", errors="replace"), base)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass

    if not main_html:
        raise ValueError("Не удалось загрузить страницу")

    zip_bytes = _build_zip(main_html, final_url, resources)
    host = re.sub(r"[^a-z0-9.-]", "_", (urlparse(final_url).hostname or "site").lower())
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return zip_bytes, f"{host}_{ts}.zip"


# ── CLI ──────────────────────────────────────────────────────────
def _main() -> None:
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        sys.exit("Использование: python -m services.scrape <url> [out.zip]")
    data, name = scrape_site(sys.argv[1])
    out = sys.argv[2] if len(sys.argv) > 2 else name
    with open(out, "wb") as f:
        f.write(data)
    import zipfile as _z
    z = _z.ZipFile(io.BytesIO(data))
    print(f"Сохранено: {out} ({len(data)} байт, {len(z.namelist())} файлов)")


if __name__ == "__main__":
    _main()
