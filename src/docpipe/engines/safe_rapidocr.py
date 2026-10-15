"""Keep recognition-only crops valid during RapidOCR's 32-pixel rounding."""
from __future__ import annotations

import logging
import math
import os
import json
import traceback
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageOps


def split_recognition_crop(image, max_side_len):
    """Split at empty columns only; never cut a glyph or overlap words."""
    import numpy as np
    limit = int(max_side_len)
    if image.width <= limit or image.height >= image.width:
        return [image]
    ink = (np.asarray(image.convert('L')) < 170).any(axis=0)
    cuts = [0]
    while image.width - cuts[-1] > limit:
        start = cuts[-1]
        empty = ~ink[start + limit // 2:start + limit]
        transitions = np.diff(np.r_[False, empty, False].astype(int))
        begins, ends = np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1)
        gaps = [(a+b)//2 for a,b in zip(begins,ends)
                if b-a >= max(3, round(image.height * .15))]
        if not gaps:
            return [image]  # An unbroken token: padding remains the fallback.
        cuts.append(start + limit // 2 + int(gaps[-1]))
    cuts.append(image.width)
    return [image.crop((a, 0, b, image.height)) for a, b in zip(cuts, cuts[1:])]


def prepare_recognition_crop(image: Image.Image, max_side_len: float) -> Image.Image:
    """Pad only risky crops; leave text pixels and normal crops unchanged."""
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError(f"Empty OCR crop: {image.size}")
    if not math.isfinite(max_side_len) or max_side_len < 32:
        raise ValueError(f"Invalid RapidOCR max_side_len: {max_side_len}")
    longest = max(width, height)
    if longest <= max_side_len:
        return image
    ratio = max_side_len / longest
    # RapidOCR rounds the reduced sides to multiples of 32. Ensure at least
    # 32 reduced pixels, rather than relying on the 16px rounding threshold.
    minimum = math.ceil(32 / ratio)
    if min(width, height) >= minimum:
        return image
    add_x, add_y = max(0, minimum - width), max(0, minimum - height)
    return ImageOps.expand(image.convert("RGB"),
        border=(add_x // 2, add_y // 2, add_x - add_x // 2, add_y - add_y // 2),
        fill="white")


class RecognitionSafeReader:
    def __init__(self, reader):
        self.reader = reader
        self.context = {}
        self.failures = []
        self._diagnostic_count = 0

    def _record(self, original, prepared, exc=None):
        event = dict(self.context, original_size=original.size, prepared_size=prepared.size,
                     exception_type=type(exc).__name__ if exc else None,
                     exception=repr(exc) if exc else None,
                     traceback=traceback.format_exc() if exc else None)
        if exc:
            self.failures.append(event)
        directory = os.getenv('DOCPIPE_OCR_DIAGNOSTICS')
        if directory and self._diagnostic_count < 100:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            if sum(path.stat().st_size for path in directory.iterdir() if path.is_file()) >= 64 * 1024 * 1024:
                return
            # Bound total retained image pixels, including very large crops.
            if original.width * original.height <= 4_000_000:
                stem = f'{id(self):x}_{self._diagnostic_count:03d}'
                original.save(directory / f'{stem}.png')
                event['image'] = f'{stem}.png'
            with (directory / 'crops.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            self._diagnostic_count += 1

    def __getattr__(self, name):
        return getattr(self.reader, name)

    def __call__(self, image, *args, **kwargs):
        from rapidocr.utils.process_img import ResizeImgError
        # Detection returns geometry: never change its image or coordinate frame.
        # Recognition-only callers consume text/confidence, not boxes.
        recognition_only = not args and kwargs.get("use_det") is False
        original = getattr(image, "size", None)
        source = image
        if recognition_only and isinstance(image, Image.Image):
            pieces = split_recognition_crop(image, float(self.reader.max_side_len))
            if len(pieces) > 1:
                self._record(image, image)
                results = []
                try:
                    for piece in pieces:
                        results.append(self(piece, **kwargs))
                finally:
                    for piece in pieces:
                        piece.close()
                if any(not result.txts for result in results):
                    return SimpleNamespace(txts=[], scores=[], boxes=None)
                return SimpleNamespace(txts=[' '.join(result.txts[0].strip() for result in results)],
                                       scores=[min(float(result.scores[0]) for result in results)], boxes=None)
            original = image.size
            image = prepare_recognition_crop(image, float(self.reader.max_side_len))
            if image.size != original:
                self._record(source, image)
                logging.getLogger(__name__).warning(
                    "Padded thin OCR crop before resize: original=%s padded=%s max_side=%s",
                    original, image.size, self.reader.max_side_len)
        try:
            return self.reader(image, *args, **kwargs)
        except ResizeImgError as exc:
            if not recognition_only:
                raise
            self._record(source, image, exc)
            logging.getLogger(__name__).exception(
                "Recognition-only OCR resize failed: original=%s prepared=%s; "
                "this refinement candidate is unavailable; base OCR is retained",
                original, getattr(image, "size", None))
            return SimpleNamespace(txts=[], scores=[], boxes=None)
        finally:
            if image is not source and isinstance(image, Image.Image):
                image.close()


def RapidOCR(*args, **kwargs):
    """Factory compatible with the project's existing RapidOCR constructors."""
    from rapidocr import RapidOCR as Backend
    params=kwargs.setdefault('params',{})
    params.setdefault('EngineConfig.onnxruntime.intra_op_num_threads',int(os.getenv('DOCPIPE_OCR_THREADS','4')))
    params.setdefault('EngineConfig.onnxruntime.inter_op_num_threads',1)
    return RecognitionSafeReader(Backend(*args, **kwargs))
