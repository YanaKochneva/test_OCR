from __future__ import annotations

from collections import Counter

from docpipe.ir import BlockType, Document


def recognized_text_chars(doc: Document) -> int:
    total = 0
    for page in doc.pages:
        for block in page.blocks:
            total += len((block.text or "").strip())
            if block.table is not None:
                total += sum(len(cell.text.strip()) for cell in block.table.cells)
    return total


def document_metrics(doc: Document) -> dict:
    block_counts = Counter(
        block.type.value for page in doc.pages for block in page.blocks
    )
    return {
        "engine": doc.engine.name,
        "engine_version": doc.engine.version,
        "pages": len(doc.pages),
        "pages_ok": sum(page.status.value == "ok" for page in doc.pages),
        "pages_degraded": sum(page.status.value == "degraded" for page in doc.pages),
        "pages_failed": sum(page.status.value == "failed" for page in doc.pages),
        "text_characters": recognized_text_chars(doc),
        "blocks_by_type": dict(sorted(block_counts.items())),
        "tables": block_counts.get(BlockType.TABLE.value, 0),
        "figures": block_counts.get(BlockType.FIGURE.value, 0),
        "parse_seconds": doc.timings.get("parse_total"),
        "cer": None,
        "wer": None,
        "accuracy_note": "CER/WER require a ground-truth transcript; use docpipe eval with a labeled DOCX reference.",
    }
