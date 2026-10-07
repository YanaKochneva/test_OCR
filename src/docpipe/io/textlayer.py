from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium

from docpipe.config import TextLayerConfig
from docpipe.ir import TextLayerStatus

_REPLACEMENT = "\ufffd"
_CYR = re.compile(r"[\u0400-\u052f]")
_LAT = re.compile(r"[A-Za-z\u00c0-\u024f]")
_WORD = re.compile(r"\S+")


def _script_count(text: str, scripts: list[str]) -> int:
    n = 0
    for ch in text:
        if "cyrillic" in scripts and _CYR.match(ch): n += 1; continue
        if "latin" in scripts and _LAT.match(ch): n += 1
    return n


def _mixed_words(text: str) -> int:
    total = 0
    for word in _WORD.findall(text):
        has_cyr = bool(_CYR.search(word))
        has_lat = bool(_LAT.search(word))
        if has_cyr and has_lat: total += 1
    return total


def classify_text(text: str, config: TextLayerConfig) -> TextLayerStatus:
    if not text or not text.strip():
        return TextLayerStatus.ABSENT
    if len(text.strip()) < config.min_text_length:
        return TextLayerStatus.GARBAGE
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return TextLayerStatus.ABSENT
    expected = _script_count(text, config.expected_scripts) / len(chars)
    controls = sum(1 for c in text if ord(c) < 32 and c not in "\n\r\t") / max(1, len(text))
    replacements = text.count(_REPLACEMENT) / max(1, len(text))
    words = _WORD.findall(text)
    mixed = _mixed_words(text) / max(1, len(words))
    if expected < config.min_expected_script_ratio or controls > config.max_control_ratio or replacements > config.max_replacement_ratio or mixed > config.max_mixed_script_word_ratio:
        return TextLayerStatus.GARBAGE
    return TextLayerStatus.VALID


def analyze_pdf(path: Path, config: TextLayerConfig) -> list[TextLayerStatus]:
    doc = pdfium.PdfDocument(str(path))
    result: list[TextLayerStatus] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_range()
                result.append(classify_text(text, config))
            finally:
                textpage.close()
            page.close()
    finally:
        doc.close()
    return result
