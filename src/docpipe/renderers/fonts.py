from __future__ import annotations

from pathlib import Path

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
            "Шрифт с кириллицей не найден: ожидался bundled "
            f"{_BUNDLED} или системный DejaVu Sans"
        )
    return path
