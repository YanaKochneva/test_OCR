from __future__ import annotations

import cv2
import numpy as np
import re
import unicodedata
from PIL import ImageOps
from collections import defaultdict
from dataclasses import dataclass
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


_VISUAL_LETTERS = str.maketrans(
    {
        "а": "a",
        "в": "b",
        "с": "c",
        "е": "e",
        "н": "h",
        "к": "k",
        "м": "m",
        "о": "o",
        "р": "p",
        "т": "t",
        "х": "x",
        "у": "y",
    }
)


def corroborates_short_value(text: str, confidence: float, alternate) -> bool:
    """Compare visual spelling; do not rewrite or translate either candidate.

    Two models can still agree incorrectly. This bounded recovery is limited
    to short cell values and never bypasses the existing-word preservation
    guard used when reconciling populated cells.
    """
    if confidence < 0.55 or not text.strip() or len(text.split()) > 3:
        return False
    if not alternate.txts or alternate.scores[0] < 0.45:
        return False
    other = alternate.txts[0].strip()

    def key(value):
        value = unicodedata.normalize("NFC", value).casefold()
        return " ".join(value.translate(_VISUAL_LETTERS).split())

    return key(text) == key(other)


def recover_uncertain_line(image, primary, reader):
    text, confidence = primary
    if confidence >= 0.85 or len(text) > 500:
        return None

    def read(raster):
        result = reader(raster, use_det=False, use_cls=False)
        if not result.txts:
            return "", 0.0
        return result.txts[0].strip(), float(result.scores[0])

    candidate, score = read(image)
    if not candidate:
        return None

    # Existing numeric evidence must not be silently replaced.
    def numbers(value):
        return re.findall(r"\d+(?:[.,]\d+)*", value)

    if numbers(text) != numbers(candidate):
        return None

    def normal(value):
        return " ".join(value.casefold().split())

    if (
        confidence >= 0.65
        and score >= 0.65
        and normal(text) == normal(candidate)
        and len(text.split()) > 3
        and sum(c.isalpha() for c in text) >= 12
    ):
        # Corroboration may recover a line without manufacturing confidence.
        return text, confidence
    if score < 0.92:
        return None
    if (
        confidence >= 0.6
        and Levenshtein.normalized_similarity(normal(text), normal(candidate)) >= 0.9
    ):
        return candidate, score
    other, other_score = read(ImageOps.autocontrast(ImageOps.expand(image, border=8, fill="white")))
    if candidate == other and other_score >= 0.92:
        return candidate, min(score, other_score)
    return None


def recognize_path_components(image, reader):
    mask = (np.asarray(image.convert("L")) < 170).astype("uint8")
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    separators = []
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        if height < 16 or width < 5 or width > height * 0.7:
            continue
        yy, xx = np.where(labels == label)
        slope, intercept = np.polyfit(yy, xx, 1)
        residual = np.sqrt(np.mean((xx - (slope * yy + intercept)) ** 2))
        if 0.1 < slope < 0.65 and residual < 1.8:
            separators.append((int(x), int(x + width)))
    if len(separators) < 2:
        return None
    output = []
    scores = []
    left = 0
    for right, end in sorted(separators) + [(image.width, image.width)]:
        region = mask[:, left:right]
        if right - left > 8 and region.sum() > 20:
            result = reader(
                image.crop((left, 0, right, image.height)), use_det=False, use_cls=False
            )
            if not result.txts:
                return None
            text = result.txts[0].strip()
            score = float(result.scores[0])
            if not text or score < 0.9:
                return None
            output.append(text)
            scores.append(score)
        if end != image.width:
            output.append("\\")
        left = end
    text = "".join(output)
    if not scores or len(text) < 8:
        return None
    return text, min(scores)


def choose_path_candidate(primary: str, alternative: str) -> str:
    if "\\" not in primary or not re.search("[А-Яа-яЁё]", primary):
        return primary
    if Levenshtein.normalized_similarity(primary.casefold(), alternative.casefold()) < 0.8:
        return primary
    # Long technical names must survive intact. Do not transliterate them.
    anchors = re.findall(r"[A-Za-z_]{8,}", primary)
    if any(anchor not in alternative for anchor in anchors):
        return primary

    def count(text):
        return len(re.findall("[А-Яа-яЁё]", text))

    return alternative if count(alternative) > count(primary) else primary
