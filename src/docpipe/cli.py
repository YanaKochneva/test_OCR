from __future__ import annotations

import argparse
import json
from pathlib import Path

from docpipe.config import load_config


def main() -> int:
    p = argparse.ArgumentParser(prog="docpipe")
    sub = p.add_subparsers(dest="cmd", required=True)

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
        default=["md", "html", "pdf-flow", "pdf-searchable"],
    )

    v = sub.add_parser("verify")
    v.add_argument("document", type=Path)
    v.add_argument("--strict", action="store_true")

    m = sub.add_parser("models")
    ms = m.add_subparsers(dest="models_cmd", required=True)
    ms.add_parser("list")
    ms.add_parser("download")

    e = sub.add_parser("eval")
    e.add_argument("reference", type=Path, help="Reference DOCX file")
    e.add_argument("ir", type=Path, help="Generated document.json")
    e.add_argument("--output", type=Path, help="Optional JSON report path")

    a = p.parse_args()
    cfg = load_config(getattr(a, "config", None))

    if a.cmd == "doctor":
        from docpipe.doctor import run_doctor
        report = run_doctor(cfg)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if a.cmd == "parse":
        from docpipe.pipeline import parse
        from docpipe.renderers import (
            render_docx, render_html, render_markdown, render_pdf_flow,
            render_pdf_positional, render_pdf_searchable,
        )
        a.out.mkdir(parents=True, exist_ok=True)
        doc = parse(a.file, cfg, engine=a.engine, out_dir=a.out)
        (a.out / "document.json").write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        outputs = set(a.outputs) | {"md", "html", "pdf-flow", "pdf-searchable"}
        if "md" in outputs:
            render_markdown(doc, a.out / "document.md")
        if "html" in outputs:
            render_html(doc, a.out / "document.html")
        if "pdf-flow" in outputs:
            render_pdf_flow(doc, a.out / "document-flow.pdf", a.out)
        if "pdf-searchable" in outputs:
            from docpipe.io.normalize import normalize_input
            normalized = normalize_input(a.file, cfg)
            try:
                render_pdf_searchable(doc, normalized, a.out / "document-searchable.pdf")
            finally:
                if normalized != a.file:
                    normalized.unlink(missing_ok=True)
        if "pdf-positional" in outputs:
            render_pdf_positional(doc, a.out / "document-positional.pdf", a.out)
        if "docx" in outputs:
            render_docx(doc, a.out / "document.docx", a.out)
        from docpipe.verify.checks import verify_document
        verification = verify_document(a.out)
        (a.out / "verification.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        from docpipe.quality import document_metrics
        metrics = document_metrics(doc)
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
            print(json.dumps({"engine": cfg.api.default_engine, "offline": cfg.offline}, ensure_ascii=False))
            return 0
        print("Загрузка моделей выполняется только явно и требует доступного источника моделей.")
        return 0

    if a.cmd == "eval":
        from docpipe.evaluation import compare_docx_reference
        report = compare_docx_reference(a.reference, a.ir)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        if a.output:
            a.output.parent.mkdir(parents=True, exist_ok=True)
            a.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
