"""
Хелперы для эндпоинтов: сохранение загруженного файла, отдача результата.
"""
import uuid
from pathlib import Path
from fastapi import UploadFile, HTTPException
from fastapi.responses import FileResponse

from utils.runners import STORAGE


async def save_upload(file: UploadFile, prefix: str = '') -> Path:
    """Сохраняет загруженный файл в storage/uploads с уникальным именем."""
    if not file.filename:
        raise HTTPException(400, "Empty filename")

    if not file.filename.lower().endswith('.zip'):
        raise HTTPException(400, "Only .zip files are accepted")

    uploads_dir = STORAGE / 'uploads'
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Уникальное имя чтобы параллельные загрузки не пересекались
    uid = uuid.uuid4().hex[:8]
    safe_name = file.filename.replace(' ', '_').replace('/', '_')
    target = uploads_dir / f"{uid}__{safe_name}"

    contents = await file.read()
    target.write_bytes(contents)
    return target


def file_download_response(out_path: str | Path) -> FileResponse:
    """Превращает путь в скачиваемый ответ."""
    p = Path(out_path)
    if not p.exists():
        raise HTTPException(500, f"Output file not found: {p.name}")
    return FileResponse(
        path=str(p),
        filename=p.name,
        media_type='application/zip',
    )


def output_relative_url(out_path: str | Path) -> str:
    """Возвращает /api/download/<filename> чтобы фронт мог скачать."""
    return f"/api/download/{Path(out_path).name}"
