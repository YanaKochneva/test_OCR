from __future__ import annotations

from html import escape
from pathlib import Path

from docpipe.ir import BlockType, Document
from docpipe.renderers.html import render as render_html


def render(document: Document, out: Path, source_dir: Path | None = None) -> Path:
    """Создать PDF из потокового HTML.

    Сначала используется WeasyPrint. При его отсутствии применяется ReportLab
    Platypus. Исходный скан в этот PDF не переносится: это именно потоковая
    пересборка документа.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    html_path = out.with_suffix(".flow.html")
    render_html(document, html_path, self_contained=False)

    try:
        from weasyprint import HTML

        HTML(filename=str(html_path), base_url=str(source_dir or out.parent)).write_pdf(str(out))
        html_path.unlink(missing_ok=True)
        return out
    except ImportError:
        pass

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from docpipe.renderers.fonts import find_bold_font, require_font

    styles = getSampleStyleSheet()
    # The default Helvetica font cannot represent the Russian OCR text.
    regular_font, bold_font = "DocpipeFlow", "DocpipeFlowBold"
    for name, path in ((regular_font, require_font()),
                       (bold_font, find_bold_font() or require_font())):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
    pdfmetrics.registerFontFamily(regular_font, normal=regular_font, bold=bold_font,
                                  italic=regular_font, boldItalic=bold_font)
    for style in styles.byName.values():
        if hasattr(style, "fontName"):
            style.fontName = bold_font if "Bold" in style.fontName else regular_font
    story = []
    base = source_dir or out.parent
    for page in document.pages:
        for block in sorted(page.blocks, key=lambda item: item.order):
            if block.type == BlockType.FIGURE and block.figure:
                image = base / block.figure.file
                if image.exists():
                    story.append(Image(str(image), width=6.2 * inch, height=4.0 * inch, kind="proportional"))
                    story.append(Spacer(1, 6))
                continue
            if block.type == BlockType.TABLE and block.table and block.table.cells:
                rows: dict[int, dict[int, str]] = {}
                for cell in block.table.cells:
                    rows.setdefault(cell.row, {})[cell.col] = cell.text
                data = [[rows.get(r, {}).get(c, "") for c in range(block.table.n_cols)] for r in range(block.table.n_rows)]
                if data:
                    story.append(Table(data, repeatRows=1,
                                       style=[("FONTNAME", (0, 0), (-1, -1), regular_font)]))
                    story.append(Spacer(1, 8))
                continue
            if block.text:
                style = styles["Heading1"] if block.type == BlockType.HEADING else styles["BodyText"]
                # OCR content is plain text, never ReportLab's XML-like markup.
                # Escape exactly once so addresses, '<RUB', '&lt;' and tags stay literal.
                text = "<br/>".join(escape(line, quote=False) for line in block.text.splitlines())
                story.append(Paragraph(text, style))
                story.append(Spacer(1, 6))
    SimpleDocTemplate(str(out), pagesize=A4).build(story)
    html_path.unlink(missing_ok=True)
    return out
