"""Persistent, model-independent OCR review artifacts. Coordinates are IR points."""

from __future__ import annotations

import hashlib
import html
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def aggregate_confidence(lines):
    """Character-weighted summary; individual weak lines remain review signals."""
    total = sum(max(1, len(text.strip())) for text, score in lines)
    return (
        sum(max(1, len(text.strip())) * score for text, score in lines) / total if total else None
    )


def attach_confidence(blocks, raw_lines):
    """Only associate an OCR score with matching text fully inside the region."""

    def score(region, text):
        matches = [
            (t, s)
            for t, b, s in raw_lines
            if region.x0 - 1 <= b.x0
            and region.y0 - 1 <= b.y0
            and region.x1 + 1 >= b.x1
            and region.y1 + 1 >= b.y1
        ]

        def normal(t):
            return "".join(t.split()).casefold()

        if matches and normal(" ".join(t for t, _ in matches)) == normal(text):
            return aggregate_confidence(matches)
        return None

    result = []
    for block in blocks:
        if block.table:
            cells = [
                c.model_copy(update={"confidence": score(c.bbox, c.text)})
                for c in block.table.cells
            ]
            block = block.model_copy(
                update={"table": block.table.model_copy(update={"cells": cells})}
            )
        elif block.text and block.confidence is None:
            block = block.model_copy(update={"confidence": score(block.bbox, block.text)})
        result.append(block)
    return result


def collect_issues(document, threshold=0.7):
    issues = []
    for page in document.pages:
        if page.status.value != "ok" or page.warnings:
            issues.append(
                {
                    "id": f"p{page.index + 1}:page",
                    "location": {"page": page.index + 1},
                    "reasons": [f"page_{page.status.value}"]
                    if page.status.value != "ok"
                    else ["page_warning"],
                    "warnings": page.warnings,
                    "severity": "error" if page.status.value == "failed" else "warning",
                    "status": "unresolved",
                    "original_text": "",
                    "confidence": None,
                    "crop": None,
                    "alternatives": [],
                    "accepted_text": None,
                }
            )
        for block in page.blocks:
            units = [(block, f"p{page.index + 1}:{block.id}", {})]
            if block.table:
                units = [
                    (
                        c,
                        f"p{page.index + 1}:{block.id}:c{c.row},{c.col}",
                        {"row": c.row, "col": c.col},
                    )
                    for c in block.table.cells
                ]
            for unit, key, extra in units:
                text = unit.text or ""
                reasons = []
                scores = [
                    line.confidence
                    for line in (getattr(unit, "lines", None) or [])
                    if line.confidence is not None
                ]
                confidence = min(scores) if scores else unit.confidence
                if confidence is not None and confidence < threshold:
                    reasons.append("low_confidence")
                if confidence is None and text.strip():
                    reasons.append("confidence_unavailable")
                if any(
                    re.search("[А-Яа-яЁё]", w) and re.search("[A-Za-z]", w)
                    for w in re.findall(r"\w+", text)
                ):
                    reasons.append("mixed_script")
                if "\ufffd" in text or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
                    reasons.append("invalid_characters")
                if block.source_label == "ocr_image_fallback":
                    reasons.append("image_fallback")
                if not reasons:
                    continue
                issues.append(
                    {
                        "id": key,
                        "location": {
                            "page": page.index + 1,
                            "block_id": block.id,
                            "bbox_pt": [unit.bbox.x0, unit.bbox.y0, unit.bbox.x1, unit.bbox.y1],
                            **extra,
                        },
                        "reasons": reasons,
                        "severity": "info" if reasons == ["confidence_unavailable"] else "warning",
                        "status": "unresolved",
                        "original_text": text,
                        "confidence": confidence,
                        "crop": None,
                        "alternatives": [],
                        "accepted_text": None,
                    }
                )
    return issues


def write_ocr_review(document, config, out, *, reset=False):
    folder = Path(out) / "review"
    if reset and folder.exists():
        folder.rename(Path(out) / ("review-archive-" + uuid.uuid4().hex))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "issues.json"
    previous = json.loads(path.read_text("utf-8")) if path.exists() else {}
    if reset or previous.get("source_sha256") != document.source.sha256:
        previous = {}
    items = {i["id"]: i for i in previous.get("issues", [])}
    for issue in collect_issues(document, config.render.unreadable_min_confidence):
        if issue["id"] in items:
            old = items[issue["id"]]
            old["reasons"] = sorted(set(old["reasons"] + issue["reasons"]))
        else:
            items[issue["id"]] = issue
    fallback_path = Path(out) / "image-fallback.json"
    if fallback_path.exists():
        for region in json.loads(fallback_path.read_text("utf-8")).get("regions", []):
            if region.get("status") != "replaced":
                continue
            key = f"p{region['page']}:{region['region']}"
            box = region["bbox"]
            issue = items.setdefault(
                key,
                {
                    "id": key,
                    "location": {
                        "page": region["page"],
                        "block_id": region["region"],
                        "bbox_pt": [box["x0"], box["y0"], box["x1"], box["y1"]],
                    },
                    "reasons": ["image_fallback"],
                    "severity": "warning",
                    "status": "unresolved",
                    "original_text": region.get("original_ocr_text", ""),
                    "confidence": None,
                    "crop": None,
                    "alternatives": [],
                    "accepted_text": None,
                },
            )
            if not issue["original_text"]:
                issue["original_text"] = region.get("original_ocr_text", "")
    backend = "nemotron" if config.review.provider == "nemotron" else "glm"
    glm_path = Path(out) / f"review-{backend}-reread.json"
    if not reset and glm_path.exists():
        glm = json.loads(glm_path.read_text("utf-8"))
        for unit in glm.get("units", []):
            issue = items.get(unit["unit_id"])
            if issue is None and (unit.get("rejected") or unit.get("proposed_edits")):
                loc = {"page": unit["page"], "block_id": unit["unit_id"].split(":")[1]}
                for page in document.pages:
                    for block in page.blocks:
                        if page.index + 1 == unit["page"] and block.id == loc["block_id"]:
                            region = block
                            if ":c" in unit["unit_id"] and block.table:
                                row, col = map(int, unit["unit_id"].rsplit(":c", 1)[1].split(","))
                                region = next(
                                    (c for c in block.table.cells if c.row == row and c.col == col),
                                    block,
                                )
                                loc.update(row=row, col=col)
                            loc["bbox_pt"] = [
                                region.bbox.x0,
                                region.bbox.y0,
                                region.bbox.x1,
                                region.bbox.y1,
                            ]
                issue = {
                    "id": unit["unit_id"],
                    "location": loc,
                    "reasons": [
                        f"{backend}_proposal" if unit.get("proposed_edits") else "ocr_disagreement"
                    ],
                    "severity": "warning",
                    "status": "unresolved",
                    "original_text": unit["before"],
                    "confidence": None,
                    "crop": None,
                    "alternatives": [],
                    "accepted_text": None,
                }
                items[issue["id"]] = issue
            if issue:
                issue["alternatives"] = [
                    {
                        "source": backend,
                        "model": glm.get("model"),
                        "text": unit["recognized"],
                        "rejected": unit.get("rejected"),
                    }
                ]
                if unit.get("rejected"):
                    issue["reasons"] = sorted(set(issue["reasons"] + ["ocr_disagreement"]))
    pages = {p.index + 1: p for p in document.pages}
    for number in sorted(
        {i["location"]["page"] for i in items.values() if "bbox_pt" in i["location"]}
    ):
        image = None
        try:
            image = _review_image(document.source.file, pages[number], config)
            page = pages[number]
            for issue in items.values():
                loc = issue["location"]
                if loc["page"] != number or "bbox_pt" not in loc:
                    continue
                x0, y0, x1, y1 = loc["bbox_pt"]
                box = (
                    max(0, int(x0 * image.width / page.width_pt) - 8),
                    max(0, int(y0 * image.height / page.height_pt) - 8),
                    min(image.width, int(x1 * image.width / page.width_pt) + 8),
                    min(image.height, int(y1 * image.height / page.height_pt) + 8),
                )
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                name = (
                    hashlib.sha256((document.source.sha256 + issue["id"]).encode()).hexdigest()[:24]
                    + ".png"
                )
                (folder / "crops").mkdir(exist_ok=True)
                with image.crop(box) as crop:
                    crop.save(folder / "crops" / name)
                issue["crop"] = "crops/" + name
        except Exception as exc:
            for issue in items.values():
                if issue["location"]["page"] == number:
                    issue["crop_error"] = type(exc).__name__
        finally:
            if image is not None:
                image.close()
    report = {
        "schema_version": 1,
        "run_id": previous.get("run_id", uuid.uuid4().hex),
        "created_at": previous.get("created_at", datetime.now(timezone.utc).isoformat()),
        "source_sha256": document.source.sha256,
        "engine": document.engine.model_dump(mode="json"),
        "config": config.model_dump(mode="json", exclude={"api", "review"}),
        "issues": list(items.values()),
    }
    report["review_required"] = any(
        i["status"] == "unresolved" and i["severity"] != "info" for i in items.values()
    )
    atomic_json(path, report)
    if reset:
        (folder / "decisions.jsonl").write_text("", encoding="utf-8")
    else:
        (folder / "decisions.jsonl").touch(exist_ok=True)
    write_review_html(folder, report)
    return report


def write_review_html(folder, report):
    items = {i["id"]: i for i in report["issues"]}
    cards = []
    for issue in items.values():

        def esc(value):
            return html.escape(str(value))

        picture = (
            f'<img loading="lazy" src="{esc(issue["crop"])}" alt="Scan fragment">'
            if issue["crop"]
            else ""
        )
        cards.append(
            f"<article><h2>{esc(issue['id'])}</h2><p>{esc(issue['status'])} · {esc(', '.join(issue['reasons']))}</p>{picture}<pre>{esc(issue['original_text'])}</pre><pre>{esc(json.dumps(issue['alternatives'], ensure_ascii=False))}</pre></article>"
        )
    (folder / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>OCR review</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto}article{border:1px solid #ccc;padding:16px;margin:16px 0}img{max-width:100%}pre{white-space:pre-wrap}</style><h1>OCR review</h1>'
        + "".join(cards),
        encoding="utf-8",
    )
    return report


def record_decision(out, issue_id, status, actor, source_sha256, note=""):
    """Record a review disposition; changing exported OCR text is a separate operation.

    Call with exclusive access to the result directory (single writer).
    """
    if status not in {"confirmed", "dismissed", "unresolved"}:
        raise ValueError("Unsupported review status")
    if not actor.strip():
        raise ValueError("Actor is required")
    folder = Path(out) / "review"
    path = folder / "issues.json"
    report = json.loads(path.read_text("utf-8"))
    if report["source_sha256"] != source_sha256:
        raise ValueError("Source hash mismatch")
    issue = next((i for i in report["issues"] if i["id"] == issue_id), None)
    if issue is None:
        raise KeyError(issue_id)
    event = {
        "id": uuid.uuid4().hex,
        "run_id": report["run_id"],
        "source_sha256": source_sha256,
        "issue_id": issue_id,
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "before": issue["status"],
        "after": status,
        "note": note,
    }
    # Journal first: a failed snapshot write still leaves an audit record.
    with (folder / "decisions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
    issue["status"] = status
    report["review_required"] = any(
        i["status"] == "unresolved" and i["severity"] != "info" for i in report["issues"]
    )
    atomic_json(path, report)
    write_review_html(folder, report)
    return event


def _review_image(source, page, config):
    from docpipe.render.source import load_page_image

    return load_page_image(source, page, config.default_dpi, config.limits)


def begin_review_run(out):
    """Archive review artifacts before processing a new source in a reused folder."""
    root = Path(out)
    names = ("review", "review-glm-reread.json", "review-nemotron-reread.json", "image-fallback.json")
    existing = [root / name for name in names if (root / name).exists()]
    if existing:
        archive = root / ("review-archive-" + uuid.uuid4().hex)
        archive.mkdir(parents=True)
        for path in existing:
            path.rename(archive / path.name)
