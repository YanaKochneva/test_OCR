from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image

from docpipe.config import AppConfig
from docpipe.errors import InputError


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_input(path: Path, config: AppConfig) -> Path:
    """Проверить вход и вернуть PDF; изображения преобразуются во временный PDF."""
    if not path.is_file():
        raise InputError(f"Входной файл не найден: {path}")
    if path.stat().st_size > config.limits.max_file_mb * 1024 * 1024:
        raise InputError("Размер входного файла превышает лимит")
    if path.suffix.lower() == ".pdf":
        return path
    try:
        image = Image.open(path)
        frames = []
        for i in range(getattr(image, "n_frames", 1)):
            image.seek(i)
            frame = image.convert("RGB")
            if frame.width * frame.height > config.limits.max_pixels_per_page:
                raise InputError("Размер страницы изображения превышает лимит")
            frames.append(frame.copy())
    except InputError:
        raise
    except Exception as exc:
        raise InputError(f"Не удалось открыть изображение: {exc}") from exc
    # img2pdf является обязательной зависимостью production-сборки. Здесь намеренно
    # не используется Pillow.save(PDF), чтобы не вводить скрытое пережатие.
    try:
        import img2pdf  # type: ignore[import-not-found]
    except ImportError as exc:
        raise InputError("Для преобразования изображения нужен пакет img2pdf") from exc
    from tempfile import NamedTemporaryFile
    tmp = NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        with Path(tmp.name).open("wb") as out:
            out.write(img2pdf.convert([frame_to_bytes(f) for f in frames]))
    except Exception as exc:
        Path(tmp.name).unlink(missing_ok=True)
        raise InputError(f"Не удалось преобразовать изображение в PDF: {exc}") from exc
    return Path(tmp.name)


def frame_to_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
