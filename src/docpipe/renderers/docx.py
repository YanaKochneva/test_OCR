from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document as WordDocument
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt

from docpipe.ir import Block, BlockType, Document, Page
from docpipe.renderers.table_layout import build_table_layout
from docpipe.renderers.text_layout import collision_free_text_tops, estimate_text_height, wrap_text
from docpipe.renderers.typography import (
    BODY_FONT_PT,
    BODY_LINE_SPACING,
    FONT_FAMILY,
    TABLE_FONT_PT,
    text_style,
)

_EMU_PER_POINT = 12_700
_TWIPS_PER_POINT = 20


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
    italic: bool = False,
    border: bool = False,
) -> None:
    lines = text.splitlines() or [""]
    escaped_lines = []
    for line_index, line in enumerate(lines):
        if line_index:
            escaped_lines.append("<w:br/>")
        escaped_lines.append(f'<w:t xml:space="preserve">{escape(line)}</w:t>')
    bold_xml = "<w:b/>" if bold else ""
    italic_xml = "<w:i/>" if italic else ""
    font_half_points = max(10, round(font_size * 2))
    border_attrs = 'stroked="t" strokeweight="0.5pt"' if border else 'stroked="f"'
    namespaces = nsdecls("w") + ' xmlns:v="urn:schemas-microsoft-com:vml"'
    xml = f"""<w:pict {namespaces}>
      <v:shape id="{escape(shape_id)}" style="position:absolute;left:{x:.3f}pt;top:{y:.3f}pt;width:{max(width, 0.5):.3f}pt;height:{max(height, 0.5):.3f}pt;z-index:{z_index};mso-position-horizontal-relative:page;mso-position-vertical-relative:page;mso-wrap-style:none" {border_attrs} filled="f">
        <v:textbox inset="1pt,0,1pt,0"><w:txbxContent>
          <w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="{max(20, round(font_half_points * BODY_LINE_SPACING * 10))}" w:lineRule="exact"/></w:pPr>
            <w:r><w:rPr><w:rFonts w:ascii="{FONT_FAMILY}" w:hAnsi="{FONT_FAMILY}" w:eastAsia="{FONT_FAMILY}" w:cs="{FONT_FAMILY}"/><w:sz w:val="{font_half_points}"/>{bold_xml}{italic_xml}</w:rPr>{''.join(escaped_lines)}</w:r>
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


def _set_table_borders(table) -> None:
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        layout = tbl_pr.find(qn("w:tblLayout"))
        index = tbl_pr.index(layout) if layout is not None else len(tbl_pr)
        tbl_pr.insert(index, borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = borders.find(qn(f"w:{edge}"))
        if element is None:
            element = OxmlElement(f"w:{edge}")
            borders.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), "333333")


def _anchor_table(table, block: Block, top: float | None = None) -> None:
    """Float a native Word table at the OCR table's page coordinates."""
    box = block.bbox
    tbl_pr = table._tbl.tblPr
    position = tbl_pr.find(qn("w:tblpPr"))
    if position is None:
        position = OxmlElement("w:tblpPr")
        style = tbl_pr.find(qn("w:tblStyle"))
        tbl_pr.insert(tbl_pr.index(style) + 1 if style is not None else 0, position)
    for name, value in {
        "horzAnchor": "page",
        "vertAnchor": "page",
        "tblpX": str(round(box.x0 * _TWIPS_PER_POINT)),
        "tblpY": str(round((box.y0 if top is None else top) * _TWIPS_PER_POINT)),
        "leftFromText": "0",
        "rightFromText": "0",
        "topFromText": "0",
        "bottomFromText": "0",
    }.items():
        position.set(qn(f"w:{name}"), value)


def _format_table_cell(cell, text: str) -> None:
    cell.text = text
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
    cell_properties = cell._tc.get_or_add_tcPr()
    shading = cell_properties.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        cell_properties.append(shading)
    shading.set(qn("w:val"), "clear")
    shading.set(qn("w:color"), "auto")
    shading.set(qn("w:fill"), "FFFFFF")
    for paragraph in cell.paragraphs:
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.0
        for run in paragraph.runs:
            run.font.name = FONT_FAMILY
            run.font.size = Pt(TABLE_FONT_PT)


def _add_table(doc: WordDocument, block: Block, top: float | None = None) -> None:
    layout = build_table_layout(block)
    table = doc.add_table(rows=layout.rows, cols=layout.cols)
    table.autofit = False
    _anchor_table(table, block, top)
    _set_table_borders(table)

    tbl_pr = table._tbl.tblPr
    tbl_width = tbl_pr.find(qn("w:tblW"))
    if tbl_width is not None:
        tbl_width.set(qn("w:w"), str(round((block.bbox.x1 - block.bbox.x0) * _TWIPS_PER_POINT)))
        tbl_width.set(qn("w:type"), "dxa")

    margins = tbl_pr.find(qn("w:tblCellMar"))
    if margins is None:
        margins = OxmlElement("w:tblCellMar")
        look = tbl_pr.find(qn("w:tblLook"))
        tbl_pr.insert(tbl_pr.index(look) if look is not None else len(tbl_pr), margins)
    for edge, value in (("top", 20), ("bottom", 20), ("start", 40), ("end", 40)):
        item = margins.find(qn(f"w:{edge}"))
        if item is None:
            item = OxmlElement(f"w:{edge}")
            margins.append(item)
        item.set(qn("w:w"), str(value))
        item.set(qn("w:type"), "dxa")

    for col_index, width in enumerate(layout.column_widths):
        table.columns[col_index].width = Pt(width)
        for row in table.rows:
            row.cells[col_index].width = Pt(width)
    for row_index, height in enumerate(layout.row_heights):
        row = table.rows[row_index]
        row.height = Pt(height)
        row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
        tr_pr = row._tr.get_or_add_trPr()
        if tr_pr.find(qn("w:cantSplit")) is None:
            tr_pr.append(OxmlElement("w:cantSplit"))
        for cell in row.cells:
            _format_table_cell(cell, "")

    for placement in layout.cells:
        cell = table.cell(placement.row, placement.col)
        if placement.rowspan > 1 or placement.colspan > 1:
            last = table.cell(
                placement.row + placement.rowspan - 1,
                placement.col + placement.colspan - 1,
            )
            cell = cell.merge(last)
        _format_table_cell(cell, placement.text)


def _add_block(doc: WordDocument, block: Block, page: Page, base: Path, text_top: float | None = None) -> None:
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
        _add_table(doc, block, text_top)
        return
    if block.text:
        if block.type == BlockType.LIST_ITEM:
            text = chr(8226) + " " + block.text
        else:
            text = block.text
        font_size, bold, italic = text_style(block.type.value)
        text = wrap_text(text, width, (font_size, bold, italic))
        content_height = max(
            height,
            estimate_text_height(text, width, (font_size, bold, italic)),
        )
        _add_textbox(
            doc,
            shape_id=f"docpipe-text-{page.index}-{block.order}",
            x=box.x0,
            y=box.y0 if text_top is None else text_top,
            width=width,
            height=content_height,
            z_index=z_index,
            text=text,
            font_size=font_size,
            bold=bold,
            italic=italic,
        )


def render(document: Document, out: Path, base_dir: Path | None = None) -> Path:
    """Create a page-faithful DOCX with objects anchored at their IR coordinates."""
    out.parent.mkdir(parents=True, exist_ok=True)
    base = base_dir or out.parent
    doc = WordDocument()
    doc.styles["Normal"].font.name = FONT_FAMILY
    doc.styles["Normal"].font.size = Pt(BODY_FONT_PT)
    if document.pages:
        _set_page(doc.sections[0], document.pages[0])
    for index, page in enumerate(document.pages):
        text_tops = collision_free_text_tops(
            page.blocks, lambda block: text_style(block.type.value)
        )
        for block in sorted(page.blocks, key=lambda item: item.order):
            _add_block(doc, block, page, base, text_tops.get(id(block)))
        if index + 1 < len(document.pages):
            next_section = doc.add_section(WD_SECTION.NEW_PAGE)
            _set_page(next_section, document.pages[index + 1])
    doc.save(str(out))
    return out
