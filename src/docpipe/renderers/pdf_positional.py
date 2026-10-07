from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from docpipe.ir import BlockType, Document


def _font_name() -> str:
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    ]
    for path in candidates:
        if path.exists():
            name = "DocpipeDejaVu"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(path)))
            return name
    raise RuntimeError("Не найден свободный шрифт с кириллицей: DejaVu Sans")


def render(document: Document, out: Path, base_dir: Path | None = None) -> Path:
    """Позиционно пересобрать страницы из IR.

    Режим намеренно не является основным: печати, подписи и прочая графика,
    не попавшие в figure-блоки, могут быть потеряны.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    font = _font_name()
    base = base_dir or out.parent
    pdf = canvas.Canvas(str(out))
    for page in document.pages:
        pdf.setPageSize((page.width_pt, page.height_pt))
        for block in sorted(page.blocks, key=lambda item: item.order):
            x = block.bbox.x0
            width = max(1.0, block.bbox.x1 - block.bbox.x0)
            height = max(1.0, block.bbox.y1 - block.bbox.y0)
            y = page.height_pt - block.bbox.y1
            if block.type == BlockType.FIGURE and block.figure:
                path = base / block.figure.file
                if path.exists():
                    pdf.drawImage(str(path), x, y, width=width, height=height, preserveAspectRatio=True, anchor="sw")
                continue
            if not block.text:
                continue
            font_size = min(12.0, max(6.0, height * 0.65))
            pdf.setFont(font, font_size)
            for line_no, line in enumerate(block.text.splitlines() or [""]):
                pdf.drawString(x, y + height - font_size * (line_no + 1), line[:500])
        pdf.showPage()
    pdf.save()
    return out
