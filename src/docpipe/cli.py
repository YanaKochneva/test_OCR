"""Command-line entry point used by the notebook."""

import argparse
from pathlib import Path

from docpipe.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(prog="docpipe")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("parse")
    command.add_argument("file", type=Path)
    command.add_argument("--out", type=Path, required=True)
    command.add_argument("--config", type=Path)
    command.add_argument("--engine", choices=["docling"], default="docling")
    command.add_argument("--outputs", nargs="+", choices=[
        "json", "md", "html", "docx", "pdf-flow", "pdf-positional", "pdf-searchable",
    ], default=["md", "html", "pdf-flow", "pdf-positional"])
    args = parser.parse_args()
    config = load_config(args.config)

    from docpipe.pipeline import parse, render_bundle
    from docpipe.errors import EmptyDocumentError

    args.out.mkdir(parents=True, exist_ok=True)
    document = parse(args.file, config, engine=args.engine, out_dir=args.out,
        progress_callback=lambda done, total: print(f"OCR pages: {done}/{total}", flush=True))
    try:
        render_bundle(document, config, args.out, args.outputs)
    except EmptyDocumentError as exc:
        print(str(exc), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
