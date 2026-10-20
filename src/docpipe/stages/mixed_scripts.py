"""Conservative selection between independently recognized path candidates."""

import re

from rapidfuzz.distance import Levenshtein


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
