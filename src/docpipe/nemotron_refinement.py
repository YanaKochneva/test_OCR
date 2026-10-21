"""Image-only crop rereading with local Nemotron; no reference documents."""

from __future__ import annotations

import hashlib
import io
import json
import re
import time
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from rapidfuzz.distance import Levenshtein

from docpipe.render.source import load_page_image as _page_image


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
            # Preserve dictionary words, abbreviations, and unknown domain terms
            # for Nemotron, even when a replacement looks plausible.
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
    backend = "nemotron"
    report_name = f"review-{backend}-reread.json"
    report = {
        "stage": f"{backend}_reread",
        "model": "nvidia/Nemotron-3.5-Parse",
        "mode": "image_only_text_recognition",
        "calls": 0,
        "corrections": [],
        "issues": [],
        "units": [],
        "review_required": False,
    }
    if reader is None:
        from docpipe.nemotron_reader import NemotronReader

        reader = NemotronReader(config.review)
    try:
        russian_dictionary()
    except ImportError:
        report.update(
            status="skipped",
            review_required=True,
            issues=[{"kind": "missing_dependency", "detail": "Install docpipe[nemotron-review]"}],
        )
        return document, report
    selected = list(candidates(document, config.review.reread_min_chars))
    report["units_total"] = len(selected)
    changes = {}
    image = None
    current_page = None
    cache_dir = Path(out) / f"{backend}-crops" if out is not None else None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        for page, unit_id, text, bbox in selected:
            unit_started = time.monotonic()
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
                        ("nvidia/Nemotron-3.5-Parse" + str(config.review.nemotron_model.resolve())).encode()
                        + b"\0nemotron-3.5-parse-v1\0"
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
                    "elapsed_seconds": round(time.monotonic() - unit_started, 3),
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
                            "source": f"{backend}_ocr_reread",
                            "image_sha256": key,
                        }
                    )
            except Exception as exc:
                report["issues"].append(
                    {"unit_id": unit_id, "page": page.index + 1, "kind": type(exc).__name__,
                     "detail": str(exc)[:300]}
                )
                break
            if out is not None:
                (Path(out) / report_name).write_text(
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
    report["runtime_device"] = getattr(reader, "device", None)
    report["model_path"] = str(config.review.nemotron_model.resolve())
    document = _apply(document, changes, mark_review=True)
    if out is not None:
        (Path(out) / report_name).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
        )
    return document, report
