from __future__ import annotations

from importlib import import_module
from typing import Any

_RENDERERS = {
    "render_docx": ("docx", "render"),
    "render_html": ("html", "render"),
    "render_markdown": ("markdown", "render"),
    "render_pdf_flow": ("pdf_flow", "render"),
    "render_pdf_positional": ("pdf_positional", "render"),
    "render_pdf_searchable": ("pdf_searchable", "render"),
}

__all__ = list(_RENDERERS)


def __getattr__(name: str) -> Any:
    """Import a renderer only when its format is requested."""
    try:
        module_name, attribute = _RENDERERS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    renderer = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = renderer
    return renderer
