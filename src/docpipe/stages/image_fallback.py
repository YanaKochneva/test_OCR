"""Preserve unreadable or lost output regions as source image fragments."""

import hashlib
import html
import math
import re
from collections import Counter
from dataclasses import replace
from pathlib import Path

from docpipe.ir import Block, BlockType, FigureData
from docpipe.input import rasterize_page
from docpipe.stages.graphics import PixelBox

LABEL = "ocr_image_fallback"


def _tokens(text):
    return re.findall(r"\w+", text.casefold().replace("ё", "е"))


def has_fallback(document):
    return any(b.source_label == LABEL for p in document.pages for b in p.blocks)


def _crop(block_id, box, order, raster, out, page_index, reason, original_text):
    width, height = raster.width_pt, raster.height_pt
    area = max(0, box.x1 - box.x0) * max(0, box.y1 - box.y0)
    if area >= width * height * 0.9:
        return None
    sx, sy = raster.image.width / width, raster.image.height / height
    pixel = PixelBox(
        max(0, math.floor(box.x0 * sx)),
        max(0, math.floor(box.y0 * sy)),
        min(raster.image.width, math.ceil(box.x1 * sx)),
        min(raster.image.height, math.ceil(box.y1 * sy)),
    )
    if pixel.x1 <= pixel.x0 or pixel.y1 <= pixel.y0:
        return None
    key = hashlib.sha256(block_id.encode()).hexdigest()[:12]
    folder = Path(out) / "images" / f"fallback-p{page_index + 1:03d}-{key}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "source-region.png"
    crop = raster.image.crop((pixel.x0, pixel.y0, pixel.x1, pixel.y1))
    try:
        crop.save(path, format="PNG")
    finally:
        crop.close()
    result = Block(
        id=block_id,
        type=BlockType.FIGURE,
        bbox=box,
        order=order,
        source_label=LABEL,
        figure=FigureData(
            file=path.relative_to(out).as_posix(),
            format=path.suffix[1:],
            width_px=pixel.x1 - pixel.x0,
            height_px=pixel.y1 - pixel.y0,
        ),
    )
    return result


def _table_html(table):
    rows = []
    covered = set()
    cells = {(c.row, c.col): c for c in table.cells}
    for row in range(table.n_rows):
        parts = []
        for col in range(table.n_cols):
            if (row, col) in covered:
                continue
            cell = cells.get((row, col))
            if cell is None:
                parts.append("<td></td>")
                continue
            for r in range(row, row + cell.rowspan):
                for c in range(col, col + cell.colspan):
                    covered.add((r, c))
            parts.append(
                f'<td rowspan="{cell.rowspan}" colspan="{cell.colspan}">{html.escape(cell.text)}</td>'
            )
        rows.append("<tr>" + "".join(parts) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


def _replace_regions(blocks, targets, raster, out, page_index):
    from docpipe.renderers.layout import _deduplicate

    blocks = _deduplicate(blocks)
    output = []
    images = []
    changes = 0
    order = max((b.order for b in blocks), default=-1) + 1
    for block in blocks:
        if (block.id, None) in targets and not block.figure:
            image = _crop(
                block.id,
                block.bbox,
                block.order,
                raster,
                out,
                page_index,
                targets[(block.id, None)],
                block.text or "",
            )
            if image is not None:
                output.append(image)
                changes += 1
                continue
        if block.table:
            cells = []
            modified = False
            for cell in block.table.cells:
                key = (block.id, (cell.row, cell.col))
                if key in targets:
                    image = _crop(
                        f"{block.id}:image-r{cell.row}c{cell.col}",
                        cell.bbox,
                        order,
                        raster,
                        out,
                        page_index,
                        targets[key],
                        cell.text,
                    )
                    if image is not None:
                        images.append(image)
                        order += 1
                        changes += 1
                        modified = True
                        cell = cell.model_copy(update={"text": "", "lines": None})
                cells.append(cell)
            if modified:
                table = block.table.model_copy(update={"cells": cells})
                table = table.model_copy(update={"html": _table_html(table)})
                block = block.model_copy(update={"table": table})
        output.append(block)
    # Cell images must be above the native table grid/fill.
    return output + images, changes


def fallback_unreadable(blocks, raster, config, out, page_index):
    if not config.render.unreadable_as_image or out is None:
        return blocks
    targets = {}
    for block in blocks:
        if block.figure:
            continue
        if block.table:
            for cell in block.table.cells:
                if "\ufffd" in cell.text:
                    targets[(block.id, (cell.row, cell.col))] = "unreadable_cell"
                elif not cell.text.strip():
                    box = cell.bbox
                    sx, sy = (
                        raster.image.width / raster.width_pt,
                        raster.image.height / raster.height_pt,
                    )
                    rect = (int(box.x0 * sx), int(box.y0 * sy), int(box.x1 * sx), int(box.y1 * sy))
                    if rect[2] <= rect[0] or rect[3] <= rect[1]:
                        continue
                    crop = raster.image.crop(rect).convert("L")
                    try:
                        margin = min(max(2, round(sx)), crop.width // 6, crop.height // 6)
                        inner = crop.crop(
                            (margin, margin, crop.width - margin, crop.height - margin)
                        )
                        try:
                            dark = sum(inner.histogram()[:150])
                            if dark > max(20, inner.width * inner.height * 0.02):
                                targets[(block.id, (cell.row, cell.col))] = (
                                    "visible_ink_without_recognized_cell_text"
                                )
                        finally:
                            inner.close()
                    finally:
                        crop.close()
            continue
        text = block.text or ""
        if (
            not text.strip()
            or "\ufffd" in text
            or (
                block.confidence is not None
                and block.confidence < config.render.unreadable_min_confidence
            )
        ):
            targets[(block.id, None)] = "unreadable_or_low_confidence"
    return _replace_regions(blocks, targets, raster, out, page_index)[0] if targets else blocks


def fallback_pdf_losses(document, config, out):
    """Replace only regions whose expected words are absent from their PDF location."""
    if not config.render.unreadable_as_image:
        return document, 0
    import pypdfium2 as pdfium

    from docpipe.input import normalize_input
    from docpipe.renderers.layout import stable_document
    from docpipe.renderers.layout import without_overlapping_ocr_echoes
    from docpipe.renderers.layout import a4_document

    source = Path(document.source.file)
    if not source.is_file():
        return document, 0
    layout = stable_document(a4_document(document))
    canonical = without_overlapping_ocr_echoes(document)
    keep = {(p.index, b.id) for p in canonical.pages for b in p.blocks}
    normalized = normalize_input(source, config)
    pages = []
    changed = 0
    try:
        with (
            pdfium.PdfDocument(normalized) as original,
            pdfium.PdfDocument(Path(out) / "document-positional.pdf") as pdf,
        ):
            for page in document.pages:
                if page.index >= len(pdf):
                    pages.append(page)
                    continue
                rendered = pdf[page.index]
                textpage = rendered.get_textpage()
                targets = {}
                try:
                    expected = Counter(
                        _tokens(
                            " ".join(
                                " ".join(c.text for c in b.table.cells) if b.table else b.text or ""
                                for b in page.blocks
                                if (page.index, b.id) in keep
                            )
                        )
                    )
                    missing = expected - Counter(_tokens(textpage.get_text_range()))
                    view = {b.id: b for b in layout.pages[page.index].blocks}
                    for block in page.blocks:
                        if (
                            block.figure
                            or block.id not in view
                            or (page.index, block.id) not in keep
                        ):
                            continue
                        mapped = view[block.id]
                        regions = [(None, block.text or "", mapped.bbox)]
                        if block.table:
                            mapped_cells = {(c.row, c.col): c for c in mapped.table.cells}
                            regions = [
                                ((c.row, c.col), c.text, mapped_cells[(c.row, c.col)].bbox)
                                for c in block.table.cells
                                if (c.row, c.col) in mapped_cells
                            ]
                        for cell, text, box in regions:
                            if not (Counter(_tokens(text)) & missing):
                                continue
                            inside = textpage.get_text_bounded(
                                left=box.x0 - 2,
                                bottom=rendered.get_height() - box.y1 - 2,
                                right=box.x1 + 2,
                                top=rendered.get_height() - box.y0 + 2,
                            )
                            lost = (Counter(_tokens(text)) - Counter(_tokens(inside))) & missing
                            if lost:
                                targets[(block.id, cell)] = "pdf_export_loss: " + ", ".join(lost)
                finally:
                    textpage.close()
                    rendered.close()
                if targets:
                    source_page = original[page.index]
                    try:
                        raster = rasterize_page(source_page, config.default_dpi, config.limits)
                    finally:
                        source_page.close()
                    try:
                        correction = (page.rotation - raster.rotation) % 360
                        if correction:
                            image = raster.image.rotate(-correction, expand=True)
                            raster.image.close()
                            raster = replace(
                                raster,
                                image=image,
                                width_pt=page.width_pt,
                                height_pt=page.height_pt,
                                rotation=page.rotation,
                            )
                        blocks, count = _replace_regions(
                            page.blocks, targets, raster, out, page.index
                        )
                        page = page.model_copy(update={"blocks": blocks})
                        changed += count
                    finally:
                        raster.image.close()
                pages.append(page)
    finally:
        if normalized != source:
            normalized.unlink(missing_ok=True)
    return document.model_copy(update={"pages": pages}), changed
