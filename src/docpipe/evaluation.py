from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from docx import Document as WordDocument
from docx.text.paragraph import Paragraph
from docx.table import Table
from docx.oxml.ns import qn
from rapidfuzz.distance import Levenshtein


def _tokens(text: str) -> list[str]:
    normalized = text.casefold().replace("ё", "е")
    return re.findall(r"\w+", normalized, flags=re.UNICODE)


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip()


def _reference_text(path: Path) -> str:
    reference = WordDocument(path)
    parts: list[str] = []
    def visit(node):
        if node.tag == qn("w:tbl"):
            item = Table(node, reference)
            seen = set()
            for row in item.rows:
                for cell in row.cells:
                    if cell._tc not in seen:
                        parts.append(cell.text)
                        seen.add(cell._tc)
        elif node.tag == qn("w:p"):
            parts.append(Paragraph(node, reference).text)
        elif node.tag in {qn("w:sdt"), qn("w:sdtContent")}:
            # Word's generated table of contents is commonly inside a content
            # control, which iter_inner_content() does not expose.
            for child in node:
                visit(child)
    for node in reference.element.body:
        visit(node)
    return " ".join(parts)


def _ir_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for page in data.get("pages", []):
        for block in sorted(page.get("blocks", []), key=lambda b: b.get("order", 0)):
            table = block.get("table") or {}
            cells = table.get("cells", [])
            if cells:
                parts.extend(cell.get("text") or "" for cell in sorted(
                    cells, key=lambda c: (c.get("row", 0), c.get("col", 0))
                ))
            else:
                parts.append(block.get("text") or "")
    return " ".join(parts)


def compare_docx_reference(reference_path: Path | str, ir_path: Path | str) -> dict[str, Any]:
    """Compare OCR with a DOCX reference using token overlap, CER, and WER."""
    reference = _reference_text(Path(reference_path))
    ir = _ir_text(json.loads(Path(ir_path).read_text(encoding="utf-8")))
    return compare_text(reference, ir)


def compare_text(reference: str, ir: str) -> dict[str, Any]:
    """Score ordered text; empty references are invalid rather than perfect OCR."""
    if not _normalized_text(reference):
        raise ValueError("Reference contains no usable text")
    expected = Counter(_tokens(reference))
    actual = Counter(_tokens(ir))
    matched = sum((expected & actual).values())
    precision = matched / sum(actual.values()) if actual else 0.0
    recall = matched / sum(expected.values()) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    reference_normalized = _normalized_text(reference)
    actual_normalized = _normalized_text(ir)
    reference_words = _tokens(reference_normalized)
    actual_words = _tokens(actual_normalized)
    cer = (
        Levenshtein.distance(reference_normalized, actual_normalized) / len(reference_normalized)
        if reference_normalized
        else 0.0
    )
    wer = (
        Levenshtein.distance(reference_words, actual_words) / len(reference_words)
        if reference_words
        else 0.0
    )

    def alignment(expected, actual):
        counts = Counter(op.tag for op in Levenshtein.editops(expected, actual))
        return {"substitutions": counts["replace"], "deletions": counts["delete"],
                "insertions": counts["insert"]}

    # Strict scoring keeps case, punctuation and yo/e; only serialization
    # whitespace and Unicode canonical composition are normalized.
    strict_reference = re.sub(r"\s+", " ", unicodedata.normalize("NFC", reference)).strip()
    strict_actual = re.sub(r"\s+", " ", unicodedata.normalize("NFC", ir)).strip()
    strict_words = strict_reference.split()
    strict_hypothesis = strict_actual.split()

    return {
        "metric": "case-insensitive; normalizes ё/е and whitespace; CER/WER include text order",
        "reference_chars": len(reference_normalized),
        "ir_chars": len(actual_normalized),
        "reference_tokens": sum(expected.values()),
        "ir_tokens": sum(actual.values()),
        "matched_tokens": matched,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "cer": cer,
        "wer": wer,
        "character_errors": alignment(reference_normalized, actual_normalized),
        "word_errors": alignment(reference_words, actual_words),
        "strict_cer": Levenshtein.distance(strict_reference, strict_actual) / len(strict_reference),
        "strict_wer": Levenshtein.distance(strict_words, strict_hypothesis) / len(strict_words),
        "document_exact_match": strict_reference == strict_actual,
        "strict_policy": "NFC; case/punctuation/yo preserved; whitespace collapsed; words split on whitespace",
    }
