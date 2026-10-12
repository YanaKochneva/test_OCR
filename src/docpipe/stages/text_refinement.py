"""Reconcile independent line recognition with text blocks, preserving layout."""
from docpipe.ir import BlockType
from docpipe.stages.ocr_recovery import recover_unassigned_lines


def refine_text_blocks(blocks, lines, page_height):
    result = []
    for block in blocks:
        if block.type != BlockType.TEXT:
            result.append(block)
            continue
        candidates = []
        for text, box, score in lines:
            if score < .85:
                continue
            cx, cy = (box.x0+box.x1)/2, (box.y0+box.y1)/2
            if block.bbox.x0-2 <= cx <= block.bbox.x1+2 and block.bbox.y0-2 <= cy <= block.bbox.y1+2:
                candidates.append((box.y0,box.x0,text))
        candidate = ' '.join(t for _,_,t in sorted(candidates))
        old = block.text or ''
        # Accept additions only when the independent pass corroborates most
        # original words, rather than replacing unrelated nearby content.
        words = old.casefold().split()
        proposed = candidate.casefold().split()
        overlap = sum(w in proposed for w in words)/max(1,len(words))
        if candidate and overlap >= .7 and len(candidate) >= len(old):
            block = block.model_copy(update={'text':candidate,'source_label':'line_ocr_refined'})
        result.append(block)
    return recover_unassigned_lines(result,lines,page_height)
