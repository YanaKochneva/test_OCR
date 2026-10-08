from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from docx import Document as WordDocument
from rapidfuzz.distance import Levenshtein


def _tokens(text: str) -> list[str]:
    normalized = text.casefold().replace("ё", "е")
    return re.findall(r"\w+", normalized, flags=re.UNICODE)


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip()


def _reference_text(path: Path) -> str:
    reference = WordDocument(path)
    parts: list[str] = []
    for item in reference.iter_inner_content():
        if hasattr(item, "rows"):
            for row in item.rows:
                parts.extend(cell.text for cell in row.cells)
        else:
            parts.append(item.text)
    return " ".join(parts)


def _ir_text(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for page in data.get("pages", []):
        for block in page.get("blocks", []):
            parts.append(block.get("text") or "")
            table = block.get("table") or {}
            parts.extend(cell.get("text") or "" for cell in table.get("cells", []))
    return " ".join(parts)


def compare_docx_reference(reference_path: Path | str, ir_path: Path | str) -> dict[str, Any]:
    """Compare OCR with a DOCX reference using token overlap, CER, and WER."""
    reference = _reference_text(Path(reference_path))
    ir = _ir_text(json.loads(Path(ir_path).read_text(encoding="utf-8")))
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
    }
