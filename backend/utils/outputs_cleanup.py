"""
Очистка storage/outputs от «сирот» — архивов, на которые не ссылается ни одна
живая сессия.

Ленды удаляют свои архивы сами (SessionManager.delete / delete_lander /
set_output), но остаются файлы, созданные до появления этой логики, и результаты
standalone-обработки из «инструментов» (api.py), у которых сессии нет вовсе.

    python -m utils.outputs_cleanup            # только показать
    python -m utils.outputs_cleanup --apply    # удалить

Плюс автоматически при старте сервера — см. cleanup_on_startup (main.py).

Свежие файлы (по умолчанию младше часа) не трогаем: они могут быть прямо сейчас
в работе у standalone-обработки.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("outputs-cleanup")

BACKEND_ROOT = Path(__file__).parent.parent.resolve()
STORAGE = BACKEND_ROOT / "storage"


def referenced_names(storage: Path = STORAGE) -> set[str]:
    """Имена архивов, занятые лендами живых сессий (sessions/*.json)."""
    names: set[str] = set()
    for meta in (storage / "sessions").glob("*.json"):
        try:
            raw = json.loads(meta.read_text())
        except Exception:  # noqa: BLE001
            # Битую мету считаем «занимающей всё» нельзя — но и молча ронять
            # чужие архивы тоже; просто пропускаем, отчёт покажет расхождение.
            continue
        for ld in (raw.get("landers") or {}).values():
            if ld.get("output_name"):
                names.add(ld["output_name"])
            for n in (ld.get("output_names") or []):
                if n:
                    names.add(n)
            for h in (ld.get("history") or []):
                if h.get("output_name"):
                    names.add(h["output_name"])
    return names


def find_orphans(storage: Path = STORAGE, min_age_hours: float = 1.0) -> list[Path]:
    outs = storage / "outputs"
    if not outs.is_dir():
        return []
    keep = referenced_names(storage)
    cutoff = time.time() - min_age_hours * 3600
    return sorted(
        p for p in outs.glob("*.zip")
        if p.name not in keep and p.stat().st_mtime < cutoff
    )


def cleanup(storage: Path = STORAGE, min_age_hours: float = 1.0,
            apply: bool = False) -> dict:
    orphans = find_orphans(storage, min_age_hours)
    freed = sum(p.stat().st_size for p in orphans)
    if apply:
        for p in orphans:
            p.unlink(missing_ok=True)
    return {"count": len(orphans), "bytes": freed, "applied": apply}


def cleanup_on_startup(storage: Path = STORAGE) -> dict:
    """Чистка сирот при старте сервера (вызывается из lifespan в main.py).

    OFFER_OUTPUTS_CLEANUP: on (по умолчанию) | off
    OFFER_OUTPUTS_MIN_AGE_HOURS: не трогать файлы младше N часов (по умолчанию 1)
    """
    raw = (os.environ.get("OFFER_OUTPUTS_CLEANUP") or "on").strip().lower()
    if raw in ("0", "false", "off", "no", "none"):
        return {"count": 0, "bytes": 0, "applied": False}
    try:
        min_age = float(os.environ.get("OFFER_OUTPUTS_MIN_AGE_HOURS") or 1.0)
    except ValueError:
        min_age = 1.0

    stats = cleanup(storage, min_age_hours=min_age, apply=True)
    if stats["count"]:
        log.warning("Очистка storage/outputs: удалено %d архивов без сессии, "
                    "освобождено %.2f ГБ", stats["count"], stats["bytes"] / 2**30)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Чистка storage/outputs от сирот")
    ap.add_argument("--apply", action="store_true", help="удалить (без флага — только показать)")
    ap.add_argument("--min-age-hours", type=float, default=1.0,
                    help="не трогать файлы младше N часов (по умолчанию 1)")
    ap.add_argument("--list", action="store_true", help="перечислить имена файлов")
    a = ap.parse_args()

    orphans = find_orphans(STORAGE, a.min_age_hours)
    total = sum(p.stat().st_size for p in orphans)
    if a.list:
        for p in orphans:
            print(f"  {p.name}  {p.stat().st_size / 2**20:.1f} МБ")
    print(f"Сирот: {len(orphans)}, объём: {total / 2**30:.2f} ГБ")
    if a.apply:
        for p in orphans:
            p.unlink(missing_ok=True)
        print(f"Удалено {len(orphans)} файлов, освобождено {total / 2**30:.2f} ГБ")
    else:
        print("Пробный прогон. Для удаления: --apply")


if __name__ == "__main__":
    main()
