from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image


@dataclass(frozen=True)
class PixelBox:
    x0: int
    y0: int
    x1: int
    y1: int


def crop_and_save(
    image: Image.Image, boxes: list[PixelBox], out_dir: Path, page_no: int
) -> list[tuple[PixelBox, Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = []
    for idx, b in enumerate(boxes, 1):
        crop = image.crop((b.x0, b.y0, b.x1, b.y1))
        try:
            # Bounded palette counting avoids sorting every pixel of a large figure.
            if crop.getcolors(maxcolors=256) is not None:
                path = out_dir / f"p{page_no:03d}_fig{idx:02d}.png"
                crop.save(path, format="PNG", optimize=True)
            else:
                path = out_dir / f"p{page_no:03d}_fig{idx:02d}.jpg"
                crop.save(path, format="JPEG", quality=90)
        finally:
            crop.close()
        result.append((b, path))
    return result
