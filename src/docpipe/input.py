from __future__ import annotations

import hashlib
import io
import math
import pypdfium2 as pdfium
import re
from PIL import Image, ImageOps
from dataclasses import dataclass
from docpipe.config import AppConfig, LimitsConfig, TextLayerConfig
from docpipe.errors import InputError
from docpipe.ir import Page, TextLayerStatus
from pathlib import Path


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
            frame = ImageOps.exif_transpose(image).convert("RGB")
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


_REPLACEMENT = "\ufffd"
_CYR = re.compile(r"[\u0400-\u052f]")
_LAT = re.compile(r"[A-Za-z\u00c0-\u024f]")
_WORD = re.compile(r"\S+")


def _script_count(text: str, scripts: list[str]) -> int:
    n = 0
    for ch in text:
        if "cyrillic" in scripts and _CYR.match(ch):
            n += 1
            continue
        if "latin" in scripts and _LAT.match(ch):
            n += 1
    return n


def _mixed_words(text: str) -> int:
    total = 0
    for word in _WORD.findall(text):
        has_cyr = bool(_CYR.search(word))
        has_lat = bool(_LAT.search(word))
        if has_cyr and has_lat:
            total += 1
    return total


def classify_text(text: str, config: TextLayerConfig) -> TextLayerStatus:
    if not text or not text.strip():
        return TextLayerStatus.ABSENT
    if len(text.strip()) < config.min_text_length:
        return TextLayerStatus.GARBAGE
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return TextLayerStatus.ABSENT
    expected = _script_count(text, config.expected_scripts) / len(chars)
    controls = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t") / max(1, len(text))
    replacements = text.count(_REPLACEMENT) / max(1, len(text))
    words = _WORD.findall(text)
    mixed = _mixed_words(text) / max(1, len(words))
    if (
        expected < config.min_expected_script_ratio
        or controls > config.max_control_ratio
        or replacements > config.max_replacement_ratio
        or mixed > config.max_mixed_script_word_ratio
    ):
        return TextLayerStatus.GARBAGE
    return TextLayerStatus.VALID


def analyze_pdf(path: Path, config: TextLayerConfig) -> list[TextLayerStatus]:
    doc = pdfium.PdfDocument(str(path))
    result: list[TextLayerStatus] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
                result.append(classify_text(text, config))
            finally:
                textpage.close()
            page.close()
    finally:
        doc.close()
    return result


@dataclass(frozen=True)
class RasterPage:
    image: Image.Image
    width_pt: float
    height_pt: float
    rotation: int
    dpi: int


def bounded_dpi(width_pt: float, height_pt: float, dpi: int, limits: LimitsConfig) -> int:
    """Apply the pixel budget before PDFium allocates a page bitmap."""
    if not all(math.isfinite(v) and v > 0 for v in (width_pt, height_pt, dpi)):
        raise ValueError("Invalid page dimensions or DPI")
    allowed = min(
        float(dpi),
        72 * math.sqrt(limits.max_pixels_per_page / (width_pt * height_pt)),
        72 * limits.max_dimension_px / max(width_pt, height_pt),
    )
    selected = math.floor(allowed)
    while selected >= 1:
        w, h = math.ceil(width_pt * selected / 72), math.ceil(height_pt * selected / 72)
        if w * h <= limits.max_pixels_per_page and max(w, h) <= limits.max_dimension_px:
            return selected
        selected -= 1
    raise ValueError("Page cannot fit configured raster limits even at 1 DPI")


def rasterize_page(
    page: pdfium.PdfPage, dpi: int, limits: LimitsConfig | None = None
) -> RasterPage:
    width_pt, height_pt = page.get_size()
    rotation = page.get_rotation() or 0
    dpi = bounded_dpi(width_pt, height_pt, dpi, limits or LimitsConfig())
    scale = dpi / 72.0
    bitmap = page.render(scale=scale, rotation=0, fill_to_stroke=True)
    try:
        image = bitmap.to_pil()
        try:
            # Detach from PDFium-owned memory exactly once, then close the bitmap.
            rgb = image.copy() if image.mode == "RGB" else image.convert("RGB")
        finally:
            image.close()
    finally:
        bitmap.close()
    return RasterPage(image=rgb, width_pt=width_pt, height_pt=height_pt, rotation=rotation, dpi=dpi)


def load_page_image(
    source: str | Path, page: Page, dpi: int, limits: LimitsConfig | None = None
) -> Image.Image:
    """Load an oriented source page while respecting the raster budget."""
    source = Path(source)
    if source.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(source) as pdf:
            pdf_page = pdf[page.index]
            try:
                rotation = (page.rotation - pdf_page.get_rotation()) % 360
                if limits is not None:

                    dpi = bounded_dpi(*pdf_page.get_size(), dpi, limits)
                bitmap = pdf_page.render(scale=dpi / 72, rotation=rotation)
                try:
                    image = bitmap.to_pil()
                    try:
                        return image.convert("RGB")
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
    with Image.open(source) as opened:
        opened.seek(page.index)
        image = ImageOps.exif_transpose(opened).convert("RGB")
    if limits is not None:
        scale = min(
            1,
            (limits.max_pixels_per_page / (image.width * image.height)) ** 0.5,
            limits.max_dimension_px / max(image.size),
        )
        if scale < 1:
            image.thumbnail((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    if page.rotation % 360:
        rotated = image.rotate(-page.rotation, expand=True)
        image.close()
        image = rotated
    return image
