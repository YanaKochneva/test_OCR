from __future__ import annotations

import cv2
import math
import numpy as np
import re
from docpipe.ir import Block, BlockType
from rapidfuzz.distance import Levenshtein


def xy_cut(blocks: list[Block], page_width: float, gap: float = 18.0) -> list[Block]:
    if len(blocks) <= 1:
        return list(blocks)
    # Детерминированное разбиение по вертикальным полосам: сначала колонки,
    # внутри колонки сверху вниз. Это fallback, а не замена engine order.
    sorted_blocks = sorted(blocks, key=lambda b: (b.bbox.x0, b.bbox.y0, b.id))
    columns: list[list[Block]] = []
    for block in sorted_blocks:
        placed = False
        for col in columns:
            right = max(x.bbox.x1 for x in col)
            left = min(x.bbox.x0 for x in col)
            if block.bbox.x0 <= right + gap and block.bbox.x1 >= left - gap:
                col.append(block)
                placed = True
                break
        if not placed:
            columns.append([block])
    columns.sort(key=lambda c: min(x.bbox.x0 for x in c))
    result = []
    for col in columns:
        result.extend(sorted(col, key=lambda b: (b.bbox.y0, b.bbox.x0, b.id)))
    return result


def assign_order(blocks: list[Block], page_width: float) -> list[Block]:
    ordered = xy_cut(blocks, page_width)
    return [b.model_copy(update={"order": i}) for i, b in enumerate(ordered)]


def clean_text(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def postprocess(blocks: list[Block], headers_footers: str = "drop") -> list[Block]:
    out = []
    for b in blocks:
        if b.text is not None and b.type != BlockType.FIGURE:
            b = b.model_copy(update={"text": clean_text(b.text)})
        if headers_footers == "drop" and b.type in {
            BlockType.HEADER,
            BlockType.FOOTER,
            BlockType.PAGE_NUMBER,
        }:
            continue
        out.append(b)
    return out


def attach_captions(blocks: list[Block], max_gap: float = 48.0) -> list[Block]:
    figs = [b for b in blocks if b.type == BlockType.FIGURE and b.source_label != "graphic_field"]
    updates = {}
    for cap in [b for b in blocks if b.type == BlockType.CAPTION]:
        candidates = []
        for fig in figs:
            same_column = abs(cap.bbox.x0 - fig.bbox.x0) <= max(
                cap.bbox.x1 - cap.bbox.x0, fig.bbox.x1 - fig.bbox.x0
            )
            if not same_column:
                continue
            gap = (
                fig.bbox.y0 - cap.bbox.y1
                if cap.bbox.y1 <= fig.bbox.y0
                else cap.bbox.y0 - fig.bbox.y1
            )
            if 0 <= gap <= max_gap:
                candidates.append((gap, fig))
        if candidates:
            fig = min(candidates, key=lambda x: (x[0], x[1].order))[1]
            updates[fig.id] = fig.model_copy(
                update={"figure": fig.figure.model_copy(update={"caption_id": cap.id})}
            )
    return [updates.get(b.id, b) for b in blocks]


def _region(block, page):
    box = block.bbox
    width, height = box.x1 - box.x0, box.y1 - box.y0
    if height <= page.height_pt * 0.06:
        if box.y0 >= page.height_pt * 0.87:
            return "footer"
        if box.y1 <= page.height_pt * 0.10:
            return "header"
    if height >= page.height_pt * 0.25 and width <= page.width_pt * 0.07:
        if box.x1 <= page.width_pt * 0.11:
            return "left"
        if box.x0 >= page.width_pt * 0.89:
            return "right"
    return None


def classify_repeated_margins(pages, headers_footers="drop"):
    groups = []
    candidates = []
    for page_index, page in enumerate(pages):
        for block_index, block in enumerate(page.blocks):
            if block.type not in {BlockType.TEXT, BlockType.OTHER}:
                continue
            text = block.text or ""
            if not 5 <= len(text) <= 300:
                continue
            region = _region(block, page)
            if not region:
                continue
            fingerprint = re.sub(r"[^\w]+", " ", text.casefold())
            fingerprint = re.sub(r"\d+", "", fingerprint)
            fingerprint = " ".join(fingerprint.split())
            if len(fingerprint) < 5:
                continue
            match = next(
                (
                    group
                    for group in groups
                    if group["region"] == region
                    and Levenshtein.normalized_similarity(group["text"], fingerprint) >= 0.85
                ),
                None,
            )
            if match is None:
                match = {"region": region, "text": fingerprint, "pages": set()}
                groups.append(match)
            match["pages"].add(page_index)
            candidates.append((page_index, block_index, match))
    support = max(3, math.ceil(len(pages) * 0.10))
    selected = {}
    for page_index, block_index, group in candidates:
        if len(group["pages"]) >= support:
            selected.setdefault(page_index, {})[block_index] = (
                BlockType.HEADER if group["region"] == "header" else BlockType.FOOTER
            )
    result = []
    for index, page in enumerate(pages):
        updates = selected.get(index, {})
        blocks = [
            block.model_copy(update={"type": updates[bi], "source_label": "repeated_margin"})
            if bi in updates
            else block
            for bi, block in enumerate(page.blocks)
        ]
        blocks = postprocess(blocks, headers_footers)
        blocks = [block.model_copy(update={"order": order}) for order, block in enumerate(blocks)]
        warnings = list(page.warnings)
        if updates:
            warnings.append(f"Reclassified recurrent margin blocks: {len(updates)}")
        result.append(page.model_copy(update={"blocks": blocks, "warnings": warnings}))
    return result


def restore_list_markers(blocks, image, meta):
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    unit = meta.width_pt / 595.276
    output = []
    for block in blocks:
        if (
            block.type != BlockType.LIST_ITEM
            or not block.text
            or block.text.lstrip().startswith(("-", "–", "—", "•"))
        ):
            output.append(block)
            continue
        box = block.bbox
        left, top = max(0, round((box.x0 - 2 * unit) * sx)), max(0, round(box.y0 * sy))
        right = min(image.width, round((box.x0 + 18 * unit) * sx))
        bottom = min(image.height, round(min(box.y1, box.y0 + 18 * unit) * sy))
        if right <= left or bottom <= top:
            output.append(block)
            continue
        with image.crop((left, top, right, bottom)) as crop:
            ink = (np.asarray(crop.convert("L")) < 150).astype("uint8")
        contours, _ = cv2.findContours(ink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        shapes = sorted([cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > 2])
        if len(shapes) >= 2:
            x, y, w, h = shapes[0]
            visible = (
                w >= 2 * h
                and 2 * unit * sx <= w <= 7 * unit * sx
                and h <= 2 * unit * sy
                and 0.25 * ink.shape[0] <= y <= 0.8 * ink.shape[0]
                and shapes[1][0] - (x + w) >= 2
            )
            if visible:
                lines = block.lines
                if lines and not lines[0].text.lstrip().startswith("-"):
                    lines = [lines[0].model_copy(update={"text": "- " + lines[0].text}), *lines[1:]]
                block = block.model_copy(update={"text": "- " + block.text, "lines": lines})
        output.append(block)
    return output


def remove_text_duplicates(blocks):
    def normalize(text):
        return re.sub(r"\s+", "", text.casefold())

    text_blocks = [b for b in blocks if b.text and not b.table and b.type != BlockType.FIGURE]
    removed = set()
    for block in sorted(text_blocks, key=lambda b: len(normalize(b.text)), reverse=True):
        box = block.bbox
        parts = []
        for other in text_blocks:
            if other is block or id(other) in removed:
                continue
            b = other.bbox
            if (
                box.x0 - 3 <= b.x0
                and b.x1 <= box.x1 + 3
                and box.y0 - 3 <= b.y0
                and b.y1 <= box.y1 + 3
            ):
                parts.append(other)
        parts.sort(key=lambda b: (b.bbox.y0, b.bbox.x0, b.order))
        # At least two components: identical single blocks might be genuine
        # overprinted text and need a separate decision.
        if len(parts) >= 2 and normalize(block.text) == normalize(" ".join(b.text for b in parts)):
            removed.add(id(block))
    return [
        b.model_copy(update={"order": i})
        for i, b in enumerate(b for b in blocks if id(b) not in removed)
    ]
