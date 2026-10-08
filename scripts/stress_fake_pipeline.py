from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from docpipe.config import AppConfig
from docpipe.pipeline import parse


def main() -> int:
    parser = argparse.ArgumentParser(description="Stress PDF ingestion/rasterization using the fake OCR engine.")
    parser.add_argument("--pages", type=int, default=300)
    args = parser.parse_args()
    if not 1 <= args.pages <= 300:
        parser.error("--pages must be between 1 and the configured 300-page limit")

    try:
        import psutil
    except ImportError:
        psutil = None

    peak_rss = 0
    stop_monitor = threading.Event()
    process = psutil.Process() if psutil else None

    def sample_memory() -> None:
        nonlocal peak_rss
        while not stop_monitor.wait(0.1):
            if process:
                peak_rss = max(peak_rss, process.memory_info().rss)

    with tempfile.TemporaryDirectory(prefix="docpipe-stress-") as temp_dir:
        source = Path(temp_dir) / "stress.pdf"
        pdf = canvas.Canvas(str(source), pagesize=letter)
        for page_number in range(1, args.pages + 1):
            pdf.drawString(36, 750, f"Synthetic pipeline stress page {page_number}")
            pdf.showPage()
        pdf.save()

        monitor = threading.Thread(target=sample_memory, name="docpipe-memory-sampler", daemon=True)
        monitor.start()
        started = time.perf_counter()
        try:
            document = parse(source, AppConfig(), engine="fake")
        finally:
            elapsed = time.perf_counter() - started
            stop_monitor.set()
            monitor.join(timeout=1)

    if process:
        peak_rss = max(peak_rss, process.memory_info().rss)
    failed = sum(page.status.value == "failed" for page in document.pages)
    print(json.dumps({
        "engine": "fake",
        "pages_requested": args.pages,
        "pages_processed": len(document.pages),
        "failed_pages": failed,
        "elapsed_seconds": elapsed,
        "peak_rss_bytes": peak_rss if process else None,
        "memory_sampler": "psutil" if process else "unavailable",
    }, indent=2, sort_keys=True))
    return 0 if len(document.pages) == args.pages and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
