"""Source-page image loading shared by OCR review and visual tools."""

from pathlib import Path

from PIL import Image, ImageOps

from docpipe.config import LimitsConfig
from docpipe.ir import Page


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
                    from docpipe.render.raster import bounded_dpi

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
