"""Recover missing cell lines without deleting existing text or borrowing neighbours."""
import re
from rapidfuzz.distance import Levenshtein
from docpipe.stages.text_refinement import preserves_existing_words


def equivalent_cell_words(original,candidate):
    words=lambda value:re.findall(r'[^\W_]+',value.casefold())
    return words(original)==words(candidate)


def is_word_extension(original,candidate):
    old=re.findall(r'\w+',original.casefold())
    new=re.findall(r'\w+',candidate.casefold())
    if not old or len(new)<=len(old):return False
    iterator=iter(new)
    return all(any(word==item for item in iterator) for word in old)


def accepts_cell_candidate(original, candidate):
    if not candidate.strip():
        return False
    if not original.strip():
        return True
    if not preserves_cell_values(original, candidate):
        return False
    # Border remnants often become high-confidence trailing underscores.
    # They add no lexical evidence to an already populated cell. Do not reject
    # ordinary punctuation corrections (e.g. restoring a sentence's period).
    if (equivalent_cell_words(original,candidate) and re.search(r'_+\s*$',candidate)
            and not re.search(r'_+\s*$',original)):
        return False
    normal = lambda value: ' '.join(value.casefold().split())
    # Numeric values present in the original must remain, in their original order.
    numbers = lambda value: re.findall(r'\d+(?:[.,]\d+)*', value)
    iterator = iter(numbers(candidate))
    if not all(any(number == item for item in iterator) for number in numbers(original)):
        return False
    operators=lambda value:re.findall(r'[<>≤≥±=]+',value)
    iterator=iter(operators(candidate))
    if not all(any(operator==item for item in iterator) for operator in operators(original)):
        return False
    if is_text_extension(original,candidate):
        return True
    similarity = Levenshtein.normalized_similarity(normal(original),normal(candidate))
    # Permit a larger addition when segmentation recovers a long missing span.
    # Retain the existing token-deletion guard used by table reconciliation.
    return (similarity >= .7 and len(candidate) >= len(original)*.9
            and preserves_existing_words(original,candidate))


def preserves_cell_values(original, candidate):
    """Even a corroborated reread may not delete numbers or formula signs."""
    tokens = lambda text: re.findall(r'\d+(?:[.,]\d+)*|[<>≤≥±=+*/%()\[\]_-]', text)
    iterator = iter(tokens(candidate))
    return all(any(value == item for item in iterator) for value in tokens(original))


def is_text_extension(original: str, candidate: str) -> bool:
    """The original tokens must be an ordered subsequence, including punctuation.

    This accepts long additions that a normalized character similarity gate
    rejects. It does not allow correcting or deleting the original tokens.
    """
    tokens = lambda value: re.findall(r'\w+|[^\w\s]', value.casefold())
    old, new = tokens(original), tokens(candidate)
    if not old or len(new) <= len(old):
        return False
    iterator = iter(new)
    return all(any(word == item for item in iterator) for word in old)


def detected_cell_text(image, reader, minimum=.9):
    """An independent segmentation pass, using the same visual recognizer."""
    result = reader(image, use_det=True, use_cls=False)
    boxes = getattr(result, 'boxes', None)
    if boxes is None or not result.txts or len(boxes) != len(result.txts):
        return ''
    lines = []
    for box, text, score in zip(boxes, result.txts, result.scores):
        if score < minimum or not text.strip():
            continue
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        if min(xs) < -1 or min(ys) < -1 or max(xs) > image.width+1 or max(ys) > image.height+1:
            continue
        lines.append((min(ys), min(xs), text.strip()))
    return ' '.join(text for _, _, text in sorted(lines))
