"""Recover visible dash list markers discarded by the layout text serializer."""
import cv2
import numpy as np
from docpipe.ir import BlockType


def restore_list_markers(blocks, image, meta):
    sx, sy = image.width/meta.width_pt, image.height/meta.height_pt
    unit = meta.width_pt/595.276
    output = []
    for block in blocks:
        if block.type != BlockType.LIST_ITEM or not block.text or block.text.lstrip().startswith(('-', '–', '—', '•')):
            output.append(block)
            continue
        box = block.bbox
        left, top = max(0,round((box.x0-2*unit)*sx)), max(0,round(box.y0*sy))
        right = min(image.width,round((box.x0+18*unit)*sx))
        bottom = min(image.height,round(min(box.y1,box.y0+18*unit)*sy))
        if right<=left or bottom<=top:
            output.append(block)
            continue
        with image.crop((left,top,right,bottom)) as crop:
            ink=(np.asarray(crop.convert('L'))<150).astype('uint8')
        contours,_=cv2.findContours(ink,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        shapes=sorted([cv2.boundingRect(c) for c in contours if cv2.contourArea(c)>2])
        if len(shapes)>=2:
            x,y,w,h=shapes[0]
            visible=(w>=2*h and 2*unit*sx<=w<=7*unit*sx
                and h<=2*unit*sy and .25*ink.shape[0]<=y<=.8*ink.shape[0]
                and shapes[1][0]-(x+w)>=2)
            if visible:
                lines=block.lines
                if lines and not lines[0].text.lstrip().startswith('-'):
                    lines=[lines[0].model_copy(update={'text':'- '+lines[0].text}),*lines[1:]]
                block=block.model_copy(update={'text':'- '+block.text,'lines':lines})
        output.append(block)
    return output
