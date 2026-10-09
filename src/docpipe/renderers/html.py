from __future__ import annotations

import base64
import mimetypes
import os
from html import escape
from pathlib import Path
from urllib.parse import quote

from docpipe.ir import Block, BlockType, Document
from docpipe.renderers.table_layout import build_table_layout
from docpipe.renderers.text_layout import collision_free_text_tops, estimate_text_height, wrap_text
from docpipe.renderers.typography import FONT_FAMILY, TABLE_FONT_PT, text_style


def _box_style(x0: float, y0: float, x1: float, y1: float) -> str:
    return (
        f"left:{x0:.3f}pt;top:{y0:.3f}pt;"
        f"width:{max(0.5, x1 - x0):.3f}pt;height:{max(0.5, y1 - y0):.3f}pt"
    )


def _text_block(block: Block, top: float | None = None) -> str:
    text = block.text or ""
    if block.type == BlockType.LIST_ITEM:
        text = chr(8226) + " " + text
    size, bold, italic = text_style(block.type.value)
    bbox = block.bbox
    text = wrap_text(text, bbox.x1 - bbox.x0, (size, bold, italic))
    top = bbox.y0 if top is None else top
    height = max(
        bbox.y1 - bbox.y0,
        estimate_text_height(text, bbox.x1 - bbox.x0, (size, bold, italic)),
    )
    style = _box_style(bbox.x0, top, bbox.x1, top + height)
    return (
        f'<div class="text-block" data-block="{escape(block.id, quote=True)}" '
        f'data-order="{block.order}" style="{style};font-size:{size:.2f}pt;'
        f'font-weight:{700 if bold else 400};font-style:{"italic" if italic else "normal"}">'
        f"{escape(text)}</div>"
    )


def _table_markup(block: Block, top: float | None = None) -> str:
    """Render OCR cells as one positioned, semantic HTML table."""
    layout = build_table_layout(block)
    box = block.bbox
    top = box.y0 if top is None else top
    style = _box_style(box.x0, top, box.x1, top + sum(layout.row_heights))
    cells = {(cell.row, cell.col): cell for cell in layout.cells}
    covered: set[tuple[int, int]] = set()
    output = [
        f'<table class="table-block" data-block="{escape(block.id, quote=True)}" '
        f'data-order="{block.order}" style="{style};font-size:{TABLE_FONT_PT:.2f}pt">',
        "<colgroup>",
    ]
    output.extend(f'<col style="width:{width:.3f}pt">' for width in layout.column_widths)
    output.append("</colgroup><tbody>")
    for row in range(layout.rows):
        output.append(f'<tr style="height:{layout.row_heights[row]:.3f}pt">')
        col = 0
        while col < layout.cols:
            if (row, col) in covered:
                col += 1
                continue
            cell = cells.get((row, col))
            if cell is None:
                output.append("<td></td>")
                col += 1
                continue
            if cell.rowspan > 1:
                covered.update(
                    (covered_row, covered_col)
                    for covered_row in range(row + 1, row + cell.rowspan)
                    for covered_col in range(col, col + cell.colspan)
                )
            rowspan = f' rowspan="{cell.rowspan}"' if cell.rowspan > 1 else ""
            colspan = f' colspan="{cell.colspan}"' if cell.colspan > 1 else ""
            output.append(f"<td{rowspan}{colspan}>{escape(cell.text)}</td>")
            col += cell.colspan
        output.append("</tr>")
    output.append("</tbody></table>")
    return "".join(output)


def render(
    document: Document,
    out: Path,
    self_contained: bool = True,
    base_dir: Path | None = None,
) -> Path:
    """Render positioned page canvases with consistent type and real tables."""
    out.parent.mkdir(parents=True, exist_ok=True)
    image_root = base_dir or out.parent
    body: list[str] = ['<main id="document">']
    for page in document.pages:
        text_tops = collision_free_text_tops(
            page.blocks, lambda block: text_style(block.type.value)
        )
        body.append(
            f'<section class="page" data-page="{page.index + 1}" '
            f'style="width:{page.width_pt:.3f}pt;height:{page.height_pt:.3f}pt">'
        )
        for block in sorted(page.blocks, key=lambda item: item.order):
            if block.type == BlockType.FIGURE and block.figure:
                image_path = image_root / block.figure.file
                if not image_path.is_file():
                    raise FileNotFoundError(f"Figure image not found: {image_path}")
                src = block.figure.file
                if self_contained:
                    mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
                    data = base64.b64encode(image_path.read_bytes()).decode("ascii")
                    src = f"data:{mime};base64,{data}"
                else:
                    relative_path = os.path.relpath(image_path, start=out.parent)
                    src = quote(Path(relative_path).as_posix(), safe="/.:_")
                style = _box_style(block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1)
                body.append(
                    f'<figure class="figure-position" data-file="{escape(block.figure.file, quote=True)}" '
                    f'style="{style}"><img class="figure" alt="" '
                    f'src="{escape(src, quote=True)}"></figure>'
                )
            elif block.type == BlockType.TABLE and block.table:
                body.append(_table_markup(block, text_tops.get(id(block))))
            elif block.type == BlockType.CAPTION and not (block.text or "").strip():
                continue
            elif block.text and block.type not in {BlockType.FIGURE, BlockType.TABLE}:
                body.append(_text_block(block, text_tops.get(id(block))))
        body.append("</section>")
    body.append("</main>")
    family = f'{FONT_FAMILY}, "DejaVu Sans", sans-serif'
    html = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Docpipe</title><style>
*{{box-sizing:border-box}}html,body{{margin:0;padding:0;background:#e9ece9;color:#151515}}
#document{{width:max-content;max-width:100%;margin:24px auto;font-family:{family};font-size:10pt}}
.page{{position:relative;overflow:hidden;background:#fff;margin:0 auto 24px;box-shadow:0 2px 14px #0002;break-after:page;page-break-after:always}}
.page:last-child{{break-after:auto;page-break-after:auto}}
.text-block,.table-block,.figure{{position:absolute;margin:0;padding:0;font-family:{family};color:#111;z-index:1}}
.text-block{{overflow:visible;line-height:1;white-space:pre-wrap;overflow-wrap:anywhere}}
.figure-position{{position:absolute;z-index:1;margin:0;padding:0;overflow:hidden;background:#fff}}.figure{{display:block;width:100%;height:100%;object-fit:fill}}
.table-block{{width:100%;height:100%;table-layout:fixed;border-collapse:collapse;line-height:1;background:#fff}}
.table-block td{{border:.5pt solid #333;padding:1.5pt 2pt;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}}
@media print{{@page{{margin:0}}html,body{{background:#fff}}#document{{margin:0}}.page{{margin:0;box-shadow:none}}}}
</style></head><body>""" + "".join(body) + "</body></html>"
    out.write_text(html, encoding="utf-8")
    return out
