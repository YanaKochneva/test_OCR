from __future__ import annotations

from pathlib import Path

from docpipe.ir import BlockType, Document


def render(document: Document, out: Path) -> Path:
    """Потоковый Markdown: картинки и подписи идут в порядке IR."""
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for page in document.pages:
        if document.pages:
            lines.append(f"<!-- page {page.index + 1} -->")
        by_id = {b.id: b for b in page.blocks}
        for block in sorted(page.blocks, key=lambda item: item.order):
            if block.type == BlockType.FIGURE and block.figure:
                alt = by_id.get(block.figure.caption_id).text if block.figure.caption_id in by_id else ""
                lines.append(f"![{alt or block.id}]({block.figure.file})")
                if alt:
                    lines.append(f"*{alt}*")
                lines.append("")
            elif block.type == BlockType.CAPTION and any(
                b.figure and b.figure.caption_id == block.id for b in page.blocks
            ):
                # Подпись уже выведена рядом с figure.
                continue
            elif block.type == BlockType.HEADING:
                lines.append("#" * min(block.level or 1, 6) + " " + (block.text or ""))
                lines.append("")
            elif block.type == BlockType.LIST_ITEM:
                lines.append("- " + (block.text or ""))
            elif block.type == BlockType.TABLE and block.table and block.table.html:
                lines.append(block.table.html)
                lines.append("")
            elif block.text:
                lines.append(block.text)
                lines.append("")
    out.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return out
