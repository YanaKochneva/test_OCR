from __future__ import annotations

from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path
import re

from PIL import ImageFont

from docpipe.ir import Block, BlockType


def collision_free_text_tops(
    blocks: Iterable[Block],
    font_style_for: Callable[[Block], tuple[float, bool, bool]],
    *,
    gap_pt: float = 1.5,
    font_metric_mode: str = "arial",
) -> dict[int, float]:
    """Return page y positions that keep rendered text glyph areas apart.

    OCR boxes can overlap slightly, and a renderer may wrap a line inside a
    box. Place each text block after any earlier block whose estimated glyph
    area intersects it. The source IR boxes remain unchanged.
    """
    placed: list[tuple[float, float, float, float]] = []
    result: dict[int, float] = {}
    text_blocks = sorted(
        (
            block for block in blocks
            if (block.text and block.type != BlockType.FIGURE) or block.table
        ),
        key=lambda block: (block.bbox.y0, block.bbox.x0, block.order),
    )

    for block in text_blocks:
        box = block.bbox
        size, bold, italic = font_style_for(block)
        size = max(1.0, size)
        width = max(1.0, box.x1 - box.x0)
        fonts = _font_candidates(size, bold, italic, font_metric_mode)
        text = block.text or ""
        if block.type == BlockType.LIST_ITEM:
            text = "• " + text
        rendered_lines = wrap_text(
            text, width, (size, bold, italic), font_metric_mode=font_metric_mode
        ).splitlines()
        max_visual_width = max((_measure(line, fonts) for line in rendered_lines), default=0.0)

        # Match the explicit line breaks emitted by HTML and Word. The old
        # character-count estimate undercounted wide glyphs and missed lines
        # that Word/Chromium wrapped in practice.
        visual_height = estimate_text_height(
            text, width, (size, bold, italic), font_metric_mode
        )
        visual_width = max(1.0, min(width, max_visual_width))
        if block.type == BlockType.TABLE and block.table:
            from docpipe.renderers.table_layout import build_table_layout
            visual_height = sum(build_table_layout(block).row_heights)
            visual_width = width
        top = box.y0

        # A shift can create a new intersection with an earlier object in a
        # neighboring column. Repeat until the final position is clear.
        while True:
            next_top = top
            for previous_left, previous_right, previous_top, previous_bottom in placed:
                horizontal_overlap = min(box.x0 + visual_width, previous_right) - max(box.x0, previous_left)
                vertical_overlap = min(top + visual_height, previous_bottom) - max(top, previous_top)
                if horizontal_overlap > 0.5 and vertical_overlap > 0:
                    next_top = max(next_top, previous_bottom + gap_pt)
            if next_top == top:
                break
            top = next_top

        result[id(block)] = top
        placed.append((box.x0, box.x0 + visual_width, top, top + visual_height))

    return result


@lru_cache(maxsize=256)
def _fonts(size_px: int, bold: bool, italic: bool, font_metric_mode: str) -> tuple[ImageFont.FreeTypeFont, ...]:
    font_dir = Path(__file__).resolve().parents[1] / "assets" / "fonts"
    if bold and italic:
        arial_name, dejavu_name = "arialbi.ttf", "DejaVuSans-Bold.ttf"
    elif bold:
        arial_name, dejavu_name = "arialbd.ttf", "DejaVuSans-Bold.ttf"
    elif italic:
        arial_name, dejavu_name = "ariali.ttf", "DejaVuSans.ttf"
    else:
        arial_name, dejavu_name = "arial.ttf", "DejaVuSans.ttf"
    arial_path = Path("C:/Windows/Fonts") / arial_name
    dejavu_path = font_dir / dejavu_name
    paths = (arial_path, dejavu_path) if font_metric_mode == "arial" else (dejavu_path, arial_path)
    for path in paths:
        if path.is_file():
            try:
                return (ImageFont.truetype(str(path), size_px),)
            except OSError:
                continue
    return (ImageFont.load_default(),)


def _font_candidates(
    size_pt: float,
    bold: bool,
    italic: bool,
    font_metric_mode: str,
) -> tuple[ImageFont.FreeTypeFont, ...]:
    if font_metric_mode not in {"arial", "dejavu"}:
        raise ValueError(f"Unsupported font metric mode: {font_metric_mode}")
    return _fonts(max(1, round(size_pt * 96 / 72)), bold, italic, font_metric_mode)


def _measure(text: str, fonts: tuple[ImageFont.FreeTypeFont, ...]) -> float:
    # PIL sizes are 96-DPI pixels; convert the target font measure back to points.
    return max((float(font.getlength(text)) for font in fonts), default=0.0) * 0.75


def _glyph_height(text: str, fonts: tuple[ImageFont.FreeTypeFont, ...]) -> float:
    if not text:
        return 1.0
    return max(
        (float(font.getbbox(text)[3] - font.getbbox(text)[1]) for font in fonts),
        default=1.0,
    ) * 0.75


def wrap_text(
    text: str,
    width_pt: float,
    font_style: tuple[float, bool, bool],
    *,
    font_metric_mode: str = "arial",
) -> str:
    """Insert deterministic line breaks before the target renderer wraps text."""
    size, bold, italic = font_style
    fonts = _font_candidates(size, bold, italic, font_metric_mode)
    width = max(1.0, width_pt)
    return "\n".join(
        wrapped
        for line in (text.splitlines() or [""])
        for wrapped in _wrap_line(line, width, fonts)
    )


def estimate_text_height(
    text: str,
    width_pt: float,
    font_style: tuple[float, bool, bool],
    font_metric_mode: str = "arial",
) -> float:
    """Estimate the actual line-box height after deterministic wrapping."""
    size, bold, italic = font_style
    fonts = _font_candidates(size, bold, italic, font_metric_mode)
    lines = wrap_text(text, width_pt, font_style, font_metric_mode=font_metric_mode).splitlines()
    glyph_height = max((_glyph_height(line, fonts) for line in lines), default=size)
    return glyph_height + max(0, len(lines) - 1) * size + 1.0


def _wrap_line(
    line: str,
    available_width_pt: float,
    fonts: tuple[ImageFont.FreeTypeFont, ...],
) -> list[str]:
    """Estimate browser/Word word wrapping using measured glyph advances."""
    if not line:
        return [""]
    available = max(1.0, available_width_pt - 2.0)  # Word textbox inset
    words = re.findall(r"\S+|\s+", line)
    lines: list[str] = []
    current = 0.0
    current_text = ""
    for token in words:
        token_width = _measure(token, fonts)
        if token.isspace():
            if current_text:
                current += token_width
                current_text += token
            continue
        if token_width > available:
            if current_text:
                lines.append(current_text.rstrip())
                current = 0.0
                current_text = ""
            fragment = ""
            for char in token:
                if fragment and _measure(fragment + char, fonts) > available:
                    lines.append(fragment)
                    fragment = char
                else:
                    fragment += char
            current_text = fragment
            current = _measure(fragment, fonts)
        elif current and current + token_width > available:
            lines.append(current_text.rstrip())
            current = token_width
            current_text = token
        else:
            current += token_width
            current_text += token
    if current_text or not lines:
        lines.append(current_text.rstrip())
    return lines
