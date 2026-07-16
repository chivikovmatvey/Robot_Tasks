"""Видео-пайплайн VSL: mp4/m3u8 → HLS (360/480/720p) + постер → архив.

Повторяет prepare-video.sh (эталонный скрипт техотдела): три качества HLS,
master.m3u8, постер webp с 1-й секунды. В итоговый архив попадает ТОЛЬКО
содержимое папки видео (promo/ + video-poster.webp) — его заливают на CDN
в папку с именем архива, поэтому имя архива = имя папки на сервере и
подставляется в ссылки config.php (см. vsl.update_video_links).

Долгая работа (ffmpeg) идёт в фоне; статус — poll через job_status().
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

log = logging.getLogger("session")

BASE_DIR = Path(__file__).resolve().parents[1]
SESSIONS_DIR = BASE_DIR / "storage" / "sessions"

_FFMPEG_TIMEOUT = 60 * 60  # предел на один проход ffmpeg (час — видео бывают длинные)

# Параметры качеств — 1-в-1 из prepare-video.sh.
_RENDITIONS = [
    # (name, scale, crf, profile, level, audio_bitrate, v_bitrate, maxrate, bufsize)
    ("360p", "640:360", "28", "baseline", "3.0", "64k", "400k", "450k", "800k"),
    ("480p", "854:480", "26", "main", "3.1", "96k", "800k", "900k", "1600k"),
    ("720p", "1280:720", "24", "main", "4.0", "128k", "1500k", "1650k", "3000k"),
]

_MASTER_M3U8 = """#EXTM3U
#EXT-X-VERSION:3

#EXT-X-STREAM-INF:BANDWIDTH=464000,RESOLUTION=640x360,CODECS="avc1.42c01e,mp4a.40.2"
360p/playlist.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=896000,RESOLUTION=854x480,CODECS="avc1.4d401f,mp4a.40.2"
480p/playlist.m3u8

#EXT-X-STREAM-INF:BANDWIDTH=1628000,RESOLUTION=1280x720,CODECS="avc1.4d4028,mp4a.40.2"
720p/playlist.m3u8
"""

# ── реестр фоновых задач ─────────────────────────────────────────
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _key(sid: str, lid: str) -> str:
    return f"{sid}/{lid}"


def _work_dir(sid: str, lid: str) -> Path:
    return SESSIONS_DIR / sid / "vsl" / re.sub(r"[^\w.-]", "_", lid)


def sanitize_archive_name(name: str) -> str:
    """Имя архива = имя папки на CDN: [a-z0-9_-], без пробелов и слэшей."""
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", (name or "").strip()).strip("_").lower()
    return name or "vsl_video"


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg не найден — установи ffmpeg для конвертации видео")
    return path


def _run_ffmpeg(args: list[str], step: str) -> None:
    proc = subprocess.run(args, capture_output=True, text=True,
                          timeout=_FFMPEG_TIMEOUT)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-800:]
        raise RuntimeError(f"{step}: ffmpeg завершился с ошибкой:\n{tail}")


def job_status(sid: str, lid: str) -> dict:
    """Текущее состояние задачи + сохранённый архив (если уже готов)."""
    with _lock:
        job = dict(_jobs.get(_key(sid, lid)) or {})
    from services.session import get_manager
    s = get_manager().get(sid)
    ls = s.landers.get(lid) if s else None
    ap = (ls.adapt_params or {}) if ls else {}
    name = ap.get("vsl_archive_name") or ""
    zip_ok = bool(name) and archive_path(sid, lid, name).exists()
    return {
        "state": job.get("state") or ("done" if zip_ok else "idle"),
        "steps": job.get("steps", []),
        "error": job.get("error"),
        "archive_name": name or job.get("archive_name") or "",
        "archive_ready": zip_ok,
        "archive_size": archive_path(sid, lid, name).stat().st_size if zip_ok else None,
        "suggested_name": _suggest(sid, lid) if not name else name,
    }


def _suggest(sid: str, lid: str) -> str:
    try:
        from services.vsl import suggest_archive_name
        return suggest_archive_name(sid, lid)
    except Exception:  # noqa: BLE001
        return "vsl_video"


def archive_path(sid: str, lid: str, name: str) -> Path:
    return _work_dir(sid, lid) / f"{sanitize_archive_name(name)}.zip"


def start_job(sid: str, lid: str, *, upload: Optional[bytes] = None,
              m3u8_url: Optional[str] = None) -> dict:
    """Запускает фоновую подготовку видео. Источник — mp4-файл ИЛИ ссылка m3u8."""
    _ffmpeg()  # ранняя проверка, чтобы ошибка ушла в ответ, а не в фон
    if not upload and not (m3u8_url or "").strip():
        raise ValueError("Нужен mp4-файл или ссылка на m3u8")
    k = _key(sid, lid)
    with _lock:
        if (_jobs.get(k) or {}).get("state") == "running":
            raise ValueError("Подготовка видео уже идёт")
        _jobs[k] = {"state": "running", "steps": [], "error": None,
                    "started_at": time.time()}

    work = _work_dir(sid, lid)
    work.mkdir(parents=True, exist_ok=True)
    src = work / "video_src.mp4"
    if upload:
        src.write_bytes(upload)

    threading.Thread(
        target=_run_job, args=(sid, lid, src, (m3u8_url or "").strip() or None),
        daemon=True, name=f"vsl-video-{sid}-{lid}",
    ).start()
    return {"state": "running"}


def _step(k: str, msg: str) -> None:
    with _lock:
        job = _jobs.get(k)
        if job is not None:
            job["steps"].append(msg)
    log.info("VSL video [%s]: %s", k, msg)


def _run_job(sid: str, lid: str, src: Path, m3u8_url: Optional[str]) -> None:
    k = _key(sid, lid)
    try:
        ff = _ffmpeg()
        work = _work_dir(sid, lid)

        if m3u8_url:
            _step(k, "Скачиваю m3u8 и конвертирую в mp4…")
            # Сначала быстрый путь без перекодирования; если поток несовместим
            # с mp4 — полное перекодирование.
            try:
                _run_ffmpeg([ff, "-y", "-i", m3u8_url, "-c", "copy",
                             "-bsf:a", "aac_adtstoasc", str(src)], "m3u8→mp4 (copy)")
            except RuntimeError:
                _step(k, "Прямое копирование не удалось — перекодирую…")
                _run_ffmpeg([ff, "-y", "-i", m3u8_url, "-c:v", "libx264",
                             "-preset", "veryfast", "-c:a", "aac", str(src)],
                            "m3u8→mp4 (recode)")
        if not src.exists() or src.stat().st_size == 0:
            raise RuntimeError("Исходное видео не получено")

        # Выход строго как у prepare-video.sh: <out>/promo/{360p,480p,720p,master.m3u8}
        # + <out>/video-poster.webp; в архив попадает содержимое <out>.
        out_dir = work / "video"
        shutil.rmtree(out_dir, ignore_errors=True)
        promo = out_dir / "promo"

        for name, scale, crf, profile, level, ab, vb, maxr, bufs in _RENDITIONS:
            _step(k, f"Конвертирую {name}…")
            rdir = promo / name
            rdir.mkdir(parents=True, exist_ok=True)
            _run_ffmpeg([
                ff, "-y", "-i", str(src),
                "-vf", f"scale={scale}",
                # 8-бит 4:2:0 обязателен для baseline/main (10-бит/4:4:4
                # исходники иначе роняют кодек «Invalid argument»).
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", crf,
                "-profile:v", profile, "-level", level,
                "-c:a", "aac", "-b:a", ab, "-ar", "44100",
                "-b:v", vb, "-maxrate", maxr, "-bufsize", bufs,
                "-hls_time", "4", "-hls_list_size", "0",
                "-hls_playlist_type", "vod",
                "-hls_segment_filename", str(rdir / "segment%03d.ts"),
                "-g", "48", "-sc_threshold", "0",
                "-x264opts", "keyint=48:min-keyint=48:no-scenecut",
                str(rdir / "playlist.m3u8"),
            ], name)

        _step(k, "Пишу master.m3u8…")
        (promo / "master.m3u8").write_text(_MASTER_M3U8, encoding="utf-8")

        _step(k, "Генерирую постер…")
        _run_ffmpeg([
            ff, "-y", "-i", str(src), "-ss", "00:00:01", "-vframes", "1",
            "-vf", "scale=iw*sar:ih,setsar=1",
            "-c:v", "libwebp", "-quality", "85", "-compression_level", "4",
            str(out_dir / "video-poster.webp"),
        ], "poster")

        # Имя архива: сохранённое ранее, иначе автопредложение.
        from services.session import get_manager
        mgr = get_manager()
        s, ls = mgr._get_lander(sid, lid)
        name = sanitize_archive_name(
            (ls.adapt_params or {}).get("vsl_archive_name") or _suggest(sid, lid))

        _step(k, f"Собираю архив {name}.zip…")
        _build_archive(out_dir, archive_path(sid, lid, name))

        # Ссылки в config.php → на папку с именем архива.
        _step(k, "Обновляю ссылки видео в config.php…")
        from services.vsl import update_video_links
        links = update_video_links(sid, lid, name)

        s, ls = mgr._get_lander(sid, lid)  # перечитать после update_video_links
        ls.adapt_params = {**(ls.adapt_params or {}), "vsl_archive_name": name}
        mgr._save(s)

        with _lock:
            _jobs[k].update(state="done", archive_name=name, links=links)
        _step(k, "Готово")
    except Exception as e:  # noqa: BLE001
        log.exception("VSL video [%s]: сбой", k)
        with _lock:
            _jobs[k].update(state="error", error=str(e))


def _build_archive(out_dir: Path, target: Path) -> None:
    """Zip СОДЕРЖИМОГО out_dir (promo/, video-poster.webp) без родительской папки.

    .ts-сегменты уже сжаты кодеком — храним без компрессии (быстро и не больше).
    """
    tmp = target.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for p in sorted(out_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(out_dir).as_posix())
    tmp.replace(target)
    # Старые архивы с другими именами подчищаем — актуален один.
    for old in target.parent.glob("*.zip"):
        if old != target:
            old.unlink(missing_ok=True)


def rename_archive(sid: str, lid: str, new_name: str) -> dict:
    """Переименовывает готовый архив и обновляет ссылки в config.php.

    Меняется только имя архива (= имя папки на сервере) — прочие части ссылок
    не трогаются."""
    from services.session import get_manager
    mgr = get_manager()
    s, ls = mgr._get_lander(sid, lid)
    old = (ls.adapt_params or {}).get("vsl_archive_name") or ""
    name = sanitize_archive_name(new_name)
    if not old:
        raise ValueError("Архив ещё не создан")
    old_path = archive_path(sid, lid, old)
    new_path = archive_path(sid, lid, name)
    if not old_path.exists():
        raise ValueError("Файл архива не найден — пересобери видео")
    if old_path != new_path:
        old_path.rename(new_path)

    from services.vsl import update_video_links
    links = update_video_links(sid, lid, name)

    s, ls = mgr._get_lander(sid, lid)
    ls.adapt_params = {**(ls.adapt_params or {}), "vsl_archive_name": name}
    mgr._save(s)
    log.info("VSL video [%s/%s]: архив переименован %s → %s", sid, lid, old, name)
    return {"archive_name": name, **links}
