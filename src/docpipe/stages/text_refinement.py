"""Reconcile independent line recognition with text blocks, preserving layout."""
from docpipe.ir import BlockType, Line
from docpipe.stages.ocr_recovery import recover_unassigned_lines
import re
from rapidfuzz.distance import Levenshtein


def preserves_existing_words(old, candidate):
    """Reject candidates that delete tokens, even when total length grows."""
    tokenize=lambda text:re.findall(r'\w+',text.casefold())
    before,after=tokenize(old),tokenize(candidate)
    return not any(op.tag=='delete' for op in Levenshtein.opcodes(before,after))


def merge_without_deletions(old, candidate):
    """Keep primary tokens where the independently read candidate has gaps."""
    before,after=old.split(),candidate.split()
    normalized=lambda words:[w.casefold() for w in words]
    output=[]
    for op in Levenshtein.opcodes(normalized(before),normalized(after)):
        if op.tag=='delete':output.extend(before[op.src_start:op.src_end])
        else:output.extend(after[op.dest_start:op.dest_end])
    return ' '.join(output)


def refine_text_blocks(blocks, lines, page_height, confirm_candidate=None):
    result = []
    for block in blocks:
        if block.type not in {
            BlockType.TEXT, BlockType.LIST_ITEM, BlockType.CAPTION,
            BlockType.FOOTNOTE, BlockType.HEADING,
        }:
            result.append(block)
            continue
        candidates = []
        for text, box, score in lines:
            if score < .85:
                continue
            cx, cy = (box.x0+box.x1)/2, (box.y0+box.y1)/2
            # A page-wide line can concatenate neighboring columns. Its center
            # alone does not establish that it belongs to this text block.
            width = max(.01, box.x1 - box.x0)
            intersection = max(0, min(box.x1, block.bbox.x1 + 2) - max(box.x0, block.bbox.x0 - 2))
            if (intersection / width >= .9
                    and block.bbox.x0-2 <= cx <= block.bbox.x1+2
                    and block.bbox.y0-2 <= cy <= block.bbox.y1+2):
                candidates.append((box.y0,box.x0,text,box,score))
        candidates.sort(key=lambda entry: (entry[0], entry[1]))
        candidate = ' '.join(entry[2] for entry in candidates)
        old = block.text or ''
        # Accept additions only when the independent pass corroborates most
        # original words, rather than replacing unrelated nearby content.
        words = old.casefold().split()
        proposed = candidate.casefold().split()
        overlap = sum(w in proposed for w in words)/max(1,len(words))
        conservative = (overlap >= .7 and len(candidate) >= len(old)
                        and preserves_existing_words(old, candidate))
        # A broken line (including falsely rotated crops) contains invented
        # tokens. Requiring every old token to survive prevents its correction.
        # Accept a complete, high-confidence geometric reread only when it
        # still anchors to a substantial part of the original block.
        normalized = lambda value: ' '.join(value.casefold().split())
        similarity = Levenshtein.normalized_similarity(normalized(old), normalized(candidate))
        corrected = (len(words) >= 6 and overlap >= .45 and similarity >= .5
                     and len(candidate) >= len(old) * .8
                     and candidates and min(entry[4] for entry in candidates) >= .95
                     and candidates[0][3].y0 <= block.bbox.y0 + 6
                     and candidates[-1][3].y1 >= block.bbox.y1 - 6)
        corroborated = False
        if (candidate and not conservative and not corrected and confirm_candidate is not None
                and len(candidates) >= 2 and len(candidate) >= len(old)
                and min(entry[4] for entry in candidates) >= .9
                and candidates[0][3].y0 <= block.bbox.y0 + 6
                and candidates[-1][3].y1 >= block.bbox.y1 - 6):
            corroborated = confirm_candidate(candidates)
        if candidate and (conservative or corrected or corroborated):
            block = block.model_copy(update={
                'text':candidate,'source_label':'line_ocr_refined',
                'lines':[Line(text=entry[2],bbox=entry[3]) for entry in candidates],
            })
        result.append(block)
    return recover_unassigned_lines(result,lines,page_height)
