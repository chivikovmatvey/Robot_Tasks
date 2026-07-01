"""
API-роуты для обработки офферов.
Каждый роут принимает multipart/form-data с zip-файлом + параметрами.
Возвращает JSON с download URL и логом обработки.
"""
import json
import mimetypes
import zipfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from utils import runners
from utils.files import save_upload, output_relative_url
from utils.runners import STORAGE

router = APIRouter(prefix="/api", tags=["processing"])

_PREVIEW_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
_PREVIEW_MAX_BYTES = 25 * 1024 * 1024


def _scan_preview_validate_upload_id(upload_id: str) -> None:
    if any(c in upload_id for c in ('/', '\\', '..')):
        raise HTTPException(400, "Invalid upload_id")


def _scan_preview_validate_inner_path(raw: str) -> bool:
    if not raw:
        return False
    norm = raw.replace('\\', '/').strip()
    if norm.startswith('/'):
        return False
    return '..' not in norm.split('/')


# ══════════════════════════════════════════════════════════════
# Справочники (для дропдаунов на фронте)
# ══════════════════════════════════════════════════════════════
@router.get("/geos")
def get_geos():
    """Список ГЕО для дропдауна — id, страна, валюта, язык."""
    geos = runners.load_geos()
    return [
        {
            'id': geo_id,
            'country_name': data.get('country_name', geo_id),
            'currency': data.get('currency', ''),
            'lang': data.get('lang', ''),
            'lang_html': data.get('lang_html', ''),
        }
        for geo_id, data in sorted(geos.items())
    ]



@router.get("/geo-words/{upload_id}")
def get_geo_words(upload_id: str, source_geo: str = Query("", description="ГЕО источника для поиска слов")):
    """
    Сканирует текст оффера и возвращает гео-слова исходного ГЕО которые нашёл в HTML.
    Используется для автодетекта замен при адаптации (Mexico → Colombia и т.п.)
    """
    _scan_preview_validate_upload_id(upload_id)
    upload_path = STORAGE / 'uploads' / upload_id
    if not upload_path.exists():
        raise HTTPException(404, f"Upload not found: {upload_id}")

    from scripts import scanner
    geos = scanner.load_geos()

    # Если source_geo не указан — определяем из data-country в тексте
    text = scanner.read_zip_text(str(upload_path))
    if not source_geo:
        detected = scanner.detect_country_lang(text)
        countries = detected.get('data_country', [])
        source_geo = countries[0] if countries else ''

    if not source_geo or source_geo not in geos:
        return {"source_geo": source_geo, "found_words": []}

    geo_words = geos[source_geo].get('geo_words', [])
    if not geo_words:
        return {"source_geo": source_geo, "found_words": []}

    # Ищем каждое слово в тексте (case-sensitive — важно для имён)
    found = []
    for word in geo_words:
        if word in text:
            found.append(word)

    return {"source_geo": source_geo, "found_words": found}


@router.get("/verticals")
def get_verticals():
    """Список вертикалей с exclude_word."""
    return runners.load_verticals()


@router.get("/assets")
def list_assets():
    """Список фото в storage/assets/ для выпадающего списка."""
    assets_dir = STORAGE / 'assets'
    if not assets_dir.exists():
        return []
    return sorted([
        f.name for f in assets_dir.iterdir()
        if f.is_file() and not f.name.startswith('.')
    ])


@router.post("/assets/upload")
async def upload_asset(file: UploadFile = File(...)):
    """Загрузка фото в storage/assets/."""
    if not file.filename:
        raise HTTPException(400, "Empty filename")
    allowed = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"File type {ext} not allowed")
    assets_dir = STORAGE / 'assets'
    assets_dir.mkdir(parents=True, exist_ok=True)
    target = assets_dir / file.filename
    target.write_bytes(await file.read())
    return {"name": file.filename, "size": target.stat().st_size}


# ══════════════════════════════════════════════════════════════
# Существующие эндпоинты ниже
# ══════════════════════════════════════════════════════════════
@router.get("/assets-file/{filename}")
def get_asset_file(filename: str):
    """Отдаёт файл из storage/assets/ — для превью на странице Assets."""
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(400, "Invalid filename")
    target = STORAGE / 'assets' / filename
    if not target.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(target)


@router.delete("/assets/{filename}")
def delete_asset(filename: str):
    """Удалить фото."""
    # Защита от path traversal
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(400, "Invalid filename")
    target = STORAGE / 'assets' / filename
    if not target.exists():
        raise HTTPException(404, "Not found")
    target.unlink()
    return {"deleted": filename}


@router.get("/outputs")
def list_outputs():
    """Список готовых zip-результатов в storage/outputs/."""
    outs = STORAGE / 'outputs'
    if not outs.exists():
        return []
    files = []
    for f in sorted(outs.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True):
        files.append({
            'name': f.name,
            'size': f.stat().st_size,
            'modified': f.stat().st_mtime,
            'url': output_relative_url(f),
        })
    return files


@router.get("/download/{filename}")
def download_output(filename: str):
    """Скачивание готового zip."""
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(400, "Invalid filename")
    target = STORAGE / 'outputs' / filename
    if not target.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(target, filename=filename, media_type='application/zip')


# ══════════════════════════════════════════════════════════════
# INJECT
# ══════════════════════════════════════════════════════════════
@router.post("/inject")
async def inject_endpoint(
    file: UploadFile = File(...),
    country: str = Form(""),
    language: str = Form(""),
    exclude_word: str = Form(""),
    price_new: str = Form(""),
    price_old: str = Form(""),
    prod_img: str = Form("product.webp"),
    custom_replacements: str = Form(""),
):
    upload = await save_upload(file)
    out_path, capture = runners.run_inject(str(upload), {
        'country': country,
        'language': language,
        'exclude_word': exclude_word,
        'price_new': price_new,
        'price_old': price_old,
        'prod_img': prod_img,
        'custom_replacements': custom_replacements,
    })
    return {
        "success": out_path is not None,
        "result_url": output_relative_url(out_path) if out_path else None,
        "result_name": Path(out_path).name if out_path else None,
        "log": capture.to_dicts(),
    }


# ══════════════════════════════════════════════════════════════
# CLEAN
# ══════════════════════════════════════════════════════════════
@router.post("/clean")
async def clean_endpoint(file: UploadFile = File(...)):
    upload = await save_upload(file)
    out_path, capture = runners.run_clean(str(upload))
    return {
        "success": out_path is not None,
        "result_url": output_relative_url(out_path) if out_path else None,
        "result_name": Path(out_path).name if out_path else None,
        "log": capture.to_dicts(),
    }


# ══════════════════════════════════════════════════════════════
# CLEAN + INJECT
# ══════════════════════════════════════════════════════════════
@router.post("/clean-inject")
async def clean_inject_endpoint(
    file: UploadFile = File(...),
    country: str = Form(""),
    language: str = Form(""),
    exclude_word: str = Form(""),
    price_new: str = Form(""),
    price_old: str = Form(""),
    prod_img: str = Form("product.webp"),
    custom_replacements: str = Form(""),
):
    upload = await save_upload(file)
    out_path, capture = runners.run_clean_inject(str(upload), {
        'country': country,
        'language': language,
        'exclude_word': exclude_word,
        'price_new': price_new,
        'price_old': price_old,
        'prod_img': prod_img,
        'custom_replacements': custom_replacements,
    })
    return {
        "success": out_path is not None,
        "result_url": output_relative_url(out_path) if out_path else None,
        "result_name": Path(out_path).name if out_path else None,
        "log": capture.to_dicts(),
    }


# ══════════════════════════════════════════════════════════════
# ANCHORS
# ══════════════════════════════════════════════════════════════
@router.post("/anchors")
async def anchors_endpoint(file: UploadFile = File(...)):
    upload = await save_upload(file)
    out_path, capture = runners.run_anchors(str(upload))
    return {
        "success": out_path is not None,
        "result_url": output_relative_url(out_path) if out_path else None,
        "result_name": Path(out_path).name if out_path else None,
        "log": capture.to_dicts(),
    }


# ══════════════════════════════════════════════════════════════
# SCAN — шаг 1
# ══════════════════════════════════════════════════════════════
@router.post("/scan")
async def scan_endpoint(file: UploadFile = File(...)):
    """
    Шаг 1: только сканирование. Возвращает найденное для предзаполнения формы.
    Файл сохраняется и его id возвращается, чтобы шаг 2 (adapt) использовал тот же файл.
    """
    upload = await save_upload(file)
    detection = runners.run_scan_only(str(upload))
    return {
        "upload_id": upload.name,    # для последующего вызова /api/adapt
        "detection": detection,
    }


@router.get("/scan-preview/{upload_id}")
def scan_preview_image(upload_id: str, path: str = Query(..., description="Путь к файлу внутри zip")):
    """
    Превью картинки из загруженного архива (для Scan+Adapt — подбор замены фото).
    """
    _scan_preview_validate_upload_id(upload_id)
    if not _scan_preview_validate_inner_path(path):
        raise HTTPException(400, "Invalid path")

    ext = Path(path.replace('\\', '/')).suffix.lower()
    if ext not in _PREVIEW_EXT:
        raise HTTPException(400, "Only image previews are allowed")

    upload_path = STORAGE / 'uploads' / upload_id
    if not upload_path.is_file():
        raise HTTPException(404, "Upload not found")

    target = path.replace('\\', '/')

    try:
        with zipfile.ZipFile(upload_path, 'r') as zf:
            names = zf.namelist()
            member = target if target in names else None
            if member is None:
                for n in names:
                    if n.replace('\\', '/') == target:
                        member = n
                        break
            if member is None:
                raise HTTPException(404, "File not in archive")

            info = zf.getinfo(member)
            if info.file_size > _PREVIEW_MAX_BYTES:
                raise HTTPException(413, "Image too large for preview")

            data = zf.read(member)
    except zipfile.BadZipFile:
        raise HTTPException(400, "Corrupt zip")

    media = mimetypes.guess_type(member)[0] or 'application/octet-stream'
    return Response(content=data, media_type=media)


# ══════════════════════════════════════════════════════════════
# ADAPT — шаг 2
# ══════════════════════════════════════════════════════════════
@router.post("/adapt")
async def adapt_endpoint(
    upload_id: str = Form(...),
    geo_id: str = Form(...),
    product_old: str = Form(""),
    product_new: str = Form(""),
    price_new: str = Form(""),
    price_old: str = Form(""),
    price_new_num: str = Form(""),
    price_new_cur: str = Form(""),
    price_old_num: str = Form(""),
    price_old_cur: str = Form(""),
    src_price_new_num: str = Form(""),
    src_price_new_cur: str = Form(""),
    src_price_old_num: str = Form(""),
    src_price_old_cur: str = Form(""),
    exclude_word: str = Form(""),
    image_map: str = Form("{}"),  # JSON-строка old->new mapping
    custom_replacements: str = Form(""),
):
    # Защита от path traversal
    if '/' in upload_id or '\\' in upload_id or '..' in upload_id:
        raise HTTPException(400, "Invalid upload_id")
    upload_path = STORAGE / 'uploads' / upload_id
    if not upload_path.exists():
        raise HTTPException(404, f"Upload not found: {upload_id}")

    try:
        image_map_dict = json.loads(image_map)
    except json.JSONDecodeError:
        raise HTTPException(400, "image_map must be valid JSON")

    out_path, capture = runners.run_adapt(str(upload_path), {
        'geo_id': geo_id,
        'product_old': product_old,
        'product_new': product_new,
        'price_new': price_new,
        'price_old': price_old,
        'price_new_num': price_new_num,
        'price_new_cur': price_new_cur,
        'price_old_num': price_old_num,
        'price_old_cur': price_old_cur,
        'src_price_new_num': src_price_new_num,
        'src_price_new_cur': src_price_new_cur,
        'src_price_old_num': src_price_old_num,
        'src_price_old_cur': src_price_old_cur,
        'exclude_word': exclude_word,
        'image_map': image_map_dict,
        'custom_replacements': custom_replacements,
    })
    return {
        "success": out_path is not None,
        "result_url": output_relative_url(out_path) if out_path else None,
        "result_name": Path(out_path).name if out_path else None,
        "log": capture.to_dicts(),
    }




@router.get("/preview/{filename}/files")
def preview_list_files(filename: str):
    """Список файлов внутри zip из outputs."""
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(400, "Invalid filename")
    target = STORAGE / 'outputs' / filename
    if not target.exists():
        raise HTTPException(404, "Not found")
    import zipfile
    files = []
    with zipfile.ZipFile(target, 'r') as zf:
        for info in zf.infolist():
            if not info.filename.endswith('/'):
                files.append({
                    'path': info.filename,
                    'size': info.file_size,
                })
    # Сортируем: сначала html/php, потом css/js, потом остальное
    def sort_key(f):
        ext = Path(f['path']).suffix.lower()
        if ext in ('.php', '.html', '.htm'): return 0
        if ext in ('.css', '.js'): return 1
        return 2
    files.sort(key=sort_key)
    return files


def _zip_names(zf) -> set[str]:
    """Множество имён файлов внутри zip (без папок)."""
    return {n for n in zf.namelist() if not n.endswith('/')}


def _resolve_zip_path(raw: str, base_dir: str, names: set[str]) -> str | None:
    """
    Резолвит путь из HTML/CSS в реальное имя внутри zip.
    Понимает './assets/x.css', '../img/a.png', 'style.css?v=2', абсолютные '/assets/...'.
    """
    import posixpath
    val = raw.split('?')[0].split('#')[0].strip().replace('\\', '/')
    if not val:
        return None
    candidates = []
    if val.startswith('/'):
        candidates.append(posixpath.normpath(val.lstrip('/')))
    else:
        if base_dir:
            candidates.append(posixpath.normpath(posixpath.join(base_dir, val)))
        candidates.append(posixpath.normpath(val))
    for c in candidates:
        if c in names:
            return c
    return None


_MIME_BY_EXT = {
    '.css':  'text/css; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png':  'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.webp': 'image/webp', '.gif': 'image/gif', '.svg': 'image/svg+xml',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff', '.woff2': 'font/woff2',
    '.ttf':  'font/ttf', '.otf': 'font/otf', '.eot': 'application/vnd.ms-fontobject',
    '.mp4':  'video/mp4', '.webm': 'video/webm', '.mp3': 'audio/mpeg',
}


def _resolve_preview_target(filename: str) -> Path:
    """Zip-источник превью. 'session__<sid>__<lid>' → ИСХОДНЫЙ архив ленда
    (для превью до адаптации); иначе — storage/outputs/<filename>."""
    if filename.startswith('session__'):
        rest = filename[len('session__'):]
        sid, _, lid = rest.partition('__')
        from services.session import get_manager
        s = get_manager().get(sid)
        ls = s.landers.get(lid) if s else None
        if ls and ls.zip_path and Path(ls.zip_path).exists():
            return Path(ls.zip_path)
        raise HTTPException(404, "Исходный архив ленда не найден")
    target = STORAGE / 'outputs' / filename
    if not target.exists():
        raise HTTPException(404, "Not found")
    return target


def _entry_path(path: str, names: set[str]) -> str | None:
    """Резолв точки входа: точный путь, иначе index.php/html/htm в корне,
    иначе первый html/php-файл архива."""
    real = path if path in names else _resolve_zip_path(path, '', names)
    if real:
        return real
    for cand in ('index.php', 'index.html', 'index.htm'):
        if cand in names:
            return cand
    for n in sorted(names):
        if Path(n).suffix.lower() in ('.php', '.html', '.htm') and '/' not in n:
            return n
    return None


@router.get("/preview/{filename}/file")
def preview_get_file(filename: str, path: str = Query(...), raw: int = Query(0)):
    """
    Читает содержимое файла из zip.
    raw=1 — отдать как есть (для панели кода).
    raw=0 — отдать с правильным MIME (для ресурсов превью в iframe);
            для CSS дополнительно переписывает url(...) на этот же эндпоинт.
    """
    import posixpath
    import urllib.parse
    import re as _re
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(400, "Invalid filename")
    if not _scan_preview_validate_inner_path(path):
        raise HTTPException(400, "Invalid path")
    target = _resolve_preview_target(filename)
    with zipfile.ZipFile(target, 'r') as zf:
        names = _zip_names(zf)
        # Резолвим: точное имя → нормализованное ('./assets/x' → 'assets/x')
        real = path if path in names else _resolve_zip_path(path, '', names)
        if real is None:
            raise HTTPException(404, f"File not found in zip: {path}")
        data = zf.read(real)

    ext = Path(real).suffix.lower()
    text_exts = {'.php', '.html', '.htm', '.css', '.js', '.json', '.txt', '.xml'}

    # Панель кода — всегда plain text
    if raw and ext in text_exts:
        return Response(content=data.decode('utf-8', errors='replace'),
                        media_type='text/plain; charset=utf-8')

    # CSS для превью: правильный MIME + переписываем url(...) (фоны, шрифты)
    if ext == '.css':
        css = data.decode('utf-8', errors='replace')
        css_dir = posixpath.dirname(real)
        base = f"/api/preview/{urllib.parse.quote(filename)}/file?path="

        def _replace_url(m):
            inner = m.group(1).strip().strip('\'"')
            if inner.startswith(('http', '//', 'data:', '#')):
                return m.group(0)
            resolved = _resolve_zip_path(inner, css_dir, names)
            if not resolved:
                return m.group(0)
            return f'url("{base}{urllib.parse.quote(resolved)}")'

        css = _re.sub(r'url\(\s*([^)]+?)\s*\)', _replace_url, css)
        return Response(content=css, media_type='text/css; charset=utf-8')

    mime = _MIME_BY_EXT.get(ext)
    if mime:
        return Response(content=data, media_type=mime)
    if ext in text_exts:
        return Response(content=data.decode('utf-8', errors='replace'),
                        media_type='text/plain; charset=utf-8')
    return Response(content=data, media_type='application/octet-stream')


class PreviewSaveBody(BaseModel):
    path: str
    content: str


@router.put("/preview/{filename}/file")
def preview_save_file(filename: str, body: PreviewSaveBody):
    """
    Сохраняет правки в файл внутри zip из outputs.
    Пересобирает zip атомарно (tmp + os.replace), остальные файлы не трогает.
    """
    import os
    import tempfile
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(400, "Invalid filename")
    if not _scan_preview_validate_inner_path(body.path):
        raise HTTPException(400, "Invalid path")
    target = STORAGE / 'outputs' / filename
    if not target.exists():
        raise HTTPException(404, "Not found")

    new_bytes = body.content.encode('utf-8')

    fd, tmp_path = tempfile.mkstemp(suffix='.zip', dir=str(target.parent))
    os.close(fd)
    try:
        replaced = False
        with zipfile.ZipFile(target, 'r') as zin, \
             zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == body.path:
                    zout.writestr(item.filename, new_bytes)
                    replaced = True
                else:
                    zout.writestr(item, zin.read(item.filename))
        if not replaced:
            raise HTTPException(404, f"File not found in zip: {body.path}")
        os.replace(tmp_path, target)
    except HTTPException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(500, f"Save failed: {e}")

    return {"success": True, "path": body.path, "size": len(new_bytes)}


@router.get("/preview/{filename}/render")
def preview_render_file(filename: str, path: str = Query("index.php")):
    """
    Рендерит HTML файл из zip с подменой путей к ресурсам.
    PHP теги остаются как текст (не выполняются).
    Резолвит './x', '../x', абсолютные '/x' и пути с '?v=' относительно папки самого файла.

    path необязателен (по умолчанию index.php): универсальный виджет ленда
    шлёт AJAX на текущий URL (render) со своими параметрами и БЕЗ path —
    раньше это давало 422. Лишние query-параметры (sub_id_*, _update_tokens)
    FastAPI игнорирует.
    """
    import posixpath
    import re as _re
    import urllib.parse
    from fastapi.responses import HTMLResponse
    if '/' in filename or '\\' in filename or '..' in filename:
        raise HTTPException(400, "Invalid filename")
    path = path or "index.php"
    if not _scan_preview_validate_inner_path(path):
        raise HTTPException(400, "Invalid path")
    target = _resolve_preview_target(filename)
    with zipfile.ZipFile(target, 'r') as zf:
        names = _zip_names(zf)
        real = _entry_path(path, names)  # точка входа (+fallback на index.*)
        if real is None:
            raise HTTPException(404, f"File not found: {path}")
        html = zf.read(real).decode('utf-8', errors='replace')

    # ВАЖНО: серверные PHP-блоки ДО <!DOCTYPE>/<html> (require_once init.php,
    # проверка clickid и т.п.) при исполнении на сервере не дают вывода. В превью
    # же они превратились бы в видимый текст ПЕРЕД doctype → браузер уходит в
    # quirks mode → вёрстка ломается, страница «вытягивается». Поэтому вырезаем
    # всё до первого <!doctype/<html (там только server-only PHP и пробелы).
    m_doc = _re.search(r'<!doctype\b|<html\b', html, _re.IGNORECASE)
    if m_doc and m_doc.start() > 0:
        head = html[:m_doc.start()]
        # срезаем только если в «голове» нет видимой разметки (лишь PHP/пробелы)
        if '<' not in _re.sub(r'<\?.*?\?>', '', head, flags=_re.DOTALL).replace('<?', ''):
            html = html[m_doc.start():]

    html_dir = posixpath.dirname(real)
    base = f"/api/preview/{urllib.parse.quote(filename)}/file?path="
    _SKIP = ('http', '//', 'data:', '#', 'mailto:', 'tel:', 'javascript:', '{', '<?')

    # src/href/data-src/data-bg
    def replace_asset(m):
        attr, quote, val = m.group(1), m.group(2), m.group(3)
        if val.startswith(_SKIP):
            return m.group(0)
        resolved = _resolve_zip_path(val, html_dir, names)
        if not resolved:
            return m.group(0)
        return f'{attr}={quote}{base}{urllib.parse.quote(resolved)}{quote}'

    html = _re.sub(
        r'(src|href|data-src|data-bg|poster)=(["\'])([^"\']+)(["\'])',
        replace_asset, html
    )

    # srcset="img1.png 1x, img2.png 2x"
    def replace_srcset(m):
        quote, val = m.group(1), m.group(2)
        parts = []
        for item in val.split(','):
            item = item.strip()
            if not item:
                continue
            bits = item.split()
            url = bits[0]
            if not url.startswith(_SKIP):
                resolved = _resolve_zip_path(url, html_dir, names)
                if resolved:
                    bits[0] = base + urllib.parse.quote(resolved)
            parts.append(' '.join(bits))
        return f'srcset={quote}{", ".join(parts)}{quote}'

    html = _re.sub(r'srcset=(["\'])([^"\']+)\1', replace_srcset, html)

    # инлайн-стили: style="background: url('img.png')"
    def replace_inline_url(m):
        inner = m.group(1).strip().strip('\'"')
        if inner.startswith(_SKIP):
            return m.group(0)
        resolved = _resolve_zip_path(inner, html_dir, names)
        if not resolved:
            return m.group(0)
        return f"url('{base}{urllib.parse.quote(resolved)}')"

    html = _re.sub(r'url\(\s*([^)]+?)\s*\)', replace_inline_url, html)

    # Нерезолвенные шаблонные плейсхолдеры ({_from_file:...} и пр.) — это макросы,
    # которые на боевом сервере подставляет orders_api/init.php (виджет, маска
    # формы, backfix). В превью PHP-инфраструктуры нет → браузер пытался грузить
    # литеральный URL "{_from_file:...}" (NS_ERROR_CORRUPTED_CONTENT) и валился с
    # SyntaxError. Убираем такие <script>/<link> и глушим оставшиеся src/href,
    # чтобы превью не сыпало ошибками (сам виджет покажется только на сервере).
    html = _re.sub(
        r'<script\b[^>]*\bsrc=(["\'])\{[^"\']*\}\1[^>]*>\s*</script\s*>',
        '<!-- preview: скрипт ленда резолвится на сервере (orders_api) -->',
        html, flags=_re.IGNORECASE)
    html = _re.sub(
        r'<script\b[^>]*\bsrc=(["\'])\{[^"\']*\}\1[^>]*>',
        '<!-- preview: скрипт ленда резолвится на сервере (orders_api) -->',
        html, flags=_re.IGNORECASE)
    html = _re.sub(r'\b(src|href)=(["\'])\{[^"\']*\}\2', r'\1=\2#\2',
                   html, flags=_re.IGNORECASE)

    # PHP теги показываем как текст (экранируем)
    html = _re.sub(r'<\?(?:php|=)(.*?)\?>',
                   lambda m: f'<span style="background:#2d1b00;color:#f59e0b;font-size:11px;padding:1px 4px;border-radius:3px;font-family:monospace">&lt;?php{m.group(1)}?&gt;</span>',
                   html, flags=_re.DOTALL)

    return HTMLResponse(content=html)

@router.post("/optimize/scan")
async def optimize_scan_endpoint(file: UploadFile = File(...)):
    """
    Шаг 1: сканирование архива — возвращает список PNG/JPG для конвертации.
    Файл сохраняется, upload_id используется в /optimize/run.
    """
    upload = await save_upload(file)
    scan = runners.run_scan_optimize(str(upload))
    return {
        "upload_id": upload.name,
        "scan": scan,
    }


@router.post("/optimize/run")
async def optimize_run_endpoint(upload_id: str = Form(...)):
    """
    Шаг 2: конвертация. Принимает upload_id из /optimize/scan.
    Возвращает готовый zip с WebP.
    """
    if any(c in upload_id for c in ("/", "\\", "..")):
        raise HTTPException(400, "Invalid upload_id")
    upload_path = STORAGE / "uploads" / upload_id
    if not upload_path.exists():
        raise HTTPException(404, f"Upload not found: {upload_id}")

    out_path, capture = runners.run_optimize(str(upload_path))
    return {
        "success": out_path is not None,
        "result_url": output_relative_url(out_path) if out_path else None,
        "result_name": Path(out_path).name if out_path else None,
        "log": capture.to_dicts(),
    }
