from __future__ import annotations

import re
from pathlib import Path

from docpipe.ir import BlockType, Document


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower().replace("ё", "е"), flags=re.UNICODE)


def ir_words(doc: Document) -> list[str]:
    blocks = (block for page in doc.pages for block in sorted(page.blocks, key=lambda item: item.order))
    return words(" ".join(block.text or "" for block in blocks))


def text_match(doc: Document, text: str) -> dict:
    expected = ir_words(doc)
    actual = words(text)
    expected_set, actual_set = set(expected), set(actual)
    return {
        "precision": sum(word in expected_set for word in actual) / max(1, len(actual)),
        "recall": sum(word in actual_set for word in expected) / max(1, len(expected)),
        "missing": [word for word in expected if word not in actual_set][:50],
        "extra": [word for word in actual if word not in expected_set][:50],
    }


def figure_in_flow(doc: Document, output_text: str) -> dict:
    figures = [block for page in doc.pages for block in page.blocks if block.type == BlockType.FIGURE]
    counts = [
        {"file": block.figure.file if block.figure else "", "count": output_text.count(block.figure.file) if block.figure else 0}
        for block in figures
    ]
    return {"ok": all(item["count"] == 1 for item in counts), "figures": counts}


def _check(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def _load_document(target: Path) -> tuple[Document, Path]:
    if target.is_dir():
        json_path, out_dir = target / "document.json", target
    else:
        json_path, out_dir = target, target.parent
    if not json_path.is_file():
        raise FileNotFoundError(f"document.json not found: {json_path}")
    return Document.model_validate_json(json_path.read_text(encoding="utf-8")), out_dir


def _figure_leakage(doc: Document) -> list[dict]:
    leaks: list[dict] = []
    tolerance = 2.0
    for page in doc.pages:
        figures = [block for block in page.blocks if block.type == BlockType.FIGURE]
        for block in page.blocks:
            if block.type in {BlockType.FIGURE, BlockType.CAPTION, BlockType.TABLE} or not (block.text or "").strip():
                continue
            for figure in figures:
                a, b = block.bbox, figure.bbox
                if (a.x0 >= b.x0 - tolerance and a.x1 <= b.x1 + tolerance
                        and a.y0 >= b.y0 - tolerance and a.y1 <= b.y1 + tolerance):
                    leaks.append({"page": page.index + 1, "block": block.id, "figure": figure.id})
                    break
    return leaks


def _verify_pdf(doc: Document, out_dir: Path, checks: list[dict]) -> None:
    pdf_path = out_dir / "document-searchable.pdf"
    if not pdf_path.is_file():
        checks.append(_check("searchable_pdf", "FAIL", "document-searchable.pdf is missing"))
        return
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            page_count = len(pdf)
            extracted_pages: list[str] = []
            for index in range(page_count):
                page = pdf[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        extracted_pages.append(text_page.get_text_range())
                    finally:
                        text_page.close()
                finally:
                    page.close()
        finally:
            pdf.close()
    except Exception as exc:
        checks.append(_check("searchable_pdf", "FAIL", f"cannot open/extract PDF: {exc}"))
        return

    checks.append(_check("searchable_pdf", "PASS", f"opened; pages={page_count}"))
    checks.append(_check("searchable_pages", "PASS" if page_count == len(doc.pages) else "FAIL",
                         f"PDF={page_count}, IR={len(doc.pages)}"))
    extracted = "\n".join(extracted_pages)
    if not extracted.strip():
        checks.append(_check("searchable_text", "FAIL", "PDF has no extractable text"))
    else:
        recall = text_match(doc, extracted)["recall"] if ir_words(doc) else 1.0
        status = "PASS" if recall >= 0.5 else "WARN" if recall >= 0.2 else "FAIL"
        checks.append(_check("searchable_text", status, f"extracted_chars={len(extracted)}, IR token recall={recall:.3f}"))
    has_cyrillic = bool(re.search(r"[\u0400-\u04ff]", " ".join(ir_words(doc))))
    has_pdf_cyrillic = bool(re.search(r"[\u0400-\u04ff]", extracted))
    checks.append(_check("fonts_cyrillic", "FAIL" if has_cyrillic and not has_pdf_cyrillic else "PASS",
                         "Cyrillic text is extractable" if has_cyrillic and has_pdf_cyrillic else
                         "Cyrillic is missing from PDF" if has_cyrillic else "no Cyrillic in IR"))


def verify_document(target: Path | str) -> dict:
    """Validate a parse output directory and return a PASS/WARN/FAIL report."""
    target = Path(target)
    checks: list[dict] = []
    try:
        doc, out_dir = _load_document(target)
    except Exception as exc:
        return {"ok": False, "result": "FAIL", "checks": [_check("ir_valid", "FAIL", str(exc))]}

    checks.append(_check("ir_valid", "PASS", f"schema_version={doc.schema_version}, engine={doc.engine.name}"))
    text_chars = sum(
        len((block.text or "").strip())
        + (
            sum(len(cell.text.strip()) for cell in block.table.cells)
            if block.table is not None
            else 0
        )
        for page in doc.pages
        for block in page.blocks
    )
    if text_chars:
        checks.append(_check("ocr_text", "PASS", f"recognized_text_chars={text_chars}"))
    elif doc.engine.name == "fake":
        checks.append(_check("ocr_text", "WARN", "fake test engine does not perform OCR"))
    else:
        checks.append(_check("ocr_text", "FAIL", "OCR result contains no text or table-cell text"))
    if not doc.pages:
        checks.append(_check("page_count", "FAIL", "document contains no pages"))
    else:
        failed = [page.index + 1 for page in doc.pages if page.status.value == "failed"]
        degraded = [page.index + 1 for page in doc.pages if page.status.value == "degraded"]
        status = "FAIL" if failed else "WARN" if degraded else "PASS"
        checks.append(_check("page_count", status,
                             f"{len(doc.pages)} pages; failed={failed}; degraded={degraded}"))

    bbox_errors = [f"page {page.index + 1}, block {block.id}" for page in doc.pages
                   for block in page.blocks
                   if not (0 <= block.bbox.x0 <= block.bbox.x1 <= page.width_pt
                           and 0 <= block.bbox.y0 <= block.bbox.y1 <= page.height_pt)]
    checks.append(_check("bbox", "FAIL" if bbox_errors else "PASS",
                         "; ".join(bbox_errors[:10]) if bbox_errors else "all block boxes are within page bounds"))

    table_errors: list[str] = []
    tables = [(page, block) for page in doc.pages for block in page.blocks if block.type == BlockType.TABLE]
    for page, block in tables:
        table = block.table
        if table is None:
            table_errors.append(f"{block.id}: missing table payload")
            continue
        for cell in table.cells:
            if (cell.row + cell.rowspan > table.n_rows
                    or cell.col + cell.colspan > table.n_cols):
                table_errors.append(f"{block.id}: cell ({cell.row},{cell.col}) span exceeds table dimensions")
            box = cell.bbox
            if not (0 <= box.x0 <= box.x1 <= page.width_pt
                    and 0 <= box.y0 <= box.y1 <= page.height_pt):
                table_errors.append(f"{block.id}: cell ({cell.row},{cell.col}) box exceeds page bounds")
    checks.append(_check("tables", "FAIL" if table_errors else "PASS",
                         "; ".join(table_errors[:10]) if table_errors else f"{len(tables)} tables; cells within bounds"))

    figures = [block for page in doc.pages for block in page.blocks if block.type == BlockType.FIGURE]
    figure_errors: list[str] = []
    for block in figures:
        rel = Path(block.figure.file) if block.figure else None
        if rel is None or not rel.parts or rel.is_absolute() or ".." in rel.parts:
            figure_errors.append(f"{block.id}: missing or unsafe image path")
        elif not (out_dir / rel).is_file():
            figure_errors.append(f"{block.id}: missing image {rel}")
    checks.append(_check("figure_files", "FAIL" if figure_errors else "PASS",
                         "; ".join(figure_errors[:10]) if figure_errors else f"{len(figures)} figure files present"))

    missing_captions = [f"{block.id}: {block.figure.caption_id}" for page in doc.pages
                        for block in page.blocks if block.figure and block.figure.caption_id
                        and block.figure.caption_id not in {item.id for item in page.blocks}]
    checks.append(_check("caption_links", "FAIL" if missing_captions else "PASS",
                         "; ".join(missing_captions[:10]) if missing_captions else "caption references resolve"))
    leaks = _figure_leakage(doc)
    checks.append(_check("figure_leakage", "FAIL" if leaks else "PASS", f"leaks={len(leaks)}"))

    flow_errors: list[str] = []
    for name in ("document.md", "document.html"):
        path = out_dir / name
        if not path.is_file():
            flow_errors.append(f"{name} missing")
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        flow_errors.extend(f"{name}: {item.figure.file} absent" for item in figures
                           if item.figure and item.figure.file and item.figure.file not in content)
    checks.append(_check("figure_flow", "FAIL" if flow_errors else "PASS",
                         "; ".join(flow_errors[:10]) if flow_errors else "figures referenced in Markdown and HTML"))

    _verify_pdf(doc, out_dir, checks)
    required = ("document.md", "document.html", "document-flow.pdf")
    missing = [name for name in required if not (out_dir / name).is_file()]
    checks.append(_check("artifacts", "FAIL" if missing else "PASS",
                         f"missing={missing}" if missing else "Markdown, HTML, and flow PDF present"))

    statuses = [item["status"] for item in checks]
    result = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    return {"ok": result != "FAIL", "result": result, "checks": checks}
