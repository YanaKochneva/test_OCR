from __future__ import annotations

import numpy as np
import re
from collections import Counter
from docpipe.ir import BBox, Block, BlockType, Line
from rapidfuzz.distance import Levenshtein


def recover_unassigned_lines(
    blocks: list[Block], lines: list[tuple[str, BBox, float]], page_height: float
) -> list[Block]:
    result = list(blocks)
    for index, (text, box, confidence) in enumerate(lines):
        if not text.strip() or confidence < 0.75:
            continue
        area = max(0.01, (box.x1 - box.x0) * (box.y1 - box.y0))

        def covered(block):
            b = block.bbox
            intersection = max(0, min(box.x1, b.x1) - max(box.x0, b.x0)) * max(
                0, min(box.y1, b.y1) - max(box.y0, b.y0)
            )
            return intersection / area >= 0.6

        if any(covered(b) for b in result):
            continue
        # A detector can join two columns into one line. Each block may cover
        # less than 60% of that line, although together they already contain
        # all its words. Do not create another overlapping copy of that text.
        overlapping = []
        overlap_area = 0.0
        for block in result:
            b = block.bbox
            intersection = max(0, min(box.x1, b.x1) - max(box.x0, b.x0)) * max(
                0, min(box.y1, b.y1) - max(box.y0, b.y0)
            )
            if intersection:
                overlapping.append(block)
                overlap_area += intersection
        if len(overlapping) > 1 and overlap_area / area >= 0.6:

            def words(value):
                return Counter(re.findall(r"\w+", value.casefold()))

            available = Counter()
            for block in overlapping:
                available.update(words(block.text or ""))
                if block.table:
                    for cell in block.table.cells:
                        available.update(words(cell.text))
            if not (words(text) - available):
                continue
        kind = BlockType.TEXT
        if box.y1 < page_height * 0.07:
            kind = BlockType.HEADER
        elif box.y0 > page_height * 0.94:
            kind = BlockType.FOOTER
        recovered = Block(
            id=f"ocr-recovery-{index}",
            type=kind,
            bbox=box,
            order=0,
            text=text,
            confidence=confidence,
            source_label="unassigned_ocr_line",
        )
        # Keep the engine's reading order. Insert near the preceding text in
        # the same column instead of rebuilding every column from scratch.
        predecessors = [
            i
            for i, b in enumerate(result)
            if b.bbox.y0 <= box.y0 and min(b.bbox.x1, box.x1) > max(b.bbox.x0, box.x0)
        ]
        position = max(predecessors, key=lambda i: result[i].bbox.y0) + 1 if predecessors else 0
        result.insert(position, recovered)
    return [b.model_copy(update={"order": i}) for i, b in enumerate(result)]


def aggregate_confidence(lines):
    """Character-weighted summary; individual weak lines remain review signals."""
    total = sum(max(1, len(text.strip())) for text, score in lines)
    return (
        sum(max(1, len(text.strip())) * score for text, score in lines) / total if total else None
    )


def attach_confidence(blocks, raw_lines):
    """Only associate an OCR score with matching text fully inside the region."""

    def score(region, text):
        matches = [
            (t, s)
            for t, b, s in raw_lines
            if region.x0 - 1 <= b.x0
            and region.y0 - 1 <= b.y0
            and region.x1 + 1 >= b.x1
            and region.y1 + 1 >= b.y1
        ]

        def normal(t):
            return "".join(t.split()).casefold()

        if matches and normal(" ".join(t for t, _ in matches)) == normal(text):
            return aggregate_confidence(matches)
        return None

    result = []
    for block in blocks:
        if block.table:
            cells = [
                c.model_copy(update={"confidence": score(c.bbox, c.text)})
                for c in block.table.cells
            ]
            block = block.model_copy(
                update={"table": block.table.model_copy(update={"cells": cells})}
            )
        elif block.text and block.confidence is None:
            block = block.model_copy(update={"confidence": score(block.bbox, block.text)})
        result.append(block)
    return result


def preserves_existing_words(old, candidate):
    """Reject candidates that delete tokens, even when total length grows."""

    def tokenize(text):
        return re.findall(r"\w+", text.casefold())

    before, after = tokenize(old), tokenize(candidate)
    return not any(op.tag == "delete" for op in Levenshtein.opcodes(before, after))


def merge_without_deletions(old, candidate):
    """Keep primary tokens where the independently read candidate has gaps."""
    before, after = old.split(), candidate.split()

    def normalized(words):
        return [w.casefold() for w in words]

    output = []
    for op in Levenshtein.opcodes(normalized(before), normalized(after)):
        if op.tag == "delete":
            output.extend(before[op.src_start : op.src_end])
        else:
            output.extend(after[op.dest_start : op.dest_end])
    return " ".join(output)


def refine_text_blocks(blocks, lines, page_height, confirm_candidate=None):
    result = []
    for block in blocks:
        if block.type not in {
            BlockType.TEXT,
            BlockType.LIST_ITEM,
            BlockType.CAPTION,
            BlockType.FOOTNOTE,
            BlockType.HEADING,
        }:
            result.append(block)
            continue
        candidates = []
        for text, box, score in lines:
            if score < 0.85:
                continue
            cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            # A page-wide line can concatenate neighboring columns. Its center
            # alone does not establish that it belongs to this text block.
            width = max(0.01, box.x1 - box.x0)
            intersection = max(0, min(box.x1, block.bbox.x1 + 2) - max(box.x0, block.bbox.x0 - 2))
            if (
                intersection / width >= 0.9
                and block.bbox.x0 - 2 <= cx <= block.bbox.x1 + 2
                and block.bbox.y0 - 2 <= cy <= block.bbox.y1 + 2
            ):
                candidates.append((box.y0, box.x0, text, box, score))
        candidates.sort(key=lambda entry: (entry[0], entry[1]))
        candidate = " ".join(entry[2] for entry in candidates)
        old = block.text or ""
        # Accept additions only when the independent pass corroborates most
        # original words, rather than replacing unrelated nearby content.
        words = old.casefold().split()
        proposed = candidate.casefold().split()
        overlap = sum(w in proposed for w in words) / max(1, len(words))
        conservative = (
            overlap >= 0.7
            and len(candidate) >= len(old)
            and preserves_existing_words(old, candidate)
        )

        # A broken line (including falsely rotated crops) contains invented
        # tokens. Requiring every old token to survive prevents its correction.
        # Accept a complete, high-confidence geometric reread only when it
        # still anchors to a substantial part of the original block.
        def normalized(value):
            return " ".join(value.casefold().split())

        similarity = Levenshtein.normalized_similarity(normalized(old), normalized(candidate))
        corrected = (
            len(words) >= 6
            and overlap >= 0.45
            and similarity >= 0.5
            and len(candidate) >= len(old) * 0.8
            and candidates
            and min(entry[4] for entry in candidates) >= 0.95
            and candidates[0][3].y0 <= block.bbox.y0 + 6
            and candidates[-1][3].y1 >= block.bbox.y1 - 6
        )
        corroborated = False
        if (
            candidate
            and not conservative
            and not corrected
            and confirm_candidate is not None
            and len(candidates) >= 2
            and len(candidate) >= len(old)
            and min(entry[4] for entry in candidates) >= 0.9
            and candidates[0][3].y0 <= block.bbox.y0 + 6
            and candidates[-1][3].y1 >= block.bbox.y1 - 6
        ):
            corroborated = confirm_candidate(candidates)
        if candidate and (conservative or corrected or corroborated):
            block = block.model_copy(
                update={
                    "text": candidate,
                    "source_label": "line_ocr_refined",
                    "confidence": sum(len(entry[2]) * entry[4] for entry in candidates)
                    / sum(len(entry[2]) for entry in candidates),
                    "lines": [
                        Line(text=entry[2], bbox=entry[3], confidence=entry[4])
                        for entry in candidates
                    ],
                }
            )
        result.append(block)
    return recover_unassigned_lines(result, lines, page_height)


def is_mixed_word(value):
    return bool(re.search("[A-Za-z]", value) and re.search("[А-Яа-яЁё]", value))


def select_word_result(primary, alternative):
    """Allow a high-confidence Latin reading to resolve mixed-script OCR."""
    if (
        primary.txts
        and alternative.txts
        and is_mixed_word(primary.txts[0])
        and float(alternative.scores[0]) >= 0.98
    ):
        before, after = primary.txts[0].strip(), alternative.txts[0].strip()

        def visual(value):
            return value.translate(
                str.maketrans("АВЕКМНОРСТХУаекорсух", "ABEKMHOPCTXYaekopcyx")
            ).casefold()

        if (
            re.fullmatch(r"[A-Za-z0-9_./\\-]+[.,;:!?]?", after)
            and len(re.findall("[A-Za-z]", after)) >= 4
            and abs(len(before) - len(after)) <= 2
            and Levenshtein.normalized_similarity(visual(before), visual(after)) >= 0.4
        ):
            return alternative
    return alternative if alternative.txts and not primary.txts else primary


def recognize_separated_words(image, reader):
    """Independent fallback for low-confidence, widely spaced text lines."""
    mask = np.asarray(image.convert("L")) < 170
    mask[:, mask.sum(axis=0) > image.height * 0.9] = False
    rows = np.flatnonzero(mask.sum(axis=1) > 2)
    if not len(rows):
        return None
    height = int(rows[-1] - rows[0] + 1)
    spans = []
    for x in np.flatnonzero(mask.sum(axis=0) > 1):
        if spans and x - spans[-1][1] <= max(4, height * 0.22):
            spans[-1][1] = int(x)
        else:
            spans.append([int(x), int(x)])
    if not 3 <= len(spans) <= 60:
        return None
    words = []
    scores = []
    for left, right in spans:
        result = reader(
            image.crop((max(0, left - 3), 0, min(image.width, right + 4), image.height)),
            use_det=False,
            use_cls=False,
        )
        if not result.txts or float(result.scores[0]) < 0.9:
            return None
        text = result.txts[0].strip()
        if not text:
            return None
        words.append(text)
        scores.append(float(result.scores[0]))
    return " ".join(words), sum(scores) / len(scores)


def refine_mixed_words(image, text, reader):
    words = text.split()

    def suspicious(word):
        return bool((re.search("[A-Za-z]", word) and re.search("[А-Яа-яЁё]", word)))

    if not any(suspicious(w) for w in words):
        return text
    mask = np.asarray(image.convert("L")) < 170
    mask[:, mask.sum(axis=0) > image.height * 0.9] = False
    rows = np.flatnonzero(mask.sum(axis=1) > 2)
    if not len(rows):
        return text
    height = int(rows[-1] - rows[0] + 1)
    active = np.flatnonzero(mask.sum(axis=0) > 1)
    spans = []
    for x in active:
        if spans and x - spans[-1][1] <= max(4, height * 0.22):
            spans[-1][1] = int(x)
        else:
            spans.append([int(x), int(x)])
    if not spans:
        return text
    for index, (left, right) in enumerate(spans):
        if index >= len(words):
            break
        if not suspicious(words[index]):
            continue
        if len(spans) != len(words) and index != 0:
            continue
        result = reader(
            image.crop((max(0, left - 3), 0, min(image.width, right + 4), image.height)),
            use_det=False,
            use_cls=False,
        )
        if not result.txts or float(result.scores[0]) < 0.98:
            continue
        candidate = result.txts[0].strip()
        if (
            candidate
            and " " not in candidate
            and abs(len(candidate) - len(words[index])) <= 2
            and Levenshtein.normalized_similarity(words[index].casefold(), candidate.casefold())
            >= 0.2
        ):
            words[index] = candidate
    return " ".join(words)
