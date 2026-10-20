from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def font_path(family: str, bold: bool = False, italic: bool = False) -> Path | None:
    return next(
        (
            path
            for name, b, i, path in installed_fonts()
            if name.casefold() == family.casefold() and b == bold and i == italic
        ),
        None,
    )


@lru_cache(maxsize=1)
def installed_fonts() -> tuple:
    """Discover families from font metadata, including optional custom folders."""
    from PIL import ImageFont

    roots = [
        Path("C:/Windows/Fonts"),
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
        Path(__file__).resolve().parent.parent / "assets/fonts",
    ]
    roots.extend(
        Path(value) for value in os.getenv("DOCPIPE_FONT_DIRS", "").split(os.pathsep) if value
    )
    found = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.ttf")):
            try:
                family, style = ImageFont.truetype(str(path), 20).getname()
                style = style.casefold()
                found.append(
                    (family, "bold" in style, "italic" in style or "oblique" in style, path)
                )
            except OSError:
                continue
    return tuple(found)


@lru_cache(maxsize=512)
def _cmap(path: str) -> frozenset:
    from reportlab.pdfbase.ttfonts import TTFont

    try:
        return frozenset(
            code for code, glyph in TTFont("FontInventory", path).face.charToGlyph.items() if glyph
        )
    except Exception:
        return frozenset()


def supports_text(path: str, text: str) -> bool:
    cmap = _cmap(path)
    return all(ord(character) in cmap for character in text if not character.isspace())


# Bundled DejaVu Sans (Bitstream Vera license, см. LICENSE-DejaVu.txt):
# детерминированный путь внутри пакета, не зависит от файловой системы хоста.
_BUNDLED = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "DejaVuSans.ttf"
_BUNDLED_BOLD = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "DejaVuSans-Bold.ttf"

# Системные кандидаты — только fallback, если bundled-шрифт отсутствует.
_SYSTEM_CANDIDATES = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
    Path("C:/Windows/Fonts/DejaVuSans.ttf"),
)


def find_font() -> Path | None:
    """Найти TTF-шрифт с кириллицей: сначала bundled, затем системные."""
    if _BUNDLED.exists():
        return _BUNDLED
    for path in _SYSTEM_CANDIDATES:
        if path.exists():
            return path
    return None


def find_bold_font() -> Path | None:
    if _BUNDLED_BOLD.exists():
        return _BUNDLED_BOLD
    return None


def require_font() -> Path:
    path = find_font()
    if path is None:
        raise RuntimeError(
            f"Шрифт с кириллицей не найден: ожидался bundled {_BUNDLED} или системный DejaVu Sans"
        )
    return path
