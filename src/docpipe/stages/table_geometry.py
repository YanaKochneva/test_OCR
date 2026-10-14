"""Measure ruled table boundaries before rereading cells."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from docpipe.engines.base import PageMeta
from docpipe.ir import BBox, Block, TableCell


def rebuild_verified_grid(block: Block, image: Image.Image, meta: PageMeta) -> Block:
    """Correct a plain ruled grid only when every separator crosses the table.

    Preserve every populated source cell. Ambiguous mappings, partial separators,
    merged cells and borderless tables retain the original model prediction.
    """
    table = block.table
    if not table or table.n_cols < 2 or any(c.rowspan != 1 or c.colspan != 1 for c in table.cells):
        return block
    sx, sy = image.width/meta.width_pt, image.height/meta.height_pt
    b = block.bbox
    left, top = max(0, int(b.x0*sx)-8), max(0, int(b.y0*sy)-8)
    right, bottom = min(image.width, int(b.x1*sx)+8), min(image.height, int(b.y1*sy)+8)
    if right-left < 40 or bottom-top < 30:
        return block
    with image.crop((left, top, right, bottom)) as crop:
        with crop.convert('L') as gray_image:
            gray = np.asarray(gray_image)
            ink = (cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY_INV,31,12) > 0).astype('uint8')
    h, w = ink.shape
    horizontal = cv2.morphologyEx(cv2.dilate(ink,np.ones((7,1),np.uint8)),
        cv2.MORPH_OPEN, np.ones((1, max(20,w//3)),np.uint8))
    vertical = cv2.morphologyEx(cv2.dilate(ink,np.ones((1,5),np.uint8)),
        cv2.MORPH_OPEN, np.ones((max(20,h//3),1),np.uint8))
    def centers(values, threshold):
        groups = []
        for value in np.flatnonzero(values > threshold):
            if groups and value-groups[-1][-1] <= 4:
                groups[-1].append(int(value))
            else:
                groups.append([int(value)])
        return [sum(group)/len(group) for group in groups]
    xs, ys = centers(vertical.sum(axis=0), h*.75), centers(horizontal.sum(axis=1),w*.75)
    if (len(xs) != table.n_cols+1 or len(ys) < 3 or len(ys)-1 < table.n_rows
            or (len(ys)-1)*table.n_cols > 4096):
        return block
    if xs[-1]-xs[0] < w*.8 or ys[-1]-ys[0] < h*.8:
        return block
    if min(np.diff(xs)) < 12 or min(np.diff(ys)) < 8:
        return block
    # A separator present over most of the table can still be absent in a
    # merged header. Verify every individual row/column, not just global sums.
    for x in xs:
        for y0, y1 in zip(ys, ys[1:]):
            strip = vertical[int(y0)+3:int(y1)-2,
                             max(0,int(x)-3):min(w,int(x)+4)]
            if strip.size == 0 or np.mean(strip.max(axis=1)>0) < .7:
                return block
    for y in ys:
        for x0, x1 in zip(xs, xs[1:]):
            strip = horizontal[max(0,int(y)-4):min(h,int(y)+5),
                               int(x0)+3:int(x1)-2]
            if strip.size == 0 or np.mean(strip.max(axis=0)>0) < .7:
                return block
    grouped = {}
    for cell in table.cells:
        if not cell.text.strip():
            continue
        cb = cell.bbox
        cx, cy = (cb.x0+cb.x1)*sx/2-left, (cb.y0+cb.y1)*sy/2-top
        col, row = int(np.searchsorted(xs,cx)-1), int(np.searchsorted(ys,cy)-1)
        if not (0 <= col < len(xs)-1 and 0 <= row < len(ys)-1):
            return block
        # Reject a source rectangle crossing several measured cells. Never split
        # a paragraph arbitrarily to force the model into the new grid.
        area = max(.01,(cb.x1-cb.x0)*(cb.y1-cb.y0))
        target = BBox(x0=(left+xs[col])/sx, x1=(left+xs[col+1])/sx,
                      y0=(top+ys[row])/sy, y1=(top+ys[row+1])/sy)
        overlap = max(0,min(cb.x1,target.x1)-max(cb.x0,target.x0))*max(
            0,min(cb.y1,target.y1)-max(cb.y0,target.y0))
        if overlap/area < .8:
            return block
        if col != cell.col or (row,col) in grouped:
            # Weak rules must not collapse existing rows or concatenate two
            # independently populated source cells into one inferred slot.
            return block
        grouped[(row,col)] = [cell]
    cells = []
    for row in range(len(ys)-1):
        for col in range(len(xs)-1):
            parts = sorted(grouped.get((row,col),[]),key=lambda c:(c.bbox.y0,c.bbox.x0))
            cells.append(TableCell(row=row,col=col,text='\n'.join(c.text for c in parts),
                bbox=BBox(x0=(left+xs[col])/sx,x1=(left+xs[col+1])/sx,
                          y0=(top+ys[row])/sy,y1=(top+ys[row+1])/sy)))
    return block.model_copy(update={'table':table.model_copy(update={
        'cells':cells,'n_rows':len(ys)-1,'n_cols':len(xs)-1,'html':None})})


def adaptive_reading_boxes(block, image, meta):
    """Expand OCR crops using proven row rules and column separators.

    This does not change renderer geometry. An absent outer rule uses the
    existing table envelope, never an inferred row or a neighbour's content.
    """
    if not block.table or block.table.n_cols < 2:
        return {}
    table=block.table
    sx,sy=image.width/meta.width_pt,image.height/meta.height_pt
    b=block.bbox
    left,top=max(0,int(b.x0*sx)-10),max(0,int(b.y0*sy)-10)
    right,bottom=min(image.width,int(b.x1*sx)+10),min(image.height,int(b.y1*sy)+10)
    if right<=left or bottom<=top:return {}
    gray=np.asarray(image.crop((left,top,right,bottom)).convert('L'))
    h,w=gray.shape
    ink=(cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,31,12)>0).astype('uint8')
    horizontal=cv2.morphologyEx(cv2.dilate(ink,np.ones((7,1),np.uint8)),cv2.MORPH_OPEN,np.ones((1,max(20,w//4)),np.uint8))
    vertical=cv2.morphologyEx(cv2.dilate(ink,np.ones((1,5),np.uint8)),cv2.MORPH_OPEN,np.ones((max(20,h//8),1),np.uint8))
    def centers(values,threshold):
        groups=[]
        for value in np.flatnonzero(values>threshold):
            if groups and value-groups[-1][-1]<=4:groups[-1].append(int(value))
            else:groups.append([int(value)])
        return [sum(g)/len(g) for g in groups]
    ys=centers(horizontal.sum(axis=1),w*.5)
    xs=centers(vertical.sum(axis=0),h*.35)
    if len(ys)!=table.n_rows+1:return {}
    separators=[]
    for col in range(table.n_cols-1):
        before=[(c.bbox.x0+c.bbox.x1)*sx/2-left for c in table.cells if c.col==col and c.colspan==1]
        after=[(c.bbox.x0+c.bbox.x1)*sx/2-left for c in table.cells if c.col==col+1 and c.colspan==1]
        if not before or not after:return {}
        choices=[x for x in xs if max(before)<x<min(after)]
        if len(choices)!=1:return {}
        separators.append(choices[0])
    bounds=[(b.x0*sx-left),*separators,(b.x1*sx-left)]
    boxes={}
    for cell in table.cells:
        if cell.row+cell.rowspan>=len(ys) or cell.col+cell.colspan>=len(bounds):return {}
        cy=(cell.bbox.y0+cell.bbox.y1)*sy/2-top
        if not ys[cell.row]-4<=cy<=ys[cell.row+cell.rowspan]+4:return {}
        boxes[(cell.row,cell.col)]=BBox(x0=(left+bounds[cell.col])/sx,
            x1=(left+bounds[cell.col+cell.colspan])/sx,
            y0=(top+ys[cell.row])/sy,y1=(top+ys[cell.row+cell.rowspan])/sy)
    return boxes


def measured_cell_boxes(
    block: Block, image: Image.Image, meta: PageMeta
) -> dict[tuple[int, int], BBox]:
    """Use a complete detected grid; retain OCR boxes when it is ambiguous.

    OCR cell boxes often enclose only recognized text. Reading those boxes
    again cannot recover words outside that already recognized area.
    """
    if block.table is None:
        return {}
    sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
    box = block.bbox
    left, top = max(0, int(box.x0 * sx) - 10), max(0, int(box.y0 * sy) - 10)
    right = min(image.width, int(box.x1 * sx) + 10)
    bottom = min(image.height, int(box.y1 * sy) + 10)
    if right <= left or bottom <= top:
        return {}
    gray = np.asarray(image.crop((left, top, right, bottom)).convert('L'))
    mask = (gray < 180).astype('uint8')
    height, width = mask.shape
    horizontal = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((1, max(20, width // 4)), np.uint8)
    )
    vertical = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN, np.ones((max(20, height // 8), 1), np.uint8)
    )

    def centers(counts, threshold):
        groups: list[list[int]] = []
        for value in np.flatnonzero(counts>threshold):
            if groups and value - groups[-1][-1] <= 4:
                groups[-1].append(int(value))
            else:
                groups.append([int(value)])
        return [sum(group) / len(group) for group in groups]

    ys = centers(horizontal.sum(axis=1), width * .5)
    xs = centers(vertical.sum(axis=0), height * .35)
    if len(xs) != block.table.n_cols + 1 or len(ys) != block.table.n_rows + 1:
        return {}
    boxes: dict[tuple[int, int], BBox] = {}
    for cell in block.table.cells:
        if (cell.row < 0 or cell.col < 0
                or cell.col+cell.colspan>=len(xs)
                or cell.row+cell.rowspan>=len(ys)):
            return {}
        boxes[(cell.row, cell.col)] = BBox(
            x0=(left + xs[cell.col]) / sx,
            x1=(left + xs[cell.col + cell.colspan]) / sx,
            y0=(top + ys[cell.row]) / sy,
            y1=(top + ys[cell.row + cell.rowspan]) / sy,
        )
    return boxes
