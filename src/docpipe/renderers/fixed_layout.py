"""Predictable typography for editable exports, matching the HTML defaults."""
import re
from docpipe.renderers.typography import FONT_FAMILY, TABLE_FONT_PT, text_style


def stable_document(document):
    """Return a render-only copy: never mutate recognized text or geometry."""
    def normalize(items, role):
        size,bold,italic=text_style(role)
        if role=='table':
            size,bold,italic=TABLE_FONT_PT,False,False
        if role=='table_header':
            size,bold,italic=TABLE_FONT_PT,True,False
        if role=='header':
            size,bold,italic=9.5,True,False
        if role in ('footer','page_number'):
            size,bold,italic=8,False,False
        result=[]
        for line in items or []:
            # Optical font ambiguity is irrelevant to an explicitly fixed style.
            optical=line.font_match_error is not None
            result.append(line.model_copy(update={
                'font_family':FONT_FAMILY,'font_size_pt':size,'bold':bold,'italic':italic,
                'runs':None,'horizontal_scale_pct':100,'glyph_offset_x_pt':0,
                'baseline_from_top_pt':None,'font_match_error':None,'font_match_margin':None,
                'review_required':False if optical else line.review_required,
                'review_reason':None if optical else line.review_reason}))
        return result or None
    pages=[]
    for page in document.pages:
        contents=any((b.text or '').strip().casefold() in ('содержание','оглавление') for b in page.blocks)
        blocks=[]
        for block in page.blocks:
            update={'lines':normalize(block.lines,block.type.value)}
            rows=(block.text or '').splitlines()
            if update['lines'] and len(rows)>1 and len(rows)==len(update['lines']):
                update['lines']=[line.model_copy(update={'text':text}) for line,text in zip(update['lines'],rows)]
                if contents:
                    from PIL import ImageFont
                    from docpipe.renderers.fonts import font_path,require_font
                    font=ImageFont.truetype(str(font_path(FONT_FAMILY,False,False) or require_font()),100)
                    repaired=[]
                    for line in update['lines']:
                        match=re.match(r'^(\d+(?:\.\d+)*\.\s+.+?)\s+(\d{1,4})$',line.text)
                        if match:
                            title,number=match.groups()
                            size=line.font_size_pt
                            gap=line.bbox.x1-line.bbox.x0-font.getlength(title+' '+number)*size/100
                            dots=max(0,int(gap/(font.getlength('.')*size/100)))
                            if dots:
                                line=line.model_copy(update={'text':title+' '+'.'*dots+number})
                        repaired.append(line)
                    update['lines']=repaired
            if block.table:
                update['table']=block.table.model_copy(update={'cells':[
                    cell.model_copy(update={'lines':normalize(cell.lines,'table_header' if cell.row==0 and
                        cell.background_color and cell.background_color.upper()!='#FFFFFF' else 'table')})
                    for cell in block.table.cells]})
            blocks.append(block.model_copy(update=update))
        pages.append(page.model_copy(update={'blocks':blocks}))
    return document.model_copy(update={'pages':pages})
