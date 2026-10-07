from __future__ import annotations
from html import escape
from pathlib import Path
from docpipe.ir import BlockType, Document

def render(document: Document, out: Path, self_contained: bool=False) -> Path:
    out.parent.mkdir(parents=True,exist_ok=True)
    body=[]
    for page in document.pages:
        body.append(f'<section class="page" data-page="{page.index+1}">')
        by_id = {b.id: b for b in page.blocks}
        for b in sorted(page.blocks,key=lambda x:x.order):
            text=escape(b.text or "")
            if b.type==BlockType.HEADING: body.append(f'<h{min(b.level or 1,6)}>{text}</h{min(b.level or 1,6)}>')
            elif b.type==BlockType.LIST_ITEM: body.append(f'<li>{text}</li>')
            elif b.type==BlockType.TABLE and b.table and b.table.html: body.append(b.table.html)
            elif b.type==BlockType.FIGURE and b.figure:
                src=b.figure.file
                if self_contained:
                    import base64
                    data=base64.b64encode(Path(out.parent/src).read_bytes()).decode()
                    mime='image/png' if b.figure.format=='png' else 'image/jpeg'; src=f'data:{mime};base64,{data}'
                caption_block = by_id.get(b.figure.caption_id) if b.figure.caption_id else None
                cap = escape(caption_block.text if caption_block else "")
                body.append(f'<figure><img src="{escape(src)}"/><figcaption>{cap}</figcaption></figure>')
            elif b.type==BlockType.CAPTION and any(x.figure and x.figure.caption_id == b.id for x in page.blocks):
                continue
            elif text: body.append(f'<p>{text}</p>')
        body.append('</section>')
    html='<!doctype html><html><head><meta charset="utf-8"><style>body{font-family:"DejaVu Sans",sans-serif}figure{break-inside:avoid}img{max-width:100%;height:auto}.page{margin-bottom:2em}</style></head><body>'+''.join(body)+'</body></html>'
    out.write_text(html,encoding='utf-8'); return out
