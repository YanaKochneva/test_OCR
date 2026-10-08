from docpipe.config import AppConfig
from docpipe.ir import Document

__all__ = ["AppConfig", "Document", "parse"]


def __getattr__(name: str):
    """Load the OCR pipeline only when callers request ``docpipe.parse``."""
    if name == "parse":
        from docpipe.pipeline import parse

        return parse
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
