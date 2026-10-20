"""Report ambiguous typography separately from OCR and structural validity."""

import json
from pathlib import Path

_REASONS = {
    "standard_font_unavailable": "Шрифт из эталона отсутствует: требуется установка или согласованная замена",
    "standard_text_exceeds_source_box": "Текст со шрифтом эталона выходит за исходную рамку: требуется проверка",
    "Several fonts have similar glyph shapes": "Несколько шрифтов имеют близкое начертание букв",
    "Glyph fit is weak; text, font or mixed emphasis needs review": "Проверьте текст, шрифт и смешанные начертания",
    "No usable glyph image for font matching": "Недостаточно чётких букв для определения шрифта",
    "No installed font supports the visible text": "Подходящий шрифт не найден среди доступных",
    "Line width includes graphics, leaders or recognition gaps; spacing needs review": "Проверьте расстояния в строке: возможны лидеры, линии или пропуски распознавания",
    "Vertical text direction and content need review": "Проверьте направление и содержание вертикального текста",
}


def line_review(line):
    pending = [item for item in [line] + (line.runs or []) if item.review_required]
    return "; ".join(
        dict.fromkeys(
            _REASONS.get(item.review_reason, item.review_reason or "Проверьте оформление")
            for item in pending
        )
    )


def review_items(document):
    output = []
    for page in document.pages:
        for block in page.blocks:
            entries = [(None, None, line) for line in (block.lines or [])]
            if block.table:
                entries.extend(
                    (cell.row, cell.col, line)
                    for cell in block.table.cells
                    for line in (cell.lines or [])
                )
            for row, col, line in entries:
                reason = line_review(line)
                if reason:
                    output.append(
                        {
                            "page": page.index + 1,
                            "block": block.id,
                            "row": row,
                            "col": col,
                            "text": line.text,
                            "bbox": line.bbox.model_dump(),
                            "font_family": line.font_family,
                            "font_size_pt": line.font_size_pt,
                            "bold": line.bold,
                            "italic": line.italic,
                            "glyph_match_error": line.font_match_error,
                            "candidate_margin": line.font_match_margin,
                            "reason": reason,
                        }
                    )
    return output


def write_style_review(document, folder):
    items = review_items(document)
    report = {
        "status": "preliminary" if items else "no_ambiguity_detected",
        "review_required": bool(items),
        "fragments": items,
        "note": "Glyph matching is a visual estimate. Absence of a warning is not proof of exact formatting. "
        "Thresholds trigger review, not a claimed font recognition accuracy.",
    }
    path = Path(folder) / "style-review.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
