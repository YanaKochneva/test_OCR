"""Image-only GLM-OCR rereading; proposals never use reference documents."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from rapidfuzz.distance import Levenshtein

from docpipe.remote_llm import _NoRedirect, local_ollama
from docpipe.render.source import load_page_image as _page_image


class GLMReader:
    def __init__(self, config):
        self.config = config

    def recognize(self, image, timeout):
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        body = {
            "model": self.config.api_model,
            "prompt": "Text Recognition:",
            "images": [base64.b64encode(buffer.getvalue()).decode("ascii")],
            "stream": False,
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        }
        request = Request(
            self.config.ollama_url.rstrip("/") + "/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                data = response.read(2_000_001)
        except HTTPError as exc:
            raise RuntimeError(f"Ollama HTTP {exc.code}") from None
        except URLError:
            raise ConnectionError("Ollama connection failed") from None
        if len(data) > 2_000_000:
            raise ValueError("OCR response exceeds size limit")
        result = json.loads(data)
        if not result.get("done") or result.get("done_reason") != "stop":
            raise ValueError("OCR response incomplete")
        text = result.get("response")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("OCR response empty")
        return text.strip()


def candidates(document, min_chars):
    for page in document.pages:
        for block in sorted(page.blocks, key=lambda b: b.order):
            prefix = f"p{page.index + 1}:{block.id}"
            if block.table:
                for cell in block.table.cells:
                    if len(cell.text.strip()) >= min_chars:
                        yield page, f"{prefix}:c{cell.row},{cell.col}", cell.text, cell.bbox
            elif (
                block.text
                and len(block.text.strip()) >= min_chars
                and block.type.value not in {"header", "footer", "page_number"}
            ):
                yield page, prefix, block.text, block.bbox


@lru_cache(maxsize=1)
def russian_dictionary():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


def proposals(original, recognized, known_word=None):
    """Require close transcription and local word alignment; never delete content."""
    from docpipe.review import valid_correction

    known_word = known_word or russian_dictionary().word_is_known

    def normalize(value):
        return re.sub(r"\s+", " ", value).strip()

    original, recognized = normalize(original), normalize(recognized)
    if not original or not 0.65 <= len(recognized) / len(original) <= 1.45:
        return [], "length_mismatch"
    if Levenshtein.normalized_similarity(original.casefold(), recognized.casefold()) < 0.72:
        return [], "transcription_disagrees"
    left, right = original.split(), recognized.split()
    matcher = SequenceMatcher(
        None, [s.casefold() for s in left], [s.casefold() for s in right], autojunk=False
    )
    changes = []
    for tag, i0, i1, j0, j1 in matcher.get_opcodes():
        if tag != "replace" or i1 - i0 != j1 - j0:
            continue
        for before, after in zip(left[i0:i1], right[j0:j1]):
            before = before.strip('.,;:!?«»"()[]•–—')
            after = after.strip('.,;:!?«»"()[]•–—')
            if (
                before == after
                or original.count(before) != 1
                or not valid_correction(before, after)
                or not re.fullmatch("[А-Яа-яЁёA-Za-z-]+", before)
                or not re.fullmatch("[А-Яа-яЁёA-Za-z-]+", after)
            ):
                continue
            # GLM-OCR's Russian reading is weaker than the primary recognizer.
            # Preserve dictionary words, abbreviations, and unknown domain terms.
            # A correction must turn an unknown token into a known Russian word.
            if (
                known_word(before.casefold())
                or not known_word(after.casefold())
                or before.isupper()
                or after.isupper()
                or before.count("-") != after.count("-")
                or before.endswith("-") != after.endswith("-")
            ):
                continue
            if Levenshtein.distance(before.casefold(), after.casefold()) > max(2, len(before) // 5):
                continue
            changes.append((before, after))
    return changes, None


def refine_document(document, config, out=None, reader=None):
    from docpipe.review import _apply

    started = time.monotonic()
    report = {
        "stage": "glm_reread",
        "model": config.review.api_model,
        "mode": "image_only_text_recognition",
        "calls": 0,
        "corrections": [],
        "issues": [],
        "units": [],
        "review_required": False,
    }
    if config.offline and not local_ollama(config.review):
        report.update(
            status="skipped", review_required=True, issues=[{"kind": "offline_remote_ollama"}]
        )
        return document, report
    reader = reader or GLMReader(config.review)
    try:
        russian_dictionary()
    except ImportError:
        report.update(
            status="skipped",
            review_required=True,
            issues=[{"kind": "missing_dependency", "detail": "Install docpipe[glm-review]"}],
        )
        return document, report
    selected = list(candidates(document, config.review.reread_min_chars))
    report["units_total"] = len(selected)
    changes = {}
    image = None
    current_page = None
    cache_dir = Path(out) / "glm-crops" if out is not None else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        for page, unit_id, text, bbox in selected:
            remaining = config.review.stage_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0 or report["calls"] >= config.review.max_calls:
                report["issues"].append({"kind": "budget_exceeded"})
                break
            try:
                if current_page != page.index:
                    if image is not None:
                        image.close()
                    image = _page_image(
                        document.source.file, page, config.review.reread_dpi, config.limits
                    )
                    current_page = page.index
                sx, sy = image.width / page.width_pt, image.height / page.height_pt
                box = (
                    max(0, round(bbox.x0 * sx) - 8),
                    max(0, round(bbox.y0 * sy) - 8),
                    min(image.width, round(bbox.x1 * sx) + 8),
                    min(image.height, round(bbox.y1 * sy) + 8),
                )
                with image.crop(box) as crop:
                    buffer = io.BytesIO()
                    crop.save(buffer, "PNG")
                    key = hashlib.sha256(
                        config.review.api_model.encode()
                        + b"\0Text Recognition:\0"
                        + buffer.getvalue()
                    ).hexdigest()
                    cached = cache_dir / (key + ".json") if cache_dir else None
                    if cached and cached.is_file():
                        recognized = json.loads(cached.read_text("utf-8"))["text"]
                    else:
                        report["calls"] += 1
                        recognized = reader.recognize(
                            crop, min(remaining, config.review.timeout_seconds)
                        )
                        if cached:
                            cached.write_text(
                                json.dumps({"text": recognized}, ensure_ascii=False), "utf-8"
                            )
                edits, rejected = proposals(text, recognized)
                item = {
                    "unit_id": unit_id,
                    "page": page.index + 1,
                    "image_sha256": key,
                    "before": text,
                    "recognized": recognized,
                    "rejected": rejected,
                    "proposed_edits": [list(edit) for edit in edits],
                }
                report["units"].append(item)
                for before, after in edits:
                    changes.setdefault(unit_id, {})[before] = after
                    report["corrections"].append(
                        {
                            "unit_id": unit_id,
                            "page": page.index + 1,
                            "before": before,
                            "after": after,
                            "source": "glm_ocr_reread",
                            "image_sha256": key,
                        }
                    )
            except Exception as exc:
                report["issues"].append(
                    {"unit_id": unit_id, "page": page.index + 1, "kind": type(exc).__name__}
                )
                break
            if out is not None:
                (Path(out) / "review-glm-reread.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
                )
    finally:
        if image is not None:
            image.close()
    report["units_completed"] = len(report["units"])
    report["rejected_units"] = sum(bool(unit["rejected"]) for unit in report["units"])
    report["review_required"] = bool(
        report["corrections"] or report["issues"] or report["rejected_units"]
    )
    report["status"] = (
        "partial"
        if report["issues"] or len(report["units"]) < len(selected)
        else "completed_with_findings"
        if report["review_required"]
        else "completed"
    )
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    document = _apply(document, changes, mark_review=True)
    if out is not None:
        (Path(out) / "review-glm-reread.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
    return document, report
