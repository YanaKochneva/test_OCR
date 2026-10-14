"""Compatibility entry point for a reconstructed, searchable PDF."""
from pathlib import Path

from docpipe.ir import Document


def render(document: Document, source_pdf: Path, out: Path) -> Path:
    """Reconstruct visible text and objects without copying source pages.

    ``source_pdf`` remains in the signature for existing callers only.
    A reconstructed PDF is searchable through its visible Unicode text.
    """
    from docpipe.renderers.pdf_positional import render as reconstruct

    return reconstruct(document, out, out.parent)
