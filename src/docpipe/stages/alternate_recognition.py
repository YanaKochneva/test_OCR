"""Visual corroboration of low-confidence table lines by another language head."""

import re

from PIL import ImageOps
from rapidfuzz.distance import Levenshtein


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
