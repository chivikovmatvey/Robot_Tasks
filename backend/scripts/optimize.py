"""
Optimize — конвертация PNG/JPG/JPEG -> WebP внутри zip-архива.
Обновляет пути в HTML/PHP/CSS/JS файлах.
GIF и видео не трогаем.
"""
import re
import io
import zipfile
from pathlib import Path
from datetime import datetime

from PIL import Image

CONVERT_EXT  = {'.png', '.jpg', '.jpeg'}
SKIP_NAMES   = {'favicon.png', 'favicon.jpg', 'favicon.jpeg'}
TEXT_EXT     = {'.html', '.htm', '.php', '.css', '.js', '.json'}
WEBP_QUALITY = 82

G     = '\033[92m'
B     = '\033[94m'
Y     = '\033[93m'
DIM   = '\033[2m'
RESET = '\033[0m'

def _ok(msg):   print(f"  {G}+{RESET} {msg}")
def _info(msg): print(f"  {B}-{RESET} {msg}")
def _warn(msg): print(f"  {Y}!{RESET} {msg}")


def _norm(name):
    return name.replace('\\', '/')


def _is_trash(name):
    n = _norm(name)
    return '/..' in n or '/./' in n or n in ('./', '.', '../')


def scan_zip(zip_path):
    images, skipped = [], []
    with zipfile.ZipFile(zip_path, 'r') as zf:
        seen = set()
        for info in zf.infolist():
            name = info.filename
            if _is_trash(name):
                continue
            norm = _norm(name).rstrip('/')
            if norm in seen:
                continue
            seen.add(norm)
            p = Path(_norm(name))
            ext = p.suffix.lower()
            if ext not in CONVERT_EXT:
                continue
            if p.name.lower() in SKIP_NAMES:
                skipped.append(name)
                continue
            images.append({'path': name, 'name': p.name, 'size_kb': round(info.file_size / 1024, 1)})
    return {'images': images, 'skipped': skipped, 'total': len(images)}


def _replace_refs(text, renamed):
    count = 0
    for old_name, new_name in renamed.items():
        new_text, n = re.subn(re.escape(old_name), new_name, text)
        text = new_text
        count += n
    return text, count


def process_zip(zip_path, out_dir=None):
    src  = Path(zip_path)
    ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
    stem = src.stem
    for marker in ('__clean__', '__injected__', '__optimized__'):
        stem = stem.split(marker)[0]

    dest = Path(out_dir) if out_dir else src.parent
    dest.mkdir(parents=True, exist_ok=True)
    out_path = dest / f"{stem}__optimized__{ts}.zip"

    stats   = {'converted': [], 'skipped': [], 'refs_updated': 0, 'files_changed': 0}
    renamed = {}

    with zipfile.ZipFile(zip_path, 'r') as zf_in:
        infos = zf_in.infolist()

        # Карта конвертации
        seen_scan = set()
        for info in infos:
            name = info.filename
            if _is_trash(name):
                continue
            norm = _norm(name).rstrip('/')
            if norm in seen_scan:
                continue
            seen_scan.add(norm)
            p   = Path(_norm(name))
            ext = p.suffix.lower()
            if ext not in CONVERT_EXT or p.name.lower() in SKIP_NAMES:
                continue
            renamed[p.name] = p.stem + '.webp'

        _info(f"Найдено для конвертации: {len(renamed)} файлов")

        seen_write = set()
        with zipfile.ZipFile(out_path, 'w', compression=zipfile.ZIP_DEFLATED) as zf_out:
            for info in infos:
                name = info.filename

                if _is_trash(name):
                    continue

                norm = _norm(name).rstrip('/')
                if norm in seen_write:
                    continue
                seen_write.add(norm)

                if name.endswith('/'):
                    zf_out.writestr(norm + '/', b'')
                    continue

                p   = Path(_norm(name))
                ext = p.suffix.lower()
                data = zf_in.read(info.filename)

                if ext in CONVERT_EXT and p.name.lower() not in SKIP_NAMES:
                    old_kb = round(len(data) / 1024, 1)
                    try:
                        img = Image.open(io.BytesIO(data))
                        if img.mode not in ('RGB', 'RGBA'):
                            img = img.convert('RGB')
                        buf = io.BytesIO()
                        img.save(buf, 'WEBP', quality=WEBP_QUALITY)
                        webp_data = buf.getvalue()
                        new_kb    = round(len(webp_data) / 1024, 1)
                        new_norm  = str(p.parent / (p.stem + '.webp')).replace('\\', '/')
                        if new_norm.startswith('./'):
                            new_norm = new_norm[2:]
                        zf_out.writestr(new_norm, webp_data)
                        saved = round(old_kb - new_kb, 1)
                        stats['converted'].append({'path': name, 'old_kb': old_kb, 'new_kb': new_kb, 'saved_kb': saved})
                        _ok(f"{p.name} -> {p.stem}.webp  {old_kb}KB -> {new_kb}KB (-{saved}KB)")
                    except Exception as e:
                        _warn(f"Ошибка {p.name}: {e}")
                        zf_out.writestr(norm, data)

                elif ext in TEXT_EXT and renamed:
                    try:
                        text = data.decode('utf-8', errors='replace')
                        new_text, n = _replace_refs(text, renamed)
                        zf_out.writestr(norm, new_text.encode('utf-8'))
                        if n > 0:
                            stats['refs_updated'] += n
                            stats['files_changed'] += 1
                            _info(f"{p.name}  {DIM}({n} замен){RESET}")
                    except Exception:
                        zf_out.writestr(norm, data)

                else:
                    # Пропускаем .webp если эта картинка уже будет конвертирована из PNG/JPG
                    base_name = p.name
                    if ext == '.webp' and base_name in [v for v in renamed.values()]:
                        continue
                    zf_out.writestr(norm, data)

    total_saved = sum(r['saved_kb'] for r in stats['converted'])
    _ok(f"Конвертировано: {len(stats['converted'])} файлов")
    _ok(f"Сэкономлено:    {round(total_saved, 1)} KB")
    _ok(f"Ссылок обновлено: {stats['refs_updated']} в {stats['files_changed']} файлах")

    return str(out_path), stats
