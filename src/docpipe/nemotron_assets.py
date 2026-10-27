"""Preserve original page pixels for Nemotron figure regions, without OCR."""
from __future__ import annotations

import math
from pathlib import Path
from docpipe.nemotron_block_types import resolve_block_type


IMAGE_CLASSES = {"picture", "figure", "image", "logo", "chart", "graphic"}


def validate_region(region):
    box = [float(v) for v in region["bbox"]]
    if len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise ValueError("Image bbox must contain four finite normalized coordinates")
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError(f"Invalid normalized image bbox: {box}")
    return {**region, "bbox": box}


def covered_fraction(box, region):
    x0, y0, x1, y1 = box
    a0, b0, a1, b1 = region
    area = (x1 - x0) * (y1 - y0)
    return (max(0, min(x1, a1) - max(x0, a0)) *
            max(0, min(y1, b1) - max(y0, b0)) / area) if area > 0 else 0


def collect_regions(page, blocks, explicit_regions=()):
    """Prefer specified/figure boxes; retain small native images in digital PDFs.

    Scan layers are excluded: they contain the text as well as the pictures.
    A logo misclassified as text needs an explicit region; no second model runs.
    """
    width, height = page.get_size()
    blocks = [resolve_block_type(block) for block in blocks]
    regions = [validate_region({**r, "source": "explicit"}) for r in explicit_regions]
    for block in blocks:
        if block["class"].lower() not in IMAGE_CLASSES:
            continue
        if any(covered_fraction(block["bbox"], region["bbox"]) > .8 for region in regions):
            continue
        regions.append(validate_region({"bbox": block["bbox"], "source": "nemotron", "class": block["class"]}))
    for obj in page.get_objects():
        if obj.type != 3:  # FPDF_PAGEOBJ_IMAGE
            continue
        left, bottom, right, top = obj.get_bounds()
        box = [max(0, left / width), max(0, 1 - top / height),
               min(1, right / width), min(1, 1 - bottom / height)]
        if right - left < 12 or top - bottom < 12:
            continue
        if (box[2] - box[0]) * (box[3] - box[1]) > .35:
            continue
        if any(b["class"] == "Table" and covered_fraction(b["bbox"], box) > .65 for b in blocks):
            continue
        # Do not save the same graphic twice when a model/manual box covers it.
        if any(covered_fraction(box, r["bbox"]) > .8 for r in regions):
            continue
        regions.append(validate_region({"bbox": box, "source": "pdf-image"}))
    return regions


def save_regions(page, regions, directory: Path, page_number: int, dpi=300):
    if not regions:
        return []
    if dpi <= 0:
        raise ValueError("Image DPI must be positive")
    directory.mkdir(parents=True, exist_ok=True)
    width, height = page.get_size()
    bitmap = page.render(scale=dpi / 72)
    image = bitmap.to_pil()
    assets = []
    try:
        for number, raw in enumerate(regions, 1):
            region = validate_region(raw)
            x0, y0, x1, y1 = region["bbox"]
            # Crop edges are integer pixels; record the actual crop box so that
            # the PDF placement reproduces its source position without stretch.
            pixels = [math.floor(x0 * image.width + 1e-7), math.floor(y0 * image.height + 1e-7),
                      math.ceil(x1 * image.width - 1e-7), math.ceil(y1 * image.height - 1e-7)]
            actual = [pixels[0] / image.width, pixels[1] / image.height,
                      pixels[2] / image.width, pixels[3] / image.height]
            path = directory / f"p{page_number:03d}-image-{number:02d}.png"
            with image.crop(pixels) as crop:
                crop.save(path, dpi=(dpi, dpi))
            assets.append({**region, "bbox": actual, "requested_bbox": region["bbox"],
                           "page": page_number, "file": path.name,
                           "bbox_points_top_left": [actual[0] * width, actual[1] * height,
                                                    actual[2] * width, actual[3] * height],
                           "pixels": [pixels[2] - pixels[0], pixels[3] - pixels[1]], "dpi": dpi})
    finally:
        image.close()
        bitmap.close()
    return assets
