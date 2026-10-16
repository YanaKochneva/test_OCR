"""Send documents to DocPipe and save the recognized document package.

Client dependency: pip install httpx
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import uuid
import zipfile

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents", nargs="+", type=Path)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--output", type=Path, default=Path("recognized.zip"))
    parser.add_argument("--timeout", type=float, default=660)
    args = parser.parse_args()
    key = os.environ.get("DOCPIPE_API_KEY")
    if not key:
        parser.error("Set DOCPIPE_API_KEY")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.output.resolve() in [path.resolve() for path in args.documents]:
        parser.error("Output must differ from input documents")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + "." + uuid.uuid4().hex + ".part")
    try:
        with ExitStack() as stack:
            files = [("files", (path.name, stack.enter_context(path.open("rb"))))
                     for path in args.documents]
            with httpx.Client(timeout=args.timeout) as client:
                with client.stream("POST", args.url.rstrip("/") + "/v1/recognize",
                                   headers={"X-API-Key": key}, files=files) as response:
                    if response.status_code != 200:
                        response.read()
                        print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
                        return 1
                    if response.headers.get("content-type", "").split(";")[0] != "application/zip":
                        raise ValueError("Expected application/zip response")
                    with temporary.open("wb") as output:
                        for chunk in response.iter_bytes():
                            output.write(chunk)
        with zipfile.ZipFile(temporary) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("status") not in {"completed", "partial"}:
                raise ValueError("Invalid batch manifest")
        temporary.replace(args.output)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        print(f"Saved: {args.output}")
        return 0 if manifest["status"] == "completed" else 2
    except (OSError, httpx.HTTPError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
