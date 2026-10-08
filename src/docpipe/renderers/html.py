from __future__ import annotations

import base64
import mimetypes
import os
from html import escape
from pathlib import Path
from urllib.parse import quote

from docpipe.ir import Block, BlockType, Document


def _box_style(x0: float, y0: float, x1: float, y1: float) -> str:
    return (
        f"left:{x0:.3f}pt;top:{y0:.3f}pt;"
        f"width:{max(0.5, x1 - x0):.3f}pt;height:{max(0.5, y1 - y0):.3f}pt"
    )


def _fit_font_size(
    text: str,
    width: float,
    height: float,
    maximum: float,
    *,
    line_height: float = 1.04,
    horizontal_inset: float = 0.0,
    vertical_inset: float = 0.0,
) -> float:
    """Estimate a font size whose wrapped text fits its positioned box."""
    available_width = max(1.0, width - horizontal_inset)
    available_height = max(1.0, height - vertical_inset)
    lines = text.splitlines() or [""]

    def fits(size: float) -> bool:
        chars_per_line = max(1, int(available_width / (size * 0.52)))
        wrapped_lines = sum(
            max(1, (len(line) + chars_per_line - 1) // chars_per_line)
            for line in lines
        )
        return wrapped_lines * size * line_height <= available_height

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


def _text_block(block: Block) -> str:
    text = block.text or ""
    if block.type == BlockType.LIST_ITEM:
        text = "• " + text
    bbox = block.bbox
    font_size = _fit_font_size(
        text,
        bbox.x1 - bbox.x0,
        bbox.y1 - bbox.y0,
        min(24.0, (bbox.y1 - bbox.y0) * 0.78),
    )
    bold = block.type == BlockType.HEADING
    tag = "h2" if bold else "div"
    style = _box_style(bbox.x0, bbox.y0, bbox.x1, bbox.y1)
    return (
        f'<{tag} class="text-block" data-block="{escape(block.id, quote=True)}" '
        f'data-order="{block.order}" style="{style};font-size:{font_size:.2f}pt;'
        f'font-weight:{700 if bold else 400}">{escape(text)}</{tag}>'
    )


def _table_cells(block: Block, page_width: float, page_height: float) -> list[str]:
    assert block.table is not None
    output: list[str] = []
    cells = block.table.cells
    for index, cell in enumerate(cells):
        box = cell.bbox
        style = _box_style(box.x0, box.y0, box.x1, box.y1)
        fontsize = _fit_font_size(
            cell.text,
            box.x1 - box.x0,
            box.y1 - box.y0,
            min(16.0, (box.y1 - box.y0) * 0.68),
            line_height=1.02,
            horizontal_inset=5.0,
            vertical_inset=2.0,
        )
        output.append(
            f'<div role="cell" class="table-cell" data-block="{escape(block.id, quote=True)}" '
            f'data-cell="{index}" style="{style};font-size:{fontsize:.2f}pt">'
            f'{escape(cell.text)}</div>'
        )
    if cells:
        return output

    # Preserve recognized table text when an engine provides no cell geometry.
    box = block.bbox
    if (block.text or "").strip():
        style = _box_style(box.x0, box.y0, box.x1, box.y1)
        output.append(
            f'<div role="cell" class="table-cell" data-block="{escape(block.id, quote=True)}" '
            f'style="{style}">{escape(block.text)}</div>'
        )
        return output

    rows: dict[int, dict[int, str]] = {}
    for cell in cells:
        rows.setdefault(cell.row, {})[cell.col] = cell.text
    cols = max(1, block.table.n_cols)
    row_count = max(1, block.table.n_rows)
    for row in range(row_count):
        for col in range(cols):
            x0 = box.x0 + (box.x1 - box.x0) * col / cols
            x1 = box.x0 + (box.x1 - box.x0) * (col + 1) / cols
            y0 = box.y0 + (box.y1 - box.y0) * row / row_count
            y1 = box.y0 + (box.y1 - box.y0) * (row + 1) / row_count
            style = _box_style(x0, y0, x1, y1)
            output.append(
                f'<div role="cell" class="table-cell" data-block="{escape(block.id, quote=True)}" '
                f'style="{style}">{escape(rows.get(row, {}).get(col, ""))}</div>'
            )
    return output


def render(
    document: Document,
    out: Path,
    self_contained: bool = True,
    base_dir: Path | None = None,
) -> Path:
    """Render an HTML document with page-sized canvases and IR-positioned objects."""
    out.parent.mkdir(parents=True, exist_ok=True)
    image_root = base_dir or out.parent
    body: list[str] = ['<main id="document">']
    for page in document.pages:
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
                body.extend(_table_cells(block, page.width_pt, page.height_pt))
            elif block.type == BlockType.CAPTION and not (block.text or "").strip():
                continue
            elif block.text and block.type not in {BlockType.FIGURE, BlockType.TABLE}:
                body.append(_text_block(block))
        body.append("</section>")
    body.append("</main>")
    html = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Распознанный документ</title><style>
*{box-sizing:border-box}html,body{margin:0;padding:0;background:#e9ece9;color:#151515}
#document{width:max-content;max-width:100%;margin:24px auto;font-family:Arial,"DejaVu Sans",sans-serif}
.page{position:relative;overflow:hidden;background:#fff;margin:0 auto 24px;box-shadow:0 2px 14px #0002;break-after:page;page-break-after:always}
.page:last-child{break-after:auto;page-break-after:auto}.text-block,.table-cell,.figure{position:absolute;margin:0;padding:0}
.text-block{overflow:visible}.table-cell,.figure{overflow:hidden}
.text-block{line-height:1.04;white-space:pre-wrap;overflow-wrap:anywhere;color:#111}
.text-block h2{margin:0}.figure-position{position:absolute;margin:0;padding:0;overflow:hidden}.figure{display:block;width:100%;height:100%;object-fit:fill}.table-cell{border:.45pt solid #363636;padding:1pt 2pt;line-height:1.02;white-space:pre-wrap;overflow-wrap:anywhere;color:#111}
@media print{@page{margin:0}html,body{background:#fff}#document{margin:0}.page{margin:0;box-shadow:none}}
</style></head><body>""" + "".join(body) + "</body></html>"
    out.write_text(html, encoding="utf-8")
    return out
