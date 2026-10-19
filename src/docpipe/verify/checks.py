from __future__ import annotations

import re
from pathlib import Path

from docpipe.ir import BlockType, Document


def words(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower().replace("ё", "е"), flags=re.UNICODE)


def ir_words(doc: Document) -> list[str]:
    blocks = (block for page in doc.pages for block in sorted(page.blocks, key=lambda item: item.order))
    parts = []
    for block in blocks:
        if block.table and block.table.cells:
            parts.extend(cell.text for cell in sorted(block.table.cells, key=lambda c: (c.row, c.col)))
        else:
            parts.append(block.text or "")
    return words(" ".join(parts))


def text_match(doc: Document, text: str) -> dict:
    from collections import Counter
    expected = ir_words(doc)
    actual = words(text)
    wanted,found=Counter(expected),Counter(actual)
    matched=sum((wanted & found).values())
    return {
        "precision": matched / max(1, len(actual)),
        "recall": matched / max(1, len(expected)),
        "missing": list((wanted-found).elements())[:50],
        "extra": list((found-wanted).elements())[:50],
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


def _verify_docx(doc: Document, out_dir: Path, checks: list[dict]) -> None:
    """Check page anchoring and native-table frame budgets, not visual fidelity."""
    from docpipe.renderers.page_size import a4_document
    doc = a4_document(doc)
    path=out_dir/'document.docx'
    if not path.is_file():
        checks.append(_check('docx_structure','FAIL','document.docx is missing'))
        return
    import xml.etree.ElementTree as ET
    from zipfile import ZipFile
    ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
        'wp':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
        'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
        'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
    errors=[]
    try:
        with ZipFile(path) as archive:
            root=ET.fromstring(archive.read('word/document.xml'))
            body=root.find('w:body',ns)
            sections=root.findall('.//w:sectPr',ns)
            if len(sections)!=len(doc.pages):errors.append('section/page count mismatch')
            canvases=[p for p in body.findall('w:p',ns)
                if (p.find('w:pPr/w:pStyle',ns) is not None
                    and p.find('w:pPr/w:pStyle',ns).get('{'+ns['w']+'}val')=='DocpipeAnchor')]
            if len(canvases)!=len(doc.pages):errors.append('expected one canvas anchor per source page')
            if body.findall('w:tbl',ns):errors.append('table remains in body flow')
            ids=[]
            for page,canvas in zip(doc.pages,canvases):
                for anchor in canvas.findall('.//wp:anchor',ns):
                    prop=anchor.find('wp:docPr',ns);ids.append(prop.get('id'))
                    horizontal=anchor.find('wp:positionH',ns);vertical=anchor.find('wp:positionV',ns)
                    if horizontal.get('relativeFrom')!='page' or vertical.get('relativeFrom')!='page':
                        errors.append(f'page {page.index+1}: object is not page-relative')
                    x=float(horizontal.find('wp:posOffset',ns).text)/12700
                    y=float(vertical.find('wp:posOffset',ns).text)/12700
                    extent=anchor.find('wp:extent',ns)
                    width=float(extent.get('cx'))/12700;height=float(extent.get('cy'))/12700
                    if min(x,y)<-.05 or x+width>page.width_pt+.05 or y+height>page.height_pt+.05:
                        errors.append(f'page {page.index+1}: {prop.get("name")} exceeds page')
                    for table in anchor.findall('.//w:tbl',ns):
                        rows=table.findall('w:tr',ns)
                        heights=[row.find('w:trPr/w:trHeight',ns) for row in rows]
                        if any(h is None or h.get('{'+ns['w']+'}hRule')!='exact' for h in heights):
                            errors.append('native table row can grow outside its frame')
                            continue
                        top=table.find('w:tblPr/w:tblBorders/w:top',ns)
                        bottom=table.find('w:tblPr/w:tblBorders/w:bottom',ns)
                        borders=sum(float(e.get('{'+ns['w']+'}sz','0'))/8
                                    for e in (top,bottom) if e is not None)
                        # Adjacent rows share a horizontal border; count one
                        # thickness per row, matching the rendered grid pitch.
                        shared_border=borders/2
                        required=sum(float(h.get('{'+ns['w']+'}val'))/20 for h in heights)+len(rows)*shared_border+1
                        if required>height+.1:errors.append('native table exceeds frame height budget')
            if len(ids)!=len(set(ids)):errors.append('duplicate drawing object identifiers')
            tables=root.findall('.//w:tbl',ns)
            expected=sum(b.table is not None for p in doc.pages for b in p.blocks)
            if len(tables)!=expected:errors.append('native table count mismatch')
        checks.append(_check('docx_structure','FAIL' if errors else 'PASS',
            '; '.join(errors[:10]) if errors else f'{len(canvases)} page anchors; {len(tables)} native tables; frame budgets valid'))
    except Exception as exc:
        checks.append(_check('docx_structure','FAIL',f'{type(exc).__name__}: {exc}'))


def _load_document(target: Path) -> tuple[Document, Path]:
    if target.is_dir():
        json_path, out_dir = target / "document.json", target
    else:
        json_path, out_dir = target, target.parent
    if not json_path.is_file():
        raise FileNotFoundError(f"document.json not found: {json_path}")
    return Document.model_validate_json(json_path.read_text(encoding="utf-8")), out_dir


def _figure_leakage(doc: Document, *, pictograms: bool = False) -> list[dict]:
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
                    # A small pictogram (e.g. cutlery on a train ticket) can also
                    # produce an isolated cross-shaped OCR glyph. Geometry alone
                    # does not prove text leakage. Preserve both representations
                    # and request human review; words/numbers and large figures
                    # remain blocking failures.
                    ambiguous = (
                        block.text.strip() in {"X", "x", "Х", "х", "×", "+"}
                        and 0 < a.x1 - a.x0 <= 12
                        and 0 < a.y1 - a.y0 <= 12
                        and 0 < b.x1 - b.x0 <= 24
                        and 0 < b.y1 - b.y0 <= 36
                        and figure.source_label not in {"ocr_image_fallback", "graphic_field"}
                    )
                    if ambiguous != pictograms:
                        continue
                    leaks.append({"page": page.index + 1, "block": block.id, "figure": figure.id})
                    break
    return leaks


def _toc_coverage(doc: Document) -> list[str]:
    """Find contents pages where OCR captured the title but lost its entries."""
    problems: list[str] = []
    for page in doc.pages:
        headings = [
            block for block in page.blocks
            if block.text and "содержание" in block.text.casefold()
        ]
        for heading in headings:
            following: list[str] = []
            # Some engines return a whole page as one multiline text block.
            # Preserve entries that follow the title inside that same block.
            remainder = re.split("содержание", heading.text or "", maxsplit=1, flags=re.IGNORECASE)
            if len(remainder) > 1 and remainder[1].strip():
                following.append(remainder[1].strip())
            for block in page.blocks:
                if block is heading or block.bbox.y0 < heading.bbox.y0:
                    continue
                if block.text and block.text.strip():
                    following.extend(line.strip() for line in block.text.splitlines() if line.strip())
                if block.table:
                    following.extend(cell.text.strip() for cell in block.table.cells if cell.text.strip())
            recognized = " ".join(following)
            # A title or stray page number alone is not a recognized TOC.
            if len(recognized) < 60 or len(following) < 3:
                problems.append(f"page {page.index + 1}: contents heading has only {len(following)} recognized entries")
    return problems


def _verify_pdf(doc: Document, out_dir: Path, checks: list[dict]) -> None:
    pdf_path = out_dir / "document-positional.pdf"
    if not pdf_path.is_file():
        checks.append(_check("searchable_pdf", "FAIL", "document-positional.pdf is missing"))
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
    try:
        import pikepdf
        hidden = []
        with pikepdf.Pdf.open(pdf_path) as reconstructed:
            for index, page in enumerate(reconstructed.pages):
                if any(str(item.operator) == 'Tr' and int(item.operands[0]) == 3
                       for item in pikepdf.parse_content_stream(page)):
                    hidden.append(index + 1)
        checks.append(_check('visible_pdf_text','FAIL' if hidden else 'PASS',
                             f'invisible text pages={hidden}' if hidden else
                             'reconstructed PDF uses visible text; no invisible OCR overlay'))
    except Exception as exc:
        checks.append(_check('visible_pdf_text','FAIL',f'cannot inspect PDF content: {exc}'))
    checks.append(_check("searchable_pages", "PASS" if page_count == len(doc.pages) else "FAIL",
                         f"PDF={page_count}, IR={len(doc.pages)}"))
    extracted = "\n".join(extracted_pages)
    from docpipe.stages.image_fallback import has_fallback
    if not extracted.strip() and has_fallback(doc):
        checks.append(_check('searchable_text','WARN','text regions intentionally preserved as images'))
    elif not extracted.strip():
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
    toc_errors = _toc_coverage(doc)
    # Keep a recoverable OCR omission visible without making every artifact
    # inaccessible to the user. A missing TOC is a quality warning, not a
    # structural corruption of the generated document bundle.
    checks.append(_check("table_of_contents", "WARN" if toc_errors else "PASS",
                         "; ".join(toc_errors) if toc_errors else "contents pages include recognized entries"))
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
    elif any(b.source_label=='ocr_image_fallback' for p in doc.pages for b in p.blocks):
        checks.append(_check('ocr_text','WARN','unreadable regions preserved as images in experimental mode'))
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
    pictograms = _figure_leakage(doc, pictograms=True)
    checks.append(_check("figure_pictogram_overlap", "WARN" if pictograms else "PASS",
                         f"ambiguous glyphs preserved; review={pictograms}" if pictograms
                         else "no ambiguous pictogram glyphs"))

    flow_errors: list[str] = []
    for name in ("document.md", "document.html"):
        path = out_dir / name
        if not path.is_file():
            flow_errors.append(f"{name} missing")
        else:
            content = path.read_text(encoding="utf-8", errors="replace")
            flow_errors.extend(f"{name}: {item.figure.file} absent" for item in figures
                               if item.figure and item.figure.file and item.figure.file not in content)
    checks.append(_check("figure_flow", "FAIL" if flow_errors else "PASS",
                         "; ".join(flow_errors[:10]) if flow_errors else "figures referenced in Markdown and HTML"))

    _verify_pdf(doc, out_dir, checks)
    import json
    policy_path=out_dir/'output-policy.json'
    policy=json.loads(policy_path.read_text(encoding='utf-8')) if policy_path.is_file() else {}
    if policy.get('docx_enabled',True):
        _verify_docx(doc, out_dir, checks)
    else:
        checks.append(_check('docx_structure','SKIP','DOCX generation disabled by output policy'))
    fallback_count=sum(b.source_label=='ocr_image_fallback' for p in doc.pages for b in p.blocks)
    checks.append(_check('image_fallback','WARN' if fallback_count else 'PASS',
        f'{fallback_count} text regions intentionally stored as source crops' if fallback_count else 'no text-image fallback'))
    from docpipe.style_review import review_items
    pending=review_items(doc)
    checks.append(_check('typography_review','WARN' if pending else 'PASS',
        f'{len(pending)} fragments need review; output is preliminary' if pending else
        'no ambiguity markers; exact visual fidelity requires separate validation'))
    import json
    review_warnings=[]
    export_errors=[]
    for name in ('review-before.json','review-after.json','review-formatting.json'):
        path=out_dir/name
        if path.is_file():
            try:
                report=json.loads(path.read_text(encoding='utf-8'))
                export_errors.extend(f'{name}: {item.get("kind")}' for item in report.get('issues',[])
                    if item.get('kind') in {'docx_text_missing','pdf_text_missing','pdf_page_count',
                        'pdf_page_missing','docx_read_error','pdf_read_error'})
                if report.get('review_required'):
                    review_warnings.append(f'{name}: {report.get("status","unknown")}')
            except (OSError, ValueError, AttributeError):
                review_warnings.append(f'{name}: unreadable review report')
    checks.append(_check('document_review','WARN' if review_warnings else 'PASS',
        '; '.join(review_warnings) if review_warnings else 'no pending review flags in available reports'))
    checks.append(_check('export_text_integrity','FAIL' if export_errors else 'PASS',
        '; '.join(export_errors) if export_errors else 'no deterministic export loss reported'))
    required = ("document.md", "document.html", "document-flow.pdf")
    missing = [name for name in required if not (out_dir / name).is_file()]
    checks.append(_check("artifacts", "FAIL" if missing else "PASS",
                         f"missing={missing}" if missing else "Markdown, HTML, and flow PDF present"))

    statuses = [item["status"] for item in checks]
    result = "FAIL" if "FAIL" in statuses else "WARN" if "WARN" in statuses else "PASS"
    return {"ok": result != "FAIL", "result": result, "checks": checks}
