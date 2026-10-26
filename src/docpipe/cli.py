from __future__ import annotations

import argparse
import json
from pathlib import Path

from docpipe.config import load_config


def main() -> int:
    p = argparse.ArgumentParser(prog="docpipe")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("nemotron-only", help="Full-page Nemotron only; raw response as PDF")
    n.add_argument("file", type=Path)
    n.add_argument("--out", type=Path, required=True, help="Output .pdf file")
    n.add_argument("--model", type=Path, default=Path("models/nemotron-3.5-parse"))
    n.add_argument("--device", default="auto", help="auto, cpu, cuda or cuda:N")
    n.add_argument("--dpi", type=int, default=150)
    n.add_argument("--max-new-tokens", type=int, default=8744)

    d = sub.add_parser("doctor")
    d.add_argument("--config", type=Path)

    q = sub.add_parser("parse")
    q.add_argument("file", type=Path)
    q.add_argument("--engine", default="docling")
    q.add_argument("--out", type=Path, required=True)
    q.add_argument("--config", type=Path)
    q.add_argument(
        "--outputs",
        nargs="+",
        default=["md", "html", "pdf-flow", "pdf-positional"],
    )

    v = sub.add_parser("verify")
    v.add_argument("document", type=Path)
    v.add_argument("--strict", action="store_true")

    r = sub.add_parser("review-decision")
    r.add_argument("out", type=Path)
    r.add_argument("issue_id")
    r.add_argument("status", choices=["confirmed", "dismissed", "unresolved"])
    r.add_argument("--actor", required=True)
    r.add_argument("--source-sha256", required=True)
    r.add_argument("--note", default="")

    m = sub.add_parser("models")
    ms = m.add_subparsers(dest="models_cmd", required=True)
    ms.add_parser("list")
    ms.add_parser("download")

    a = p.parse_args()
    if a.cmd == "nemotron-only":
        from docpipe.nemotron_only import recognize

        try:
            result = recognize(a.file, a.out, a.model, device=a.device,
                               dpi=a.dpi, max_new_tokens=a.max_new_tokens)
        except (ValueError, FileNotFoundError, RuntimeError) as exc:
            p.exit(1, f"Nemotron: {exc}\n")
        print(result)
        return 0

    cfg = load_config(getattr(a, "config", None))

    if a.cmd == "review-decision":
        from docpipe.ocr_issues import record_decision

        event = record_decision(a.out, a.issue_id, a.status, a.actor, a.source_sha256, a.note)
        print(json.dumps(event, ensure_ascii=False))
        return 0

    if a.cmd == "doctor":
        from docpipe.doctor import run_doctor

        report = run_doctor(cfg)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if a.cmd == "parse":
        from docpipe.pipeline import parse

        a.out.mkdir(parents=True, exist_ok=True)
        doc = parse(
            a.file,
            cfg,
            engine=a.engine,
            out_dir=a.out,
            progress_callback=lambda done, total: print(f"OCR pages: {done}/{total}", flush=True),
        )
        from docpipe.output import render_bundle

        doc = render_bundle(doc, cfg, a.out, a.outputs, a.file)
        from docpipe.review import review_after_render

        post_review = review_after_render(doc, cfg, a.out)
        from docpipe.style_review import write_style_review
        from docpipe.verify.checks import verify_document

        write_style_review(doc, a.out)
        verification = verify_document(a.out)
        (a.out / "verification.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        from docpipe.quality import document_metrics

        metrics = document_metrics(doc)
        pre_path = a.out / "review-before.json"
        pre_review = json.loads(pre_path.read_text(encoding="utf-8")) if pre_path.is_file() else {}
        metrics["review_required"] = bool(
            metrics["review_required"]
            or pre_review.get("review_required")
            or post_review.get("review_required")
        )
        ocr_review = json.loads((a.out / "review" / "issues.json").read_text(encoding="utf-8"))
        metrics["review_required"] = metrics["review_required"] or ocr_review["review_required"]
        metrics["ocr_review_fragments"] = len(ocr_review["issues"])
        metrics["verification"] = verification["result"]
        (a.out / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if not verification["ok"]:
            print(json.dumps(verification, ensure_ascii=False, indent=2))
            return 1
        return 0

    if a.cmd == "verify":
        from docpipe.verify.checks import verify_document

        report = verify_document(a.document)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        failed = report.get("result") == "FAIL" or not report.get("ok", False)
        warned = report.get("result") == "WARN"
        return 1 if failed or (a.strict and warned) else 0

    if a.cmd == "models":
        if a.models_cmd == "list":
            print(
                json.dumps(
                    {"engine": cfg.api.default_engine, "offline": cfg.offline}, ensure_ascii=False
                )
            )
            return 0
        print("Загрузка моделей выполняется только явно и требует доступного источника моделей.")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
