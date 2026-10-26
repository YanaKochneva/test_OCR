"""Render a Nemotron HTML/LaTeX table within its predicted page box."""
from __future__ import annotations

import html
from pathlib import Path
import re
import sys
from statistics import median


def draw_table(canvas, raw, x, top, width, height):
    from bs4 import BeautifulSoup
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, Table, TableStyle

    if "\\begin{tabular}" in raw:
        model_dir = Path(__file__).resolve().parents[2] / "models/nemotron-3.5-parse"
        if str(model_dir) not in sys.path:
            sys.path.insert(0, str(model_dir))
        from nemotron_parse.postprocessing import PostprocessOptions, postprocess_text
        raw = postprocess_text(raw, "Table", table_format="HTML", options=PostprocessOptions(
            infer_headers=False, normalize_text=False, deduplicate=False))
    soup = BeautifulSoup(raw, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("Nemotron table has no supported HTML/LaTeX structure")
    rows = table.find_all("tr")
    if not rows:
        raise ValueError("Nemotron table has no rows")
    cells, occupied, spans = [], set(), []
    ncols = 0
    for row, tr in enumerate(rows):
        col = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (row, col) in occupied:
                col += 1
            rs, cs = max(1, int(cell.get("rowspan", 1))), max(1, int(cell.get("colspan", 1)))
            rs = min(rs, len(rows) - row)
            body = cell.get_text(" ", strip=True)
            body = re.sub(r"\*\*(.*?)\*\*", r"\1", body, flags=re.S)
            cells.append((row, col, html.escape(body), cell.name == "th"))
            for r in range(row, row + rs):
                for c in range(col, col + cs):
                    occupied.add((r, c))
            if rs > 1 or cs > 1:
                spans.append(("SPAN", (col, row), (col + cs - 1, row + rs - 1)))
            col += cs
            ncols = max(ncols, col)
    if not ncols:
        raise ValueError("Nemotron table has no columns")
    # Weight columns by their content. This needs no secondary recognition.
    samples = [[] for _ in range(ncols)]
    for row, col, body, bold in cells:
        span = next((s for s in spans if s[1] == (col, row)), None)
        if span and span[2][0] != col:
            continue
        samples[col].append(min(80, max(8, len(html.unescape(body)))))
    weights = [median(values) ** .6 if values else 1 for values in samples]
    widths = [width * w / sum(weights) for w in weights]
    size = 9.5
    while True:
        data = [[""] * ncols for _ in rows]
        for row, col, body, bold in cells:
            style = ParagraphStyle("cell", fontName="NemotronBold" if bold else "Nemotron",
                                   fontSize=size, leading=size * 1.1, splitLongWords=True)
            data[row][col] = Paragraph(body, style)
        result = Table(data, colWidths=widths)
        result.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#888888")),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            *spans,
        ]))
        _, used = result.wrap(width, height)
        if used <= height or size <= 4:
            break
        size -= .1
    if used > height:
        raise ValueError("Nemotron table does not fit its predicted box")
    # Keep both top and bottom at the predicted coordinates, rather than
    # compressing a tall table into a small patch at the top of the box.
    result = Table(data, colWidths=widths,
                   rowHeights=[h * height / used for h in result._rowHeights])
    result.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#888888")),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1), *spans,
    ]))
    result.wrap(width, height)
    result.drawOn(canvas, x, top - height)
    return {"rows": len(rows), "columns": ncols, "font_size": round(size, 2)}
