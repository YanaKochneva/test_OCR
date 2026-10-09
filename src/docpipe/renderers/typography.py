"""Typography defaults calibrated against the supplied Polyus source DOCX."""

FONT_FAMILY = "Arial"
BODY_FONT_PT = 11.0
HEADING_FONT_PT = 12.0
CAPTION_FONT_PT = 10.0
TABLE_FONT_PT = 11.0
FOOTNOTE_FONT_PT = 11.0
# OCR boxes on the supplied scan are spaced roughly one body-font high.
# A 1.3 multiplier made neighboring positioned lines collide in Word/HTML.
BODY_LINE_SPACING = 1.0


def text_style(block_type: str) -> tuple[float, bool, bool]:
    """Return (font size, bold, italic) for a recognized block role."""
    if block_type == "heading":
        return HEADING_FONT_PT, True, False
    if block_type == "caption":
        return CAPTION_FONT_PT, False, True
    if block_type in {"footnote", "page_number"}:
        return FOOTNOTE_FONT_PT, False, False
    return BODY_FONT_PT, False, False
