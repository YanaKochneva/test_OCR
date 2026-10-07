from __future__ import annotations

from docpipe.ir import Block, BlockType


def attach_captions(blocks: list[Block], max_gap: float = 48.0) -> list[Block]:
    figs=[b for b in blocks if b.type==BlockType.FIGURE]
    updates={}
    for cap in [b for b in blocks if b.type==BlockType.CAPTION]:
        candidates=[]
        for fig in figs:
            same_column=abs(cap.bbox.x0-fig.bbox.x0) <= max(cap.bbox.x1-cap.bbox.x0, fig.bbox.x1-fig.bbox.x0)
            if not same_column: continue
            gap=fig.bbox.y0-cap.bbox.y1 if cap.bbox.y1<=fig.bbox.y0 else cap.bbox.y0-fig.bbox.y1
            if 0 <= gap <= max_gap: candidates.append((gap,fig))
        if candidates:
            fig=min(candidates,key=lambda x:(x[0],x[1].order))[1]
            updates[fig.id]=fig.model_copy(update={"figure":fig.figure.model_copy(update={"caption_id":cap.id})})
    return [updates.get(b.id,b) for b in blocks]
