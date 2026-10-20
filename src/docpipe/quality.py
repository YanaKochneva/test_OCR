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
    from docpipe.style_review import review_items

    pending = review_items(doc)
    from docpipe.ocr_issues import collect_issues

    ocr_pending = [i for i in collect_issues(doc) if i["severity"] != "info"]
    block_counts = Counter(block.type.value for page in doc.pages for block in page.blocks)
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
        "ocr_review_fragments": len(ocr_pending),
        "review_required": bool(ocr_pending)
        or bool(pending)
        or any(b.source_label == "ocr_image_fallback" for p in doc.pages for b in p.blocks),
        "typography_review_fragments": len(pending),
        "cer": None,
        "wer": None,
        "accuracy_note": "CER/WER require comparison against a labeled reference; successful processing does not measure recognition accuracy.",
    }
