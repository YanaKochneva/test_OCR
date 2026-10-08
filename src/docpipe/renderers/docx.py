from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document as WordDocument
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt

from docpipe.ir import Block, BlockType, Document, Page

_EMU_PER_POINT = 12_700


def _set_page(section, page: Page) -> None:
    section.page_width = Pt(page.width_pt)
    section.page_height = Pt(page.height_pt)
    section.top_margin = Pt(0)
    section.bottom_margin = Pt(0)
    section.left_margin = Pt(0)
    section.right_margin = Pt(0)
    section.header_distance = Pt(0)
    section.footer_distance = Pt(0)


def _anchor_paragraph(doc: WordDocument):
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = Pt(1)
    return paragraph


def _add_textbox(
    doc: WordDocument,
    *,
    shape_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    z_index: int,
    text: str,
    font_size: float,
    bold: bool = False,
    border: bool = False,
) -> None:
    lines = text.splitlines() or [""]
    escaped_lines = []
    for line_index, line in enumerate(lines):
        if line_index:
            escaped_lines.append("<w:br/>")
        escaped_lines.append(f'<w:t xml:space="preserve">{escape(line)}</w:t>')
    bold_xml = "<w:b/>" if bold else ""
    font_half_points = max(10, round(font_size * 2))
    border_attrs = 'stroked="t" strokeweight="0.5pt"' if border else 'stroked="f"'
    namespaces = nsdecls("w") + ' xmlns:v="urn:schemas-microsoft-com:vml"'
    xml = f"""<w:pict {namespaces}>
      <v:shape id="{escape(shape_id)}" style="position:absolute;left:{x:.3f}pt;top:{y:.3f}pt;width:{max(width, 0.5):.3f}pt;height:{max(height, 0.5):.3f}pt;z-index:{z_index};mso-position-horizontal-relative:page;mso-position-vertical-relative:page;mso-wrap-style:none" {border_attrs} filled="f">
        <v:textbox inset="1pt,0,1pt,0"><w:txbxContent>
          <w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="{max(20, round(font_half_points * 12))}" w:lineRule="exact"/></w:pPr>
            <w:r><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="{font_half_points}"/>{bold_xml}</w:rPr>{''.join(escaped_lines)}</w:r>
          </w:p>
        </w:txbxContent></v:textbox>
      </v:shape>
    </w:pict>"""
    run = OxmlElement("w:r")
    run.append(parse_xml(xml))
    paragraph = _anchor_paragraph(doc)
    paragraph._p.append(run)


def _float_picture(
    picture,
    x: float,
    y: float,
    width: float,
    height: float,
    z_index: int,
) -> None:
    inline = picture._inline
    drawing = inline.getparent()
    anchor = OxmlElement("wp:anchor")
    for name, value in {
        "distT": "0", "distB": "0", "distL": "0", "distR": "0",
        "simplePos": "0", "relativeHeight": str(max(0, z_index)),
        "behindDoc": "0", "locked": "0", "layoutInCell": "1", "allowOverlap": "1",
    }.items():
        anchor.set(name, value)

    simple = OxmlElement("wp:simplePos")
    simple.set("x", "0")
    simple.set("y", "0")
    position_h = OxmlElement("wp:positionH")
    position_h.set("relativeFrom", "page")
    offset_h = OxmlElement("wp:posOffset")
    offset_h.text = str(round(x * _EMU_PER_POINT))
    position_h.append(offset_h)
    position_v = OxmlElement("wp:positionV")
    position_v.set("relativeFrom", "page")
    offset_v = OxmlElement("wp:posOffset")
    offset_v.text = str(round(y * _EMU_PER_POINT))
    position_v.append(offset_v)
    extent = inline.find(qn("wp:extent"))
    if extent is not None:
        extent.set("cx", str(round(width * _EMU_PER_POINT)))
        extent.set("cy", str(round(height * _EMU_PER_POINT)))
    effect_extent = inline.find(qn("wp:effectExtent"))
    wrap = OxmlElement("wp:wrapNone")
    doc_pr = inline.find(qn("wp:docPr"))
    frame = inline.find(qn("wp:cNvGraphicFramePr"))
    graphic = inline.find(qn("a:graphic"))
    graphic_extents = graphic.findall(".//" + qn("a:ext")) if graphic is not None else []
    if graphic_extents:
        graphic_extents[0].set("cx", str(round(width * _EMU_PER_POINT)))
        graphic_extents[0].set("cy", str(round(height * _EMU_PER_POINT)))
    ordered = [simple, position_h, position_v, extent]
    if effect_extent is not None:
        ordered.append(effect_extent)
    ordered.extend([wrap, doc_pr, frame, graphic])
    for element in ordered:
        if element is not None:
            if element.getparent() is inline:
                inline.remove(element)
            anchor.append(element)
    drawing.replace(inline, anchor)


def _fit_font_size(text: str, width: float, height: float, maximum: float) -> float:
    """Estimate a readable font size that fits inside a positioned text box."""
    available_width = max(1.0, width - 2.0)  # account for the textbox's 1pt insets
    lines = text.splitlines() or [""]

    def fits(size: float) -> bool:
        chars_per_line = max(1, int(available_width / (size * 0.52)))
        wrapped_lines = sum(max(1, (len(line) + chars_per_line - 1) // chars_per_line) for line in lines)
        return wrapped_lines * size * 1.2 <= max(1.0, height)

    # _add_textbox serializes font sizes in half-points and enforces a 5pt floor.
    low, high = 5.0, max(5.0, maximum)
    if not fits(low):
        return low
    for _ in range(16):
        middle = (low + high) / 2
        if fits(middle):
            low = middle
        else:
            high = middle
    return low


def _add_block(doc: WordDocument, block: Block, page: Page, base: Path) -> None:
    box = block.bbox
    width = max(0.5, box.x1 - box.x0)
    height = max(0.5, box.y1 - box.y0)
    z_index = 100 + block.order
    if block.type == BlockType.FIGURE and block.figure:
        path = base / block.figure.file
        if not path.is_file():
            raise FileNotFoundError(f"Figure image not found: {path}")
        paragraph = _anchor_paragraph(doc)
        run = paragraph.add_run()
        run.font.size = Pt(1)
        picture = run.add_picture(str(path), width=Pt(width), height=Pt(height))
        _float_picture(picture, box.x0, box.y0, width, height, z_index)
        return
    if block.type == BlockType.TABLE and block.table:
        for cell_index, cell in enumerate(block.table.cells):
            cell_box = cell.bbox
            cell_height = max(0.5, cell_box.y1 - cell_box.y0)
            cell_width = max(0.5, cell_box.x1 - cell_box.x0)
            font_size = _fit_font_size(cell.text, cell_width, cell_height, min(16.0, cell_height * 0.68))
            _add_textbox(
                doc,
                shape_id=f"docpipe-table-{page.index}-{block.order}-{cell_index}",
                x=cell_box.x0,
                y=cell_box.y0,
                width=cell_box.x1 - cell_box.x0,
                height=cell_height,
                z_index=z_index,
                text=cell.text,
                font_size=font_size,
                border=True,
            )
        return
    if block.text:
        font_size = _fit_font_size(block.text, width, height, min(24.0, height * 0.78))
        _add_textbox(
            doc,
            shape_id=f"docpipe-text-{page.index}-{block.order}",
            x=box.x0,
            y=box.y0,
            width=width,
            height=height,
            z_index=z_index,
            text=block.text,
            font_size=font_size,
            bold=block.type == BlockType.HEADING,
        )


def render(document: Document, out: Path, base_dir: Path | None = None) -> Path:
    """Create a page-faithful DOCX with objects anchored at their IR coordinates."""
    out.parent.mkdir(parents=True, exist_ok=True)
    base = base_dir or out.parent
    doc = WordDocument()
    doc.styles["Normal"].font.name = "Arial"
    if document.pages:
        _set_page(doc.sections[0], document.pages[0])
    for index, page in enumerate(document.pages):
        for block in sorted(page.blocks, key=lambda item: item.order):
            _add_block(doc, block, page, base)
        if index + 1 < len(document.pages):
            next_section = doc.add_section(WD_SECTION.NEW_PAGE)
            _set_page(next_section, document.pages[index + 1])
    doc.save(str(out))
    return out
