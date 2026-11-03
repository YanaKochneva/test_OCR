"""Receipt OCR: two readings, confidence selection, preserved total labels.

Keep candidates in JSON; apply selected text directly to the receipt page.
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

LOG = logging.getLogger(__name__)
NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*")
MONEY = re.compile(r"(?<!\d)(\d[\d ]*[.,]\d{2})(?!\d)")


def numbers(text):
    # Separators and leading zeros are evidence, including fiscal identifiers.
    return NUMBER.findall(text)


def receipt_evidence(text):
    text = " ".join(text.casefold().replace("ё", "е").split())
    strong = bool(re.search(r"кассовый\s+чек|фискальный\s+(?:чек|документ)", text))
    fiscal = sum(bool(re.search(pattern, text)) for pattern in (
        r"\bинн\b", r"\bфн\b", r"\bфд\b", r"\bфпд?\b", r"\bккт\b"))
    payment = bool(re.search(r"итог|наличными|безналичными|приход|расход", text))
    bank = sum(bool(re.search(p, text)) for p in
               (r"\brrn\b", r"терминал", r"одобрено", r"банковская карта", r"код авторизации"))
    numbered_check = bool(re.search(r"\bчек\s*[№#]\s*\d", text))
    return (strong or (fiscal >= 2 and payment)
            or (numbered_check and len(numbers(text)) >= 3)
            or (bank >= 2 and len(numbers(text)) >= 3))


def is_expense_form(lines):
    # A reimbursement form may mention "кассовый чек" in its table. That
    # reference does not turn the form itself into a receipt.
    heading = " ".join(l["text"] for l in lines[:12]).casefold()
    form_heading = bool(re.search(r"отч[её]т\s+о\s+расходах|карта\s+(?:уч[её]та\s+)?маршрута", heading))
    text = " ".join(l["text"] for l in lines).casefold()
    expense_table = all(term in text for term in ("табельный номер", "расходы на", "отчету"))
    return form_heading or expense_table


def _lines(result):
    if result.boxes is None or not result.txts:
        return []
    return [{"text": str(t).strip(), "confidence": float(s),
             "bbox": [float(b[:, 0].min()), float(b[:, 1].min()),
                      float(b[:, 0].max()), float(b[:, 1].max())]}
            for b, t, s in zip(result.boxes, result.txts, result.scores) if str(t).strip()]


def _regions(image):
    """Join nearby ink, not PDF image objects (which may be tiny fragments)."""
    gray = np.asarray(image.convert("L"))
    mask = (gray < 200).astype("uint8") * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT,
        (max(5, image.width // 35), max(5, image.height // 65)))
    joined = cv2.dilate(mask, kernel)
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # First split side-by-side receipts at long vertical whitespace gutters.
    # Ink grouping alone tends to fragment a receipt into individual paragraphs.
    column_ink = (gray < 180).sum(axis=0)
    blank = column_ink <= max(2, round(image.height * .002))
    transitions = np.diff(np.r_[False, blank, False].astype(int))
    gaps = [(int(a), int(b)) for a, b in zip(np.flatnonzero(transitions == 1),
                                            np.flatnonzero(transitions == -1))
            if b-a >= image.width*.025 and a > image.width*.18 and b < image.width*.82]
    cuts = [0]
    for a, b in gaps:
        cut = (a+b)//2
        if cut-cuts[-1] >= image.width*.18:
            cuts.append(cut)
    cuts.append(image.width)
    strips = [(a, 0, b, image.height) for a, b in zip(cuts, cuts[1:])
              if b-a >= image.width*.18] if len(cuts) > 2 else []
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w >= image.width * .12 and h >= image.height * .12:
            boxes.append((max(0, x-12), max(0, y-12),
                          min(image.width, x+w+12), min(image.height, y+h+12)))
    return strips + sorted(boxes, key=lambda b: (b[1], b[0]))


def _read(reader, image):
    result = reader(image, use_det=False, use_cls=False)
    if not result.txts:
        return {"text": "", "confidence": 0.0}
    return {"text": str(result.txts[0]).strip(), "confidence": float(result.scores[0])}


def isolated_reader(reader):
    """RapidOCR call flags persist; restore shared engine state after each read."""
    backend = getattr(reader, "reader", reader)
    def read(*args, **kwargs):
        flags = {key: getattr(backend, key) for key in ("use_det", "use_rec", "use_cls")
                 if hasattr(backend, key)}
        try:
            return reader(*args, **kwargs)
        finally:
            for key, value in flags.items():
                setattr(backend, key, value)
    return read


def deskew(image):
    """Rotate only when several near-horizontal strokes agree on a small tilt."""
    import math
    gray = np.asarray(image.convert("L"))
    edges = cv2.Canny(gray, 60, 160)
    lines = cv2.HoughLinesP(edges, 1, np.pi/1800, threshold=40,
                           minLineLength=max(30, image.width//8), maxLineGap=10)
    angles = []
    for x0, y0, x1, y1 in ([] if lines is None else np.asarray(lines).reshape(-1, 4)):
        angle = math.degrees(math.atan2(int(y1-y0), int(x1-x0)))
        angle = (angle+90) % 180-90
        if abs(angle) <= 7:
            angles.append(angle)
    angle = float(np.median(angles)) if len(angles) >= 5 else 0.0
    if len(angles) < 5 or np.median(np.abs(np.asarray(angles)-angle)) > 1 or abs(angle) < .3:
        angle = 0.0
    result = image.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor="white")
    result.info["receipt_deskew"] = angle
    return result


TOTAL = re.compile(r"\b(?:итого?|всего(?:\s+к\s+оплате)?|сумма\s+к\s+оплате|к\s+оплате)\b", re.I)


def choose_reading(readings, threshold):
    available = [r for r in readings if r["text"].strip()]
    if not available:
        return {"text": "", "confidence": 0.0, "numbers": [], "readings": readings,
                "review_required": True, "reason": "empty_readings"}
    # Stable max: on an exact confidence tie retain the first reading.
    best = max(available, key=lambda r: r["confidence"])
    text = best["text"]
    label = next((TOTAL.search(r["text"]).group(0) for r in available if TOTAL.search(r["text"])), None)
    if label and not TOTAL.search(text):
        text = label + (" " + text if text else "")
    uncertain = (best["confidence"] < threshold or
                 len({r["text"] for r in readings}) > 1 or len(available) < 2)
    return {"text": text, "confidence": best["confidence"], "numbers": numbers(text),
            "readings": readings, "selected_method": best.get("method"),
            "review_required": uncertain,
            "reason": "confidence_selection" if uncertain else None}


def verify_numeric_line(image, primary, reader, alternate, threshold):
    # The full-receipt reading is pass one; one enlarged line reread is pass two.
    with image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS) as enlarged:
        with ImageOps.autocontrast(enlarged.convert("RGB")) as contrast:
            second = _read(alternate or reader, contrast)
    readings = [dict(primary, method="primary"),
                dict(second, method="alternate_model" if alternate is not None else "contrast")]
    result = choose_reading(readings, threshold)
    if alternate is None:
        result.update(review_required=True, reason="confidence_selection_same_model")
    return result


def check_arithmetic(lines):
    """Check explicit qty*price=amount and explicit total; never repair digits."""
    issues, amounts, totals = [], [], []
    for index, line in enumerate(lines):
        text = line["text"].casefold()
        match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:шт\.?|л|кг)?\s*[xх×*]\s*(\d+(?:[.,]\d+)?)\s*(?:шт\.?|л|кг)?\s*=\s*(\d+[.,]\d{2})", text)
        if match:
            qty, price, amount = [Decimal(v.replace(",", ".")) for v in match.groups()]
            amounts.append(amount)
            if abs(qty * price - amount) > Decimal("0.01"):
                issues.append({"line": index, "kind": "quantity_price_mismatch"})
        if re.search(r"\bитог(?:о)?\b", text):
            values = MONEY.findall(text)
            if values:
                totals.append(Decimal(values[-1].replace(" ", "").replace(",", ".")))
    # A subset of recognized items cannot establish a total mismatch.
    # Only check total when every non-total money line is an explicit item.
    money_lines = [l for l in lines if MONEY.search(l["text"])
                   and not re.search(r"итог|налич|оплат|сдач|ндс|скид", l["text"], re.I)]
    complete = bool(amounts) and len(money_lines) == len(amounts)
    if complete and len(totals) == 1 and abs(sum(amounts) - totals[0]) > Decimal("0.01"):
        issues.append({"kind": "items_total_mismatch"})
    return {"issues": issues, "total_check": "checked" if complete and len(totals) == 1 else "insufficient_evidence"}


def fiscal_fields(lines):
    fields = []
    for index, line in enumerate(lines):
        for name, pattern in (("inn", r"\bинн\b"), ("fn", r"\bфн\b"),
                              ("fd", r"\bфд\b"), ("fp", r"\bфпд?\b"),
                              ("total", r"\bитог(?:о)?\b"),
                              ("date", r"\b\d{2}[./-]\d{2}[./-]\d{2,4}\b")):
            if re.search(pattern, line["text"], re.I):
                fields.append({"kind": name, "line": index, "raw": line["text"],
                               "numbers": numbers(line["text"]),
                               "review_required": line.get("review_required", True)})
    return fields


def qr_payloads(image):
    """Decode locally; QR data is additional evidence, never a text replacement."""
    detector = cv2.QRCodeDetector()
    ok, values, _, _ = detector.detectAndDecodeMulti(np.asarray(image.convert("RGB")))
    return [value for value in values if value] if ok else []


def _alternate_reader(engine):
    if hasattr(engine, "_receipt_alternate"):
        return engine._receipt_alternate
    # Use an explicitly local, different recognizer. Never download at parse time.
    import os
    import rapidocr
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    from docpipe.engines.rapidocr import RapidOCR
    root = Path(os.getenv("DOCPIPE_RAPIDOCR_MODEL_DIR", str(Path(rapidocr.__file__).parent / "models")))
    path = root / "eslav_PP-OCRv5_rec_mobile.onnx"
    engine._receipt_alternate = None
    if path.is_file():
        engine._receipt_alternate = RapidOCR(params={"Rec.model_path": str(path),
            "Rec.lang_type": LangRec("eslav"), "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Rec.model_type": ModelType.MOBILE, "Global.text_score": 0.0})
    return engine._receipt_alternate


def process_receipts(raster, page_index, engine, config, out_dir):
    if not config.receipts.enabled:
        return [], []
    reader = (engine._get_refinement_reader() if hasattr(engine, "_get_refinement_reader")
              else getattr(engine, "reader", None))
    if reader is None:
        return [], ["Receipt OCR unavailable: engine has no image reader"]
    reader = isolated_reader(reader)
    reports, warnings = [], []
    image = raster.image
    scale = min(1.0, config.receipts.scout_max_side / max(image.size))
    with image.resize((max(1, round(image.width*scale)), max(1, round(image.height*scale)))) as scout:
        candidates = _regions(scout)
        # Bank slips often lack fiscal markers, and a faint gutter may contain
        # scanner noise. Split only if BOTH halves independently look like receipts.
        halves = [(0, 0, scout.width//2, scout.height),
                  (scout.width//2, 0, scout.width, scout.height)]
        half_evidence = []
        for half in halves:
            with scout.crop(half) as preview:
                half_evidence.append(receipt_evidence("\n".join(
                    l["text"] for l in _lines(reader(preview, use_det=True, use_rec=True, use_cls=False)))))
        if all(half_evidence):
            candidates = halves
        if len(candidates) > 16:
            warnings.append("Receipt region search limited to 16 components plus whole page")
            candidates = candidates[:16]
        # A page-wide fallback also handles receipts split by large white gaps.
        candidates.append((0, 0, scout.width, scout.height))
        selected = []
        for box in candidates:
            if len(reports) >= config.receipts.max_receipts_per_page:
                warnings.append("Receipt candidate limit reached; review remaining page content")
                break
            def overlaps(b):
                intersection = max(0, min(box[2], b[2])-max(box[0], b[0])) * max(0, min(box[3], b[3])-max(box[1], b[1]))
                smaller = min((box[2]-box[0])*(box[3]-box[1]), (b[2]-b[0])*(b[3]-b[1]))
                return smaller > 0 and intersection/smaller > .7
            if any(overlaps(b) for b in selected):
                continue
            with scout.crop(box) as preview:
                orientation = None
                for angle in (0, 90, 180, 270):
                    with preview.rotate(angle, expand=True) as trial:
                        lines = _lines(reader(trial, use_det=True, use_rec=True, use_cls=False))
                    if receipt_evidence("\n".join(l["text"] for l in lines)):
                        orientation = angle
                        break
            if orientation is None:
                continue
            selected.append(box)
            pixels = (max(0, int(box[0]/scale)), max(0, int(box[1]/scale)),
                      min(image.width, int(box[2]/scale)), min(image.height, int(box[3]/scale)))
            with image.crop(pixels) as raw, raw.rotate(orientation, expand=True) as oriented, deskew(oriented) as crop:
                lines = sorted(_lines(reader(crop, use_det=True, use_rec=True, use_cls=False)), key=lambda l: (round(l["bbox"][1]/12), l["bbox"][0]))
                if is_expense_form(lines):
                    selected.pop()
                    continue
                report = {"id": f"p{page_index+1:03d}-receipt-{len(reports)+1:02d}",
                          "page": page_index+1, "bbox_px": list(pixels), "rotation": orientation,
                          "deskew_degrees": crop.info.get("receipt_deskew", 0),
                          "lines": lines, "review_required": False, "image": None}
                if out_dir is not None:
                    folder = Path(out_dir) / "receipts"
                    folder.mkdir(parents=True, exist_ok=True)
                    file = folder / (report["id"] + ".png")
                    crop.save(file)
                    report["image"] = file.relative_to(out_dir).as_posix()
                try:
                    alternate = _alternate_reader(engine)
                except Exception as exc:
                    LOG.exception("Independent receipt recognizer unavailable")
                    alternate = None
                    report["alternate_warning"] = f"{type(exc).__name__}: {exc!r}"
                count = 0
                for line in lines:
                    if not numbers(line["text"]) and not TOTAL.search(line["text"]):
                        continue
                    if count >= config.receipts.max_numeric_lines:
                        line.update(review_required=True, reason="numeric_line_limit")
                        report["review_required"] = True
                        continue
                    count += 1
                    x0, y0, x1, y1 = line["bbox"]
                    rect = (max(0, int(x0)-6), max(0, int(y0)-6),
                            min(crop.width, int(x1)+6), min(crop.height, int(y1)+6))
                    if rect[2] <= rect[0] or rect[3] <= rect[1]:
                        line.update(review_required=True, reason="invalid_line_crop")
                    else:
                        try:
                            with crop.crop(rect) as fragment:
                                line.update(verify_numeric_line(fragment,
                                    {"text": line["text"], "confidence": line["confidence"]},
                                    reader, alternate, config.receipts.min_numeric_confidence))
                        except Exception as exc:
                            LOG.exception("Receipt numeric reread failed")
                            line.update(review_required=True, reason=f"reread_failed: {type(exc).__name__}: {exc!r}")
                    report["review_required"] |= line["review_required"]
                for line in lines:
                    line["bbox_page"] = receipt_line_box(line["bbox"], pixels, raw.size,
                        oriented.size, crop.size, orientation, report["deskew_degrees"],
                        image.size, (raster.width_pt, raster.height_pt))
                report["arithmetic"] = check_arithmetic(lines)
                report["fields"] = fiscal_fields(lines)
                try:
                    report["qr_payloads"] = qr_payloads(crop)
                except cv2.error as exc:
                    report["qr_payloads"] = []
                    report["qr_warning"] = repr(exc)
                report["review_required"] |= bool(report["arithmetic"]["issues"])
                reports.append(report)
    return reports, warnings


def write_report(document, out):
    receipts = [r for p in document.pages for r in p.receipts]
    (Path(out) / "receipts.json").write_text(json.dumps({"receipts": receipts}, ensure_ascii=False, indent=2), encoding="utf-8")


def receipt_line_box(box, pixels, raw_size, oriented_size, crop_size, orientation,
                     skew, image_size, page_size):
    import math
    def inverse(x, y, before, after, angle):
        a = math.radians(angle)
        x, y = x-after[0]/2, y-after[1]/2
        return (math.cos(a)*x-math.sin(a)*y+before[0]/2,
                math.sin(a)*x+math.cos(a)*y+before[1]/2)
    points = []
    for x, y in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])):
        x, y = inverse(x, y, oriented_size, crop_size, skew)
        x, y = inverse(x, y, raw_size, oriented_size, orientation)
        points.append(((x+pixels[0])*page_size[0]/image_size[0],
                       (y+pixels[1])*page_size[1]/image_size[1]))
    return [max(0, min(p[0] for p in points)), max(0, min(p[1] for p in points)),
            min(page_size[0], max(p[0] for p in points)), min(page_size[1], max(p[1] for p in points))]


def apply_receipt_readings(blocks, receipts, raster):
    """Put selected readings on the original pages, rather than in extra sheets."""
    from docpipe.ir import Block, BBox, Line
    output = list(blocks)
    for receipt in receipts:
        pixels = receipt["bbox_px"]
        sx, sy = raster.width_pt/raster.image.width, raster.height_pt/raster.image.height
        region = [pixels[0]*sx, pixels[1]*sy, pixels[2]*sx, pixels[3]*sy]
        def inside(block):
            b = block.bbox
            area = (b.x1-b.x0)*(b.y1-b.y0)
            intersection = max(0, min(b.x1, region[2])-max(b.x0, region[0])) * max(0, min(b.y1, region[3])-max(b.y0, region[1]))
            return area > 0 and intersection/area > .9
        covered = [b for b in output if (not b.figure or b.source_label == "ocr_image_fallback") and inside(b)]
        replacements = []
        for index, line in enumerate(receipt["lines"]):
            box = line.get("bbox_page")
            if not box or box[2] <= box[0] or box[3] <= box[1] or not line["text"].strip():
                continue
            replacements.append(Block(id=f"{receipt['id']}-line-{index}", type="text", order=0,
                text=line["text"], confidence=line["confidence"], bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
                lines=[Line(text=line["text"], confidence=line["confidence"],
                            bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]),
                            font_size_pt=max(1, min(10, (box[3]-box[1])*.75)))],
                source_label="receipt_text"))
        if not replacements:
            continue
        # Keep original total labels if the receipt detector missed that line.
        retained_totals = []
        for block in covered:
            if TOTAL.search(block.text or "") and not any(
                TOTAL.search(b.text or "") and abs(b.bbox.y0-block.bbox.y0) < 12 for b in replacements
            ):
                retained_totals.append(block.model_copy(update={"source_label": "receipt_text"}))
        covered_ids = {b.id for b in covered}
        output = [b for b in output if b.id not in covered_ids] + replacements + retained_totals
    # Preserve spatial order while satisfying the IR's unique order constraint.
    return [b.model_copy(update={"order": i}) for i, b in enumerate(sorted(output, key=lambda b: (b.bbox.y0, b.bbox.x0)))]
