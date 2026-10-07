from __future__ import annotations

import re
from docpipe.ir import Block, BlockType


def clean_text(text: str) -> str:
    text=text.replace("\u00ad", "")
    text=re.sub(r"-\s*\n\s*", "", text)
    text=re.sub(r"[ \t]+", " ", text)
    return text.strip()


def postprocess(blocks: list[Block], headers_footers: str = "drop") -> list[Block]:
    out=[]
    for b in blocks:
        if b.text is not None and b.type != BlockType.FIGURE:
            b=b.model_copy(update={"text":clean_text(b.text)})
        if headers_footers == "drop" and b.type in {BlockType.HEADER,BlockType.FOOTER,BlockType.PAGE_NUMBER}:
            continue
        out.append(b)
    return out
