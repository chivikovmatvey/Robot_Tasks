"""Нейро-редактор картинок ленда (GPT Image 2 через AITUNNEL).

Берёт картинку из адаптированного ленда, редактирует по промпту (напр.
«переведи текст на картинке на польский»), подгоняет результат под исходный
размер и заменяет файл в output-архиве — изменение сразу видно в превью.

Модель настраивается через IMAGE_EDIT_MODEL (по умолчанию gpt-image-2).
Качество low (1.8₽) / medium / high.
"""

from __future__ import annotations

import io
import logging
import os
from typing import Optional

from PIL import Image

from services.session import get_manager

log = logging.getLogger("image_edit")

DEFAULT_MODEL = "gpt-image-2"
ALLOWED_QUALITY = {"low", "medium", "high"}


def image_edit_model() -> str:
    return os.getenv("IMAGE_EDIT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def pick_gpt_size(image_bytes: bytes) -> str:
    """Ближайший поддерживаемый размер GPT Image 2 по пропорциям оригинала
    (1024x1024 | 1024x1536 | 1536x1024). Результат потом ресайзится под точный
    размер оригинала в replace_output_media."""
    try:
        w, h = Image.open(io.BytesIO(image_bytes)).size
    except Exception:  # noqa: BLE001
        return "1024x1024"
    ratio = w / h if h else 1.0
    if ratio >= 1.25:
        return "1536x1024"
    if ratio <= 0.8:
        return "1024x1536"
    return "1024x1024"


def _to_png(raw: bytes) -> bytes:
    """Конвертирует любую картинку в PNG (первый кадр, RGB/RGBA)."""
    img = Image.open(io.BytesIO(raw))
    if getattr(img, "is_animated", False):
        img.seek(0)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def edit_lander_media(sid: str, lid: str, path: str, prompt: str,
                      *, quality: str = "low",
                      ref_images: Optional[list[bytes]] = None) -> dict:
    """Редактирует картинку ленда по промпту и заменяет её в output-архиве.

    ref_images — доп. референсные картинки (напр. «замени продукт на тот, что
    на втором фото»). Передаются модели как доп. входы.
    Возвращает {path, dimensions, size, model, quality}.
    """
    from connectors.aitunnel import client_from_env
    if not (prompt or "").strip():
        raise ValueError("Пустой промпт")
    quality = quality if quality in ALLOWED_QUALITY else "low"

    mgr = get_manager()
    client = client_from_env()
    if client is None:
        raise ValueError("AITUNNEL не настроен — задай AITUNNEL_API_KEY в .env")

    # 1) Исходная картинка ленда.
    orig_bytes, fname = mgr.read_output_media(sid, lid, path)
    size = pick_gpt_size(orig_bytes)

    # GPT Image 2 принимает только png/jpeg/webp — конвертируем в PNG
    # (покрывает и gif/svg/bmp, и неверный mimetype). Берётся первый кадр.
    try:
        png_bytes = _to_png(orig_bytes)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Не открыть картинку: {e}")

    # Референсные картинки (второй+ вход) — тоже в PNG.
    extra: list[tuple[bytes, str, str]] = []
    for i, rb in enumerate(ref_images or []):
        if not rb:
            continue
        try:
            extra.append((_to_png(rb), f"ref{i + 1}.png", "image/png"))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Не открыть референс #{i + 1}: {e}")

    # 2) Редактирование через GPT Image 2.
    result_bytes = client.edit_image(
        png_bytes, prompt.strip(), model=image_edit_model(), size=size,
        quality=quality, filename="image.png", mime="image/png",
        extra_images=extra or None)

    # 3) Подгонка под оригинал + замена в архиве.
    info = mgr.replace_output_media(sid, lid, path, result_bytes)
    # 4) Закрепляем правку как замену + в image_map, чтобы повторная адаптация
    #    её не затёрла (output пересобирается из исходника).
    override = mgr.persist_media_override(sid, lid, path)
    log.info("Нейро-правка %s (сессия %s/%s, референсов: %d): %s",
             path, sid, lid, len(extra), info)
    return {**info, **override, "model": image_edit_model(),
            "quality": quality, "gpt_size": size}


# ── CLI ──────────────────────────────────────────────────────────
def _main() -> None:
    import argparse
    from pathlib import Path
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Нейро-правка картинки ленда (GPT Image 2).")
    ap.add_argument("sid")
    ap.add_argument("lid")
    ap.add_argument("path", help="Путь картинки в output-архиве, напр. product2.png")
    ap.add_argument("prompt")
    ap.add_argument("--quality", default="low", choices=sorted(ALLOWED_QUALITY))
    args = ap.parse_args()
    res = edit_lander_media(args.sid, args.lid, args.path, args.prompt, quality=args.quality)
    print(res)


if __name__ == "__main__":
    _main()
