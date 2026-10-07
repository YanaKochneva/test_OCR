from __future__ import annotations

from pathlib import Path

from docpipe.ir import BlockType, Document


def render(document: Document, out: Path, base_dir: Path | None = None) -> Path:
    """Создать DOCX из IR в порядке чтения."""
    from docx import Document as WordDocument
    from docx.shared import Inches

    out.parent.mkdir(parents=True, exist_ok=True)
    base = base_dir or out.parent
    doc = WordDocument()
    for page in document.pages:
        for block in sorted(page.blocks, key=lambda item: item.order):
            if block.type == BlockType.HEADING:
                doc.add_heading(block.text or "", level=min(block.level or 1, 9))
            elif block.type == BlockType.LIST_ITEM:
                doc.add_paragraph(block.text or "", style="List Bullet")
            elif block.type == BlockType.TABLE and block.table:
                table = doc.add_table(rows=block.table.n_rows, cols=block.table.n_cols)
                for cell in block.table.cells:
                    table.cell(cell.row, cell.col).text = cell.text
            elif block.type == BlockType.FIGURE and block.figure:
                path = base / block.figure.file
                if path.exists():
                    doc.add_picture(str(path), width=Inches(6.0))
                if block.figure.caption_id:
                    doc.add_paragraph(block.figure.caption_id)
            elif block.text:
                doc.add_paragraph(block.text)
        doc.add_page_break()
    if doc.paragraphs and not doc.paragraphs[-1].text:
        pass
    doc.save(str(out))
    return out
