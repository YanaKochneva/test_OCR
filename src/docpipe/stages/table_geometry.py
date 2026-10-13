"""Measure ruled table boundaries before rereading cells."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from docpipe.engines.base import PageMeta
from docpipe.ir import BBox, Block


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
