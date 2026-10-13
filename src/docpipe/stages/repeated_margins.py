"""Classify recurrent marginal text using document evidence, without a reference."""
import math
import re

from rapidfuzz.distance import Levenshtein

from docpipe.ir import BlockType
from docpipe.stages.postprocess import postprocess


def _region(block, page):
    box = block.bbox
    width, height = box.x1-box.x0, box.y1-box.y0
    if height <= page.height_pt*.06:
        if box.y0 >= page.height_pt*.87:
            return 'footer'
        if box.y1 <= page.height_pt*.10:
            return 'header'
    if height >= page.height_pt*.25 and width <= page.width_pt*.07:
        if box.x1 <= page.width_pt*.11:
            return 'left'
        if box.x0 >= page.width_pt*.89:
            return 'right'
    return None


def classify_repeated_margins(pages, headers_footers='drop'):
    groups=[]
    candidates=[]
    for page_index,page in enumerate(pages):
        for block_index,block in enumerate(page.blocks):
            if block.type not in {BlockType.TEXT,BlockType.OTHER}:
                continue
            text=block.text or ''
            if not 5 <= len(text) <= 300:
                continue
            region=_region(block,page)
            if not region:
                continue
            fingerprint=re.sub(r'[^\w]+',' ',text.casefold())
            fingerprint=re.sub(r'\d+','',fingerprint)
            fingerprint=' '.join(fingerprint.split())
            if len(fingerprint)<5:
                continue
            match=next((group for group in groups if group['region']==region
                        and Levenshtein.normalized_similarity(group['text'],fingerprint)>=.85),None)
            if match is None:
                match={'region':region,'text':fingerprint,'pages':set()}
                groups.append(match)
            match['pages'].add(page_index)
            candidates.append((page_index,block_index,match))
    support=max(3,math.ceil(len(pages)*.10))
    selected={}
    for page_index,block_index,group in candidates:
        if len(group['pages'])>=support:
            selected.setdefault(page_index,{})[block_index]=(
                BlockType.HEADER if group['region']=='header' else BlockType.FOOTER)
    result=[]
    for index,page in enumerate(pages):
        updates=selected.get(index,{})
        blocks=[block.model_copy(update={'type':updates[bi],'source_label':'repeated_margin'})
                if bi in updates else block for bi,block in enumerate(page.blocks)]
        blocks=postprocess(blocks,headers_footers)
        blocks=[block.model_copy(update={'order':order}) for order,block in enumerate(blocks)]
        warnings=list(page.warnings)
        if updates:
            warnings.append(f'Reclassified recurrent margin blocks: {len(updates)}')
        result.append(page.model_copy(update={'blocks':blocks,'warnings':warnings}))
    return result
