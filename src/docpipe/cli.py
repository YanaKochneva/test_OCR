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
        default=["md", "html", "pdf-flow", "pdf-positional"],
    )

    v = sub.add_parser("verify")
    v.add_argument("document", type=Path)
    v.add_argument("--strict", action="store_true")

    m = sub.add_parser("models")
    ms = m.add_subparsers(dest="models_cmd", required=True)
    ms.add_parser("list")
    ms.add_parser("download")

    a = p.parse_args()
    cfg = load_config(getattr(a, "config", None))

    if a.cmd == "doctor":
        from docpipe.doctor import run_doctor
        report = run_doctor(cfg)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if a.cmd == "parse":
        from docpipe.pipeline import parse
        a.out.mkdir(parents=True, exist_ok=True)
        doc = parse(
            a.file, cfg, engine=a.engine, out_dir=a.out,
            progress_callback=lambda done, total: print(
                f"OCR pages: {done}/{total}", flush=True
            ),
        )
        from docpipe.output import render_bundle
        doc=render_bundle(doc,cfg,a.out,a.outputs,a.file)
        from docpipe.review import review_after_render
        post_review=review_after_render(doc, cfg, a.out)
        from docpipe.verify.checks import verify_document
        from docpipe.style_review import write_style_review
        write_style_review(doc,a.out)
        verification = verify_document(a.out)
        (a.out / "verification.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        from docpipe.quality import document_metrics
        metrics = document_metrics(doc)
        pre_path=a.out/'review-before.json'
        pre_review=json.loads(pre_path.read_text(encoding='utf-8')) if pre_path.is_file() else {}
        metrics['review_required']=bool(metrics['review_required'] or
            pre_review.get('review_required') or post_review.get('review_required'))
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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
