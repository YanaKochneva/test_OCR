"""Render-copy typography and geometry guards; never change the stored OCR IR."""
from __future__ import annotations

import re
from statistics import median
from rapidfuzz.distance import Levenshtein


def _key(text):
    return ''.join(re.findall(r'\w+', text.casefold()))


def _intersection(a, b):
    return max(0, min(a.x1,b.x1)-max(a.x0,b.x0))*max(0,min(a.y1,b.y1)-max(a.y0,b.y0))


def _area(box):
    return max(.01,(box.x1-box.x0)*(box.y1-box.y0))


def _deduplicate(blocks):
    """Remove a composite OCR echo only when separate overlapping parts corroborate it."""
    output=[]
    for block in blocks:
        key=_key(block.text or '')
        if not key or block.table or block.type.value=='figure':
            output.append(block);continue
        parts=[b for b in blocks if b is not block and b.text and not b.table
               and b.type.value!='figure' and 0<len(_key(b.text))<=len(key)
               and _intersection(block.bbox,b.bbox)/min(_area(block.bbox),_area(b.bbox))>=.55]
        parts.sort(key=lambda b:(b.bbox.y0,b.bbox.x0,b.order))
        if parts and max(b.bbox.y0 for b in parts)-min(b.bbox.y0 for b in parts) <= 1.5*max(
                b.bbox.y1-b.bbox.y0 for b in parts):
            parts.sort(key=lambda b:(b.bbox.x0,b.bbox.y0,b.order))
        combined=''.join(_key(b.text) for b in parts)
        if (len(parts)>=2 and .9<=len(combined)/len(key)<=1.1
                and Levenshtein.normalized_similarity(key,combined)>=.94):
            continue
        output.append(block)
    return output


def _move_box(box, dy):
    return box.model_copy(update={'y0':box.y0+dy,'y1':box.y1+dy})


def _move_lines(lines,dy):
    return [line.model_copy(update={'bbox':_move_box(line.bbox,dy),
        'runs':_move_lines(line.runs,dy)}) for line in lines] if lines else None


def _uniform_cell(cell):
    if not cell.lines:
        return cell
    from docpipe.renderers.typography import TABLE_FONT_PT, FONT_FAMILY
    from docpipe.renderers.fonts import font_path, require_font
    from PIL import ImageFont
    lines=sorted(cell.lines,key=lambda line:(line.bbox.y0,line.bbox.x0))
    limit=min(TABLE_FONT_PT,median(line.font_size_pt or TABLE_FONT_PT for line in lines))
    for previous,following in zip(lines,lines[1:]):
        pitch=following.bbox.y0-previous.bbox.y0
        if pitch>1:
            limit=min(limit,pitch/1.12)
    available=max(1,cell.bbox.y1-lines[-1].bbox.y0-1.5)
    size=max(1,min(limit,available/1.12))
    size=int(size*2)/2 or .5  # match DOCX half-point serialization
    def normalize(line):
        family=line.font_family or FONT_FAMILY
        font=ImageFont.truetype(str(font_path(family,line.bold,line.italic) or require_font()),100)
        measured=font.getlength(line.text)*size/100
        width=(line.bbox.y1-line.bbox.y0 if line.rotation in (90,270)
               else line.bbox.x1-line.bbox.x0)
        scale=min(100,max(1,width/measured*100)) if measured else 100
        return line.model_copy(update={'font_size_pt':size,'horizontal_scale_pct':scale,
            'runs':[normalize(run) for run in line.runs] if line.runs else None})
    return cell.model_copy(update={'lines':[normalize(line) for line in lines]})


def prepare_layout(document):
    """Use identical cell sizes and collision offsets for positional renderers."""
    from docpipe.renderers.text_layout import estimate_text_height
    from docpipe.renderers.typography import text_style
    pages=[]
    for page in document.pages:
        blocks=_deduplicate(page.blocks)
        placed=[]
        results={}
        for block in sorted(blocks,key=lambda b:(b.bbox.y0,b.bbox.x0,b.order)):
            original_identity=id(block)
            if block.table:
                block=block.model_copy(update={'table':block.table.model_copy(update={
                    'cells':[_uniform_cell(c) for c in block.table.cells]})})
            box=block.bbox
            # Thin rules are intentional form fields, not occupied text rectangles.
            obstacle=block.type.value!='figure' or box.y1-box.y0>3
            height=box.y1-box.y0
            if block.lines:
                height=max(height,max(line.bbox.y0+1.2*(line.font_size_pt or 10)
                    for line in block.lines)-box.y0)
            elif block.text and not block.table:
                height=max(height,estimate_text_height(block.text,box.x1-box.x0,text_style(block.type.value)))
            top=box.y0
            if obstacle:
                for left,right,first,last in placed:
                    if min(box.x1,right)-max(box.x0,left)>2 and top<last+1 and top+height>first:
                        top=last+1
            dy=top-box.y0
            if abs(dy)>.001:
                updates={'bbox':_move_box(box,dy),'lines':_move_lines(block.lines,dy)}
                if block.table:
                    updates['table']=block.table.model_copy(update={'cells':[
                        c.model_copy(update={'bbox':_move_box(c.bbox,dy),'lines':_move_lines(c.lines,dy)})
                        for c in block.table.cells]})
                block=block.model_copy(update=updates)
            if obstacle:
                placed.append((box.x0,box.x1,top,top+height))
            results[original_identity]=block
        pages.append(page.model_copy(update={'blocks':[results[id(b)] for b in blocks]}))
    return document.model_copy(update={'pages':pages})
