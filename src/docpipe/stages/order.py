from __future__ import annotations

from docpipe.ir import Block


def xy_cut(blocks: list[Block], page_width: float, gap: float = 18.0) -> list[Block]:
    if len(blocks) <= 1: return list(blocks)
    # Детерминированное разбиение по вертикальным полосам: сначала колонки,
    # внутри колонки сверху вниз. Это fallback, а не замена engine order.
    sorted_blocks=sorted(blocks,key=lambda b:(b.bbox.x0,b.bbox.y0,b.id))
    columns: list[list[Block]]=[]
    for block in sorted_blocks:
        placed=False
        for col in columns:
            right=max(x.bbox.x1 for x in col)
            left=min(x.bbox.x0 for x in col)
            if block.bbox.x0 <= right+gap and block.bbox.x1 >= left-gap:
                col.append(block); placed=True; break
        if not placed: columns.append([block])
    columns.sort(key=lambda c:min(x.bbox.x0 for x in c))
    result=[]
    for col in columns:
        result.extend(sorted(col,key=lambda b:(b.bbox.y0,b.bbox.x0,b.id)))
    return result


def assign_order(blocks: list[Block], page_width: float) -> list[Block]:
    ordered=xy_cut(blocks,page_width)
    return [b.model_copy(update={"order":i}) for i,b in enumerate(ordered)]
