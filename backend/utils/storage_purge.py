"""
Очистка storage (вручную с главной или при остановке uvicorn).

OFFER_PURGE_ON_SHUTDOWN: off | temp | all — см. purge_storage_on_shutdown.

Папки assets/ и configs/ не трогаем.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def _rm_if_exists(path: Path) -> None:
    if path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _clear_dir_contents(d: Path) -> int:
    """Удаляет всё внутри каталога; сам каталог оставляет. Возвращает число удалённых элементов."""
    if not d.is_dir():
        return 0
    n = 0
    for child in list(d.iterdir()):
        _rm_if_exists(child)
        n += 1
    return n


def clear_storage(storage: Path, mode: str) -> dict:
    """
    mode:
      temp — uploads/ + output/
      all  — temp + outputs/ (все zip и вложенные папки)
    """
    m = (mode or "temp").strip().lower()
    if m not in ("temp", "all"):
        m = "temp"

    uploads = storage / "uploads"
    scratch = storage / "output"
    outputs = storage / "outputs"

    n_up = _clear_dir_contents(uploads)
    n_scratch = _clear_dir_contents(scratch)
    n_out = 0
    if m == "all":
        n_out = _clear_dir_contents(outputs)

    return {
        "scope": m,
        "uploads_removed": n_up,
        "output_cleared": n_scratch,
        "outputs_cleared": n_out,
    }


def purge_storage_on_shutdown(storage: Path) -> None:
    raw = (os.environ.get("OFFER_PURGE_ON_SHUTDOWN") or "temp").strip().lower()
    if raw in ("0", "false", "off", "no", "none"):
        return

    mode = raw if raw in ("temp", "all") else "temp"
    stats = clear_storage(storage, mode)
    msg = (
        f"[offer-processor] OFFER_PURGE_ON_SHUTDOWN={stats['scope']}: "
        f"uploads_removed={stats['uploads_removed']}, output_cleared={stats['output_cleared']}"
    )
    if stats["scope"] == "all":
        msg += f", outputs_cleared={stats['outputs_cleared']}"
    print(msg, flush=True)
