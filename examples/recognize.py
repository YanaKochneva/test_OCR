"""Send documents to DocPipe, download ZIP and unpack recognized documents.

Install the client dependency: python -m pip install httpx
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile

import httpx


def unpack_result(archive_path: Path, destination: Path) -> None:
    """Extract into a new folder, rejecting paths outside that folder."""
    if destination.exists():
        raise FileExistsError(f"Result folder already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="docpipe-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                name = PurePosixPath(member.filename)
                if (name.is_absolute() or ".." in name.parts
                        or "\\" in member.filename or ":" in member.filename
                        or stat.S_ISLNK(member.external_attr >> 16)):
                    raise ValueError(f"Unsafe archive path: {member.filename}")
                target = (temporary / member.filename).resolve()
                if temporary.resolve() not in target.parents:
                    raise ValueError(f"Unsafe archive path: {member.filename}")
            archive.extractall(temporary)
        temporary.rename(destination)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def recognize_documents(
    documents: list[Path],
    *,
    api_key: str,
    output: Path,
    url: str = "http://localhost:8000",
    timeout: float = 660,
    extract_dir: Path | None = None,
) -> dict:
    """Call a running service; return manifest after saving its output files.

    A partial result is saved too: inspect manifest['status'] and each document.
    HTTP, file and malformed-response errors raise exceptions to the caller.
    """
    if not documents or not api_key.strip() or timeout <= 0:
        raise ValueError("Provide input documents, API key and a positive timeout")
    if any(not path.is_file() for path in documents):
        raise ValueError("Each input document must be an existing file")
    if output.resolve() in [path.resolve() for path in documents]:
        raise ValueError("Output must differ from input documents")
    if output.exists():
        raise FileExistsError(f"Output already exists: {output}")
    if extract_dir is not None:
        if extract_dir.exists() or extract_dir.resolve() == output.resolve():
            raise ValueError("Extraction folder must be new and differ from output ZIP")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".part")
    try:
        with ExitStack() as stack:
            files = [("files", (path.name, stack.enter_context(path.open("rb"))))
                     for path in documents]
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", url.rstrip("/") + "/v1/recognize",
                                   headers={"X-API-Key": api_key}, files=files) as response:
                    if response.status_code != 200:
                        response.read()
                        raise RuntimeError(f"HTTP {response.status_code}: {response.text}")
                    content_type = response.headers.get("content-type", "").split(";")[0]
                    if content_type != "application/zip":
                        raise ValueError("Expected application/zip response")
                    with temporary.open("wb") as file:
                        for chunk in response.iter_bytes():
                            file.write(chunk)
        with zipfile.ZipFile(temporary) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if (not isinstance(manifest, dict)
                    or manifest.get("status") not in {"completed", "partial"}
                    or not isinstance(manifest.get("documents"), list)):
                raise ValueError("Invalid batch manifest")
        temporary.replace(output)
        if extract_dir is not None:
            unpack_result(output, extract_dir)
        return manifest
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents", nargs="+", type=Path, help="PDF or image files")
    parser.add_argument("--url", default=os.getenv("DOCPIPE_URL", "http://localhost:8000"))
    parser.add_argument("--output", type=Path, default=Path("recognized.zip"))
    parser.add_argument("--timeout", type=float, default=660)
    parser.add_argument("--extract-dir", type=Path, help="New folder for unpacked documents")
    parser.add_argument("--no-extract", action="store_true", help="Only download ZIP")
    args = parser.parse_args()
    key = os.environ.get("DOCPIPE_API_KEY")
    if not key:
        parser.error("Set DOCPIPE_API_KEY to the same value as the service API key")
    if args.no_extract and args.extract_dir is not None:
        parser.error("Use either --extract-dir or --no-extract")
    extract_dir = None if args.no_extract else (
        args.extract_dir or args.output.with_name(args.output.stem + "_files")
    )
    try:
        manifest = recognize_documents(
            args.documents, api_key=key, output=args.output, url=args.url,
            timeout=args.timeout, extract_dir=extract_dir,
        )
        print(f"ZIP: {args.output.resolve()}")
        if extract_dir is not None:
            print(f"Documents: {extract_dir.resolve()}")
        for document in manifest["documents"]:
            source = document.get("source_filename", "unknown")
            if document.get("status") == "completed":
                review = " (review required)" if document.get("review_required") else ""
                print(f"OK: {source} -> {document.get('directory', '')}{review}")
            else:
                print(f"FAILED: {source}: {document.get('error')}", file=sys.stderr)
        return 0 if manifest["status"] == "completed" else 2
    except (OSError, httpx.HTTPError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
