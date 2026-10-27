"""Resolve contradictory Nemotron labels using explicit table serialization.

No document names, page numbers, lexical corrections or additional OCR are used.
Explicit Chart/Logo labels stay graphical; generic Picture labels are checked.
"""
from __future__ import annotations

import html
import re

AMBIGUOUS_CLASSES = {"picture", "figure", "image", "graphic", "text"}
LATEX_START = re.compile(r"^\s*\\begin\{(tabular|longtable)\}\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")


def _cell(value):
    value = html.escape(value.strip())
    value = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", value, flags=re.S)
    return re.sub(r"&lt;(/?(?:sup|sub|br)/?)&gt;", r"<\1>", value)


def resolve_block_type(block):
    """Promote generic labels only when their entire body starts as a table."""
    cls, body = block["class"], block.get("text", "")
    if cls.lower() not in AMBIGUOUS_CLASSES:
        return dict(block)
    repairs = []
    normalized = None
    latex = LATEX_START.match(body)
    if latex:
        end = re.search(r"\\end\{" + re.escape(latex[1]) + r"\}", body[latex.end():])
        content = body[latex.end():]
        if end:
            content = content[:end.start()]
            if body[latex.end()+end.end():].strip():
                return dict(block)  # not a standalone table
        rows = [re.split(r"(?<!\\)&", row.strip()) for row in
                re.split(r"\\{2,}\s*(?:\r?\n|$)", content) if row.strip()]
        if len(rows) < 2:
            return dict(block)
        columns = len(rows[0])
        # A truncated column continuation can have a complete header, a single
        # anchored value, then bullet paragraphs with missing/misplaced '&'.
        # Retain all paragraphs in that anchored column, never invent values
        # for the empty columns or spread bullets into new table columns.
        occupied = [[i for i, value in enumerate(row) if value.strip()] for row in rows[1:]]
        fragmented = (end is None and columns > 1 and len(rows) >= 4
                      and len(rows[1]) == columns and len(occupied[0]) == 1
                      and all(len(indices) == 1 for indices in occupied)
                      and sum(len(row) < columns for row in rows[2:]) >= (len(rows)-2)*.6)
        if fragmented:
            anchor = occupied[0][0]
            fragments = [_cell(row[indices[0]]) for row, indices in zip(rows[1:], occupied)]
            values = [""] * columns
            values[anchor] = "<br/><br/>".join(fragments)
            normalized = "<table><tr>" + "".join("<th>" + _cell(v) + "</th>" for v in rows[0])
            normalized += "</tr><tr>" + "".join("<td>" + v + "</td>" for v in values) + "</tr></table>"
            repairs.append("column_continuation")
        elif end is None:
            normalized = body.rstrip() + "\n\\end{" + latex[1] + "}"
            repairs.append("closed_table_environment")
        else:
            normalized = body
    elif re.match(r"^\s*<table\b", body, re.I):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body, "html.parser")
        table = soup.find("table")
        rows = table.find_all("tr")
        if len(rows) >= 2 and all(row.find(["td", "th"]) is not None for row in rows):
            normalized = str(table)
    else:
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if len(lines) >= 3 and re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", lines[1]):
            rows = [[v.strip() for v in line.strip("|").split("|")] for line in [lines[0], *lines[2:]]]
            if len(rows[0]) >= 2 and all(len(row) == len(rows[0]) for row in rows):
                normalized = "<table>" + "".join("<tr>" + "".join(
                    f"<{tag}>" + _cell(value) + f"</{tag}>" for value in row) + "</tr>"
                    for row, tag in zip(rows, ["th"] + ["td"]*(len(rows)-1))) + "</table>"
    if normalized is None:
        return dict(block)
    return {**block, "class": "Table", "text": normalized, "original_class": cls,
            "type_resolution": "explicit_table_markup", "table_repairs": repairs}


def source_column_widths(page, bbox, columns):
    """Find continuous vertical rules in the original pixels, without OCR."""
    import numpy as np

    bitmap = page.render(scale=2)
    image = bitmap.to_pil().convert("L")
    try:
        x0, y0, x1, y1 = bbox
        left, top, right, bottom = (round(x0*image.width), round(y0*image.height),
                                   round(x1*image.width), round(y1*image.height))
        roi = np.asarray(image)[top:bottom, left:right]
        if min(roi.shape, default=0) < 5:
            return None
        inset = max(1, int(roi.shape[0]*.05))
        coverage = (roi[inset:-inset] < 200).mean(axis=0)
        hits = np.flatnonzero(coverage > .65)
        groups = np.split(hits, np.flatnonzero(np.diff(hits) > 1)+1)
        centers = [float(group.mean()) for group in groups if len(group)]
        inner = [value/roi.shape[1] for value in centers if .04 < value/roi.shape[1] < .96]
        if len(inner) != columns-1:
            return None
        boundaries = [0, *inner, 1]
        return [b-a for a, b in zip(boundaries, boundaries[1:])]
    finally:
        image.close()
        bitmap.close()
