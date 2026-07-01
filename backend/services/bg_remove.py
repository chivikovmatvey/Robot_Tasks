"""Удаление фона у изображений локально через rembg (offline, без ключей).

rembg использует U2-Net (onnxruntime). Модель скачивается один раз при первом
вызове (~170МБ) в ~/.u2net. Если rembg не установлен — даём понятную ошибку с
подсказкой поставить зависимость (см. requirements).

Качество краёв:
  - post_process_mask=True — чистит маску до резкого края (убирает «грязные»
    полупрозрачные пиксели по контуру, из-за которых была видимая тёмная кайма);
  - _defringe() — подкрашивает оставшиеся полупрозрачные краевые пиксели цветом
    ближайшего непрозрачного соседа (color-bleed), чтобы тёмный ореол не вылезал
    ни на каком фоне.

Модель настраивается через BG_REMOVE_MODEL (по умолчанию u2net — на тестах он
дал самый чистый край; isnet-general-use оказался и медленнее, и с тёмной каймой).

Результат — PNG с прозрачным фоном (альфа-канал).
"""

from __future__ import annotations

import io
import logging
import os

import numpy as np
from PIL import Image, ImageFilter

log = logging.getLogger("bg_remove")

_SESSION = None  # ленивый кэш rembg-сессии (модель грузится один раз)


def _model_name() -> str:
    return os.getenv("BG_REMOVE_MODEL", "u2net").strip() or "u2net"


def _get_session():
    global _SESSION
    if _SESSION is None:
        try:
            from rembg import new_session  # type: ignore
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError(
                "Не установлен rembg. Установи: pip install rembg onnxruntime "
                "(модель ~170МБ скачается при первом удалении фона)."
            ) from e
        _SESSION = new_session(_model_name())
    return _SESSION


def _erode_alpha(rgba: Image.Image) -> Image.Image:
    """Срезает 1px по контуру (эрозия альфы) — убирает самый внешний загрязнённый
    тёмным фоном ряд пикселей, из-за которого и остаётся видимая кайма. Продукт
    становится на 1px уже — на вёрстке незаметно, зато ореол уходит."""
    r, g, b, a = rgba.split()
    a = a.filter(ImageFilter.MinFilter(3))   # min по 3×3 = эрозия на 1px
    return Image.merge("RGBA", (r, g, b, a))


def _defringe(rgba: Image.Image, passes: int = 4) -> Image.Image:
    """Убирает тёмную кайму: подкрашивает полупрозрачные/прозрачные краевые
    пиксели цветом ближайшего непрозрачного соседа (color-bleed). Альфу не
    трогаем — меняется только RGB, поэтому контур остаётся тем же, но без
    тёмного ореола на цветном фоне.
    """
    arr = np.asarray(rgba.convert("RGBA")).astype(np.float32)
    rgb = arr[..., :3]
    alpha = arr[..., 3]
    known = alpha >= 250.0          # «надёжный» цвет продукта
    if not known.any() or known.all():
        return rgba

    filled = rgb.copy()
    mask = known.copy()
    shifts = ((1, 0), (-1, 0), (0, 1), (0, -1),
              (1, 1), (1, -1), (-1, 1), (-1, -1))
    for _ in range(max(1, passes)):
        if mask.all():
            break
        acc = np.zeros_like(filled)
        cnt = np.zeros(mask.shape, np.float32)
        for dy, dx in shifts:
            s = np.roll(np.roll(filled, dy, 0), dx, 1)
            sk = np.roll(np.roll(mask, dy, 0), dx, 1).astype(np.float32)
            acc += s * sk[..., None]
            cnt += sk
        todo = (~mask) & (cnt > 0)
        filled[todo] = acc[todo] / cnt[todo][..., None]
        mask[todo] = True

    out = np.dstack([filled, alpha]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def remove_background(data: bytes) -> bytes:
    """Удаляет фон у изображения. Вход — байты любого формата, выход — PNG (RGBA)."""
    if not data:
        raise ValueError("Пустое изображение")
    try:
        from rembg import remove  # type: ignore
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError(
            "Не установлен rembg. Установи: pip install rembg onnxruntime"
        ) from e

    # Нормализуем вход в PNG-байты (rembg принимает байты/PIL; покрываем gif/svg).
    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "is_animated", False):
            img.seek(0)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        src_png = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Не открыть картинку: {e}")

    # post_process_mask=True — резкий чистый контур (главный фикс тёмной каймы).
    out = remove(src_png, session=_get_session(), post_process_mask=True)
    try:
        res = Image.open(io.BytesIO(out)).convert("RGBA")
        res = _erode_alpha(res)         # срезаем загрязнённый внешний ряд (1px)
        res = _defringe(res)            # добиваем остаточный ореол по краю
        buf = io.BytesIO()
        res.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return out
