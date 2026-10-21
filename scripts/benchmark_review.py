"""Compare unchanged Docling OCR against a configured crop reviewer, then score."""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pypdfium2 as pdfium
from rapidfuzz.distance import Levenshtein
from docpipe.config import AppConfig, load_config
from docpipe.ir import Document
from docpipe.pipeline import parse
from docpipe.review import review_before_render


def normalize(text):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def page_text(page):
    return "\n".join(
        "\n".join(c.text for c in block.table.cells) if block.table else block.text or ""
        for block in sorted(page.blocks, key=lambda b: b.order)
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("scan", type=Path)
    p.add_argument("reference", type=Path)
    p.add_argument("--config", type=Path, default=Path("configs/nemotron-review.yaml"))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--baseline", type=Path, help="Reuse a saved unreviewed document.json")
    p.add_argument("--max-calls", type=int, help="Override crop-call budget, e.g. 2 for a smoke test")
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cfg = load_config(args.config)
    if args.max_calls is not None:
        if not 2 <= args.max_calls <= 600:
            p.error("--max-calls must be between 2 and 600")
        cfg.review.max_calls = args.max_calls
    if args.baseline:
        baseline = Document.model_validate_json(args.baseline.read_text("utf-8"))
        from docpipe.pipeline import _sha256
        if baseline.source.sha256 != _sha256(args.scan):
            raise ValueError("Baseline does not belong to this scan")
    else:
        base_cfg = AppConfig(force_ocr="always", offline=True)
        baseline = parse(args.scan, base_cfg, out_dir=args.out / "baseline",
                         progress_callback=lambda n, total: print(f"Docling {n}/{total}", flush=True))
    (args.out / "baseline.json").write_text(baseline.model_dump_json(indent=2), encoding="utf-8")
    if any(page.status.value == "failed" for page in baseline.pages):
        raise RuntimeError("Baseline has failed pages; refusing to score incomplete OCR")
    reviewed, report = review_before_render(baseline.model_copy(deep=True), cfg, args.out)
    (args.out / "reviewed.json").write_text(reviewed.model_dump_json(indent=2), encoding="utf-8")
    # The reference is never passed to the OCR pipeline or crop reviewer.
    rows = []
    differences = []
    with pdfium.PdfDocument(args.reference) as reference:
        if len(reference) != len(baseline.pages):
            raise ValueError("Reference and scan page counts differ")
        for i, (before, after) in enumerate(zip(baseline.pages, reviewed.pages)):
            page = reference[i]
            layer = page.get_textpage()
            try:
                expected = normalize(layer.get_text_range())
            finally:
                layer.close()
                page.close()
            if not expected:
                raise ValueError(f"Reference page {i + 1} has no text")
            row = {"page": i + 1, "reference_chars": len(expected),
                   "baseline_edits": Levenshtein.distance(expected, normalize(page_text(before))),
                   "reviewed_edits": Levenshtein.distance(expected, normalize(page_text(after)))}
            rows.append(row)
            for label, actual in (("baseline", before), ("reviewed", after)):
                hypothesis = normalize(page_text(actual))
                differences.extend(
                    {"page": i + 1, "stage": label, "operation": tag,
                     "expected": expected[a:b], "actual": hypothesis[c:d],
                     "reference_context": expected[max(0, a - 25):b + 25]}
                    for tag, a, b, c, d in Levenshtein.opcodes(expected, hypothesis)
                    if tag != "equal"
                )
    total = sum(r["reference_chars"] for r in rows)
    completed = report.get("units_completed", 0)
    metrics = dict(scope="whole_document_in_reading_order", pages=rows,
                   reviewer=cfg.review.provider, units_completed=completed,
                   normalization="NFC and collapsed whitespace; preserve case, punctuation, all content",
                   baseline_cer=sum(r["baseline_edits"] for r in rows) / total,
                   reviewed_cer=(sum(r["reviewed_edits"] for r in rows) / total) if completed else None,
                   reviewer_status=report["status"], corrections=len(report["corrections"]),
                   calls=report["calls"])
    (args.out / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "differences.json").write_text(json.dumps(differences, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
