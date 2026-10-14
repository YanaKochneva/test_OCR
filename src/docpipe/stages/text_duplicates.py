"""Remove overlapping assembly duplicates, never repeated text elsewhere."""
import re

from docpipe.ir import BlockType


def remove_text_duplicates(blocks):
    normalize = lambda text: re.sub(r'\s+', '', text.casefold())
    text_blocks = [b for b in blocks if b.text and not b.table
                   and b.type != BlockType.FIGURE]
    removed = set()
    for block in sorted(text_blocks, key=lambda b: len(normalize(b.text)), reverse=True):
        box = block.bbox
        parts = []
        for other in text_blocks:
            if other is block or id(other) in removed:
                continue
            b = other.bbox
            if (box.x0 - 3 <= b.x0 and b.x1 <= box.x1 + 3
                    and box.y0 - 3 <= b.y0 and b.y1 <= box.y1 + 3):
                parts.append(other)
        parts.sort(key=lambda b: (b.bbox.y0, b.bbox.x0, b.order))
        # At least two components: identical single blocks might be genuine
        # overprinted text and need a separate decision.
        if len(parts) >= 2 and normalize(block.text) == normalize(' '.join(b.text for b in parts)):
            removed.add(id(block))
    return [b.model_copy(update={'order':i}) for i,b in enumerate(
        b for b in blocks if id(b) not in removed)]
