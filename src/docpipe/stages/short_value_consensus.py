"""Corroborate uncertain short table values across recognition models."""

from __future__ import annotations

import unicodedata

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
