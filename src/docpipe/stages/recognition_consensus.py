"""Bounded image-only retries; accept corroborated text, never a reference."""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

import numpy as np
from PIL import ImageOps
from rapidfuzz.distance import Levenshtein


@dataclass(frozen=True)
class RecognitionDecision:
    text: str
    confidence: float
    attempts: int
    agreed: bool


def tight_ink_image(image):
    """Remove surrounding whitespace, retaining all ink and a two-pixel margin."""
    mask = np.asarray(image.convert("L")) < 170
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    left, top = max(0, int(xs.min()) - 2), max(0, int(ys.min()) - 2)
    right, bottom = min(image.width, int(xs.max()) + 3), min(image.height, int(ys.max()) + 3)
    if bottom - top >= image.height - 2 and right - left >= image.width - 2:
        return None
    return image.crop((left, top, right, bottom))


def corroborates_tight_reading(primary, candidate):
    before, after = primary[0], candidate[0]
    if not before or candidate[1] < 0.95:
        return False
    if re.findall(r"\d+(?:[.,]\d+)?", before) != re.findall(r"\d+(?:[.,]\d+)?", after):
        return False

    def clean(value):
        return "".join(value.casefold().split())

    return Levenshtein.normalized_similarity(clean(before), clean(after)) >= 0.85


def recognize_with_consensus(image, reader, initial=None):
    def read(img):
        result = reader(img, use_det=False, use_cls=False)
        if not result.txts:
            return "", 0.0
        return unicodedata.normalize("NFC", result.txts[0].strip()), float(result.scores[0])

    primary = initial if initial is not None else read(image)
    tight = tight_ink_image(image) if len(primary[0]) >= 12 and primary[1] < 0.99 else None
    if primary[0] and primary[1] >= 0.96 and tight is None:
        return RecognitionDecision(*primary, 1, True)
    # Preserve grayscale detail. Hard binarization may erase thin characters.
    contrasted = ImageOps.autocontrast(image.convert("RGB"), cutoff=0)
    variants = [contrasted, ImageOps.expand(contrasted, border=8, fill="white")]
    candidates = [primary, *[read(v) for v in variants]]
    if tight is not None:
        padded = ImageOps.autocontrast(tight.convert("RGB"), cutoff=0)
        try:
            pair = [read(tight), read(padded)]
            if pair[0][0] == pair[1][0] and all(
                corroborates_tight_reading(primary, c) for c in pair
            ):
                return RecognitionDecision(pair[0][0], sum(c[1] for c in pair) / 2, 5, True)
        finally:
            tight.close()
            padded.close()
    groups = defaultdict(list)
    for text, score in candidates:
        if text:
            groups[text].append(score)
    agreed = [
        (text, sum(scores) / len(scores))
        for text, scores in groups.items()
        if len(scores) >= 2 and min(scores) >= 0.85
    ]
    if agreed:
        text, score = max(agreed, key=lambda item: item[1])
        return RecognitionDecision(text, score, 3, True)
    # Confidence scores alone do not establish which conflicting spelling is right.
    return RecognitionDecision(*primary, 3, False)
