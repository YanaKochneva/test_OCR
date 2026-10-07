from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from docpipe.config import FigureConfig
from docpipe.ir import BBox, Block, BlockType

@dataclass(frozen=True)
class PixelBox:
    x0: int; y0: int; x1: int; y1: int


def iou(a: PixelBox, b: PixelBox) -> float:
    x0=max(a.x0,b.x0); y0=max(a.y0,b.y0); x1=min(a.x1,b.x1); y1=min(a.y1,b.y1)
    inter=max(0,x1-x0)*max(0,y1-y0)
    union=(a.x1-a.x0)*(a.y1-a.y0)+(b.x1-b.x0)*(b.y1-b.y0)-inter
    return inter/union if union else 0.0


def merge_boxes(boxes: list[PixelBox], threshold: float) -> list[PixelBox]:
    result: list[PixelBox] = []
    for box in boxes:
        merged=False
        for i, old in enumerate(result):
            if iou(box, old) >= threshold:
                result[i]=PixelBox(min(box.x0,old.x0),min(box.y0,old.y0),max(box.x1,old.x1),max(box.y1,old.y1)); merged=True; break
        if not merged: result.append(box)
    return result


def filter_boxes(boxes: list[PixelBox], image_size: tuple[int,int], config: FigureConfig, block_count: int) -> list[PixelBox]:
    w,h=image_size; area=w*h; out=[]
    for b in boxes:
        bw,bh=b.x1-b.x0,b.y1-b.y0
        if bw < config.min_width_px or bh < config.min_height_px or bw*bh/area < config.min_area_ratio: continue
        if bw >= w and bh >= h and block_count == 0: continue
        out.append(b)
    return out


def crop_and_save(image: Image.Image, boxes: list[PixelBox], out_dir: Path, page_no: int) -> list[tuple[PixelBox,Path]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    result=[]
    for idx,b in enumerate(boxes,1):
        crop=image.crop((b.x0,b.y0,b.x1,b.y1))
        arr=np.asarray(crop)
        flat=arr.reshape(-1,arr.shape[-1]) if arr.ndim==3 else arr.reshape(-1,1)
        colors=len(np.unique(flat,axis=0)) if len(flat) else 999
        if colors <= 256:
            ext="png"; path=out_dir/f"p{page_no:03d}_fig{idx:02d}.png"; crop.save(path,format="PNG",optimize=True)
        else:
            ext="jpg"; path=out_dir/f"p{page_no:03d}_fig{idx:02d}.jpg"; crop.save(path,format="JPEG",quality=90)
        result.append((b,path))
    return result
