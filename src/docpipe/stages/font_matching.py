"""Match visible glyph shapes to installed fonts without a gold document."""
from functools import lru_cache
from collections import Counter

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from docpipe.renderers.fonts import installed_fonts, supports_text,font_path

_unusable_fonts=set()


@lru_cache(maxsize=32)
def _font(path):
    return ImageFont.truetype(path,100)


def _tight(mask):
    ys,xs=np.nonzero(mask)
    if len(xs)<5:
        return None
    return mask[ys.min():ys.max()+1,xs.min():xs.max()+1]


def _compact(mask):
    # Justification changes word spaces without changing the font. Compare
    # glyph shapes after removing empty columns so spacing cannot masquerade
    # as a different family or a condensed/bold variant.
    return mask[:,mask.any(axis=0)]


def fit_font(line,image,meta,family_hint=None):
    if not line.text.strip():
        return line
    sx,sy=image.width/meta.width_pt,image.height/meta.height_pt
    b=line.bbox
    rotation=line.rotation
    if not rotation and b.y1-b.y0>4*(b.x1-b.x0) and b.x1-b.x0<meta.width_pt*.08:
        rotation=90
    with image.crop((max(0,int(b.x0*sx)),max(0,int(b.y0*sy)),
                     min(image.width,int(b.x1*sx+1)),min(image.height,int(b.y1*sy+1)))) as crop:
        if rotation:
            with crop.rotate(-rotation,expand=True) as upright:
                rgb=np.asarray(upright.convert('RGB'))
        else:
            rgb=np.asarray(crop.convert('RGB'))
        gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
        source=_tight(gray<170)
    if source is None:
        return line.model_copy(update={'review_required':True,'review_reason':'No usable glyph image for font matching'})
    ink=gray[gray<170]
    pixels=rgb[gray<=np.quantile(ink,.2)]
    color=np.median(pixels,axis=0).astype('uint8')
    color='#'+''.join(f'{int(v):02x}' for v in color)
    h,_=source.shape
    source_stroke=float(cv2.distanceTransform(source.astype('uint8'),cv2.DIST_L2,3)[source>0].mean())/h
    source=_compact(source)
    w=source.shape[1]
    target_w=min(1000,w)
    target_h=max(12,round(h*target_w/w))
    reference=cv2.resize(source.astype('uint8'),(target_w,target_h),interpolation=cv2.INTER_NEAREST)
    reference=cv2.copyMakeBorder(reference,3,3,3,3,cv2.BORDER_CONSTANT)
    source_distance=cv2.distanceTransform(1-reference,cv2.DIST_L2,3)
    candidates=[]
    for family,bold,italic,path in installed_fonts():
            if str(path) in _unusable_fonts:
                continue
            if family_hint and family!=family_hint:
                continue
            if not supports_text(str(path),''.join(sorted(set(line.text)))):
                continue
            variant=int(bold)+2*int(italic)
            try:
                font=_font(str(path))
                box=font.getbbox(line.text,anchor='ls')
            except (OSError,ValueError):
                _unusable_fonts.add(str(path));continue
            template_height=box[3]-box[1]
            if template_height<1:
                continue
            # Match at the scan's own pixel size. Upscaling a 100px glyph and
            # shrinking it changes hinting/stroke weight and falsely favors bold.
            pixel_size=max(4,round(h/template_height*100))
            try:
                font=ImageFont.truetype(str(path),pixel_size)
                box=font.getbbox(line.text,anchor='ls')
            except (OSError,ValueError):
                _unusable_fonts.add(str(path));continue
            width,height=box[2]-box[0],box[3]-box[1]
            if width<1 or height<1:
                continue
            mask=Image.new('L',(width+2,height+2))
            try:
                ImageDraw.Draw(mask).text((1-box[0],1-box[1]),line.text,font=font,fill=255,anchor='ls')
            except (OSError,ValueError):
                mask.close();_unusable_fonts.add(str(path));continue
            synthetic=_tight(np.asarray(mask)>100)
            mask.close()
            if synthetic is None:
                continue
            synthetic_stroke=float(cv2.distanceTransform(synthetic.astype('uint8'),cv2.DIST_L2,3)[synthetic>0].mean())/synthetic.shape[0]
            synthetic=_compact(synthetic)
            candidate=cv2.resize(synthetic.astype('uint8'),(target_w,target_h),interpolation=cv2.INTER_NEAREST)
            candidate=cv2.copyMakeBorder(candidate,3,3,3,3,cv2.BORDER_CONSTANT)
            distance=cv2.distanceTransform(1-candidate,cv2.DIST_L2,3)
            # Symmetric glyph distance tolerates scan noise, but penalizes
            # missing strokes and falsely bold or slanted letters.
            error=float(source_distance[candidate>0].mean()+distance[reference>0].mean())/max(1,target_h)
            error+=5*abs(source_stroke-synthetic_stroke)
            size=h/height*pixel_size/sy
            baseline=-box[1]/pixel_size*size
            candidates.append((error,family,variant,size,baseline,box[0]/pixel_size*size))
    if not candidates:
        return line.model_copy(update={'review_required':True,'review_reason':'No installed font supports the visible text'})
    candidates.sort()
    error,family,variant,size,baseline,offset=candidates[0]
    second=next((candidate[0] for candidate in candidates[1:] if candidate[1:3]!=(family,variant)),None)
    margin=second-error if second is not None else None
    reason=('Several fonts have similar glyph shapes' if margin is not None and margin<.004
            else 'Glyph fit is weak; text, font or mixed emphasis needs review' if error>.08 else None)
    if rotation:
        reason='Vertical text direction and content need review'
    return line.model_copy(update={'font_family':family,'bold':bool(variant&1),
        'italic':bool(variant&2),'font_size_pt':round(size,3),
        'baseline_from_top_pt':round(baseline,3),'glyph_offset_x_pt':round(offset,3),
        'font_match_error':round(error,4),'font_match_margin':round(margin,4) if margin is not None else None,
        'review_required':reason is not None,'review_reason':reason,'color':color,'rotation':rotation})


def restore_fonts(blocks,image,meta):
    result=[]
    for block in blocks:
        changes={}
        if block.text and not block.table and not block.lines:
            lines=_recover_text_lines(block,image,meta)
            if lines:
                changes['lines']=lines
        if block.lines:
            changes['lines']=_fit_group(block.lines,image,meta)
        elif changes.get('lines'):
            changes['lines']=_fit_group(changes['lines'],image,meta)
        if block.table:
            cells=[cell.model_copy(update={'lines':_fit_group(cell.lines,image,meta)})
                   if cell.lines else cell for cell in block.table.cells]
            changes['table']=block.table.model_copy(update={'cells':cells})
        result.append(block.model_copy(update=changes) if changes else block)
    return result


def _fit_group(lines,image,meta):
    probes=sorted(range(len(lines)),key=lambda i:len(lines[i].text),reverse=True)[:3]
    measured={i:_probe_line(lines[i],image,meta) for i in probes}
    support=Counter(line.font_family for line in measured.values() if line.font_family
                    and line.font_match_error is not None and line.font_match_error<.1)
    family=None
    if support:
        candidate,count=support.most_common(1)[0]
        if count>=2 and count/sum(support.values())>=.66:
            family=candidate
    fitted=[]
    for i,line in enumerate(lines):
        result=measured.get(i) or fit_font(line,image,meta,family_hint=family)
        if family and result.font_match_error is not None and result.font_match_error>.12:
            result=fit_font(line,image,meta)
        result=_fit_word_runs(result,image,meta)
        result=guard_line_width(result)
        fitted.append(result)
    return fitted


def guard_line_width(line):
    if line.runs:
        return line.model_copy(update={'runs':[guard_line_width(run) for run in line.runs]})
    path=font_path(line.font_family,line.bold,line.italic) if line.font_family else None
    if not path or not line.font_size_pt:
        return line
    box=_font(str(path)).getbbox(line.text,anchor='ls')
    width=(box[2]-box[0])*line.font_size_pt/100
    available=line.bbox.y1-line.bbox.y0 if line.rotation in (90,270) else line.bbox.x1-line.bbox.x0
    ratio=available/width if width else 1
    if ratio>1.3 or ratio<.7:
        return line.model_copy(update={'horizontal_scale_pct':min(100,ratio*100),
            'review_required':True,'review_reason':'Line width includes graphics, leaders or recognition gaps; spacing needs review'})
    return line.model_copy(update={'horizontal_scale_pct':ratio*100})


def _probe_line(line,image,meta):
    regions=_word_regions(line,image,meta)
    probes=sorted((r for r in (regions or []) if len(r.text)>=5),key=lambda r:len(r.text),reverse=True)[:2]
    measured=[fit_font(region,image,meta) for region in probes]
    if (len(measured)==2 and measured[0].font_family
            and measured[0].font_family==measured[1].font_family
            and all(r.font_match_error is not None and r.font_match_error<.1 for r in measured)):
        return fit_font(line,image,meta,family_hint=measured[0].font_family)
    return fit_font(line,image,meta)


def _fit_word_runs(line,image,meta):
    """Recover mixed inline emphasis when pixel word boundaries are unambiguous."""
    if not line.font_family:
        return line
    regions=_word_regions(line,image,meta)
    if not regions:
        return line
    runs=[]
    for local in regions:
        local=local.model_copy(update={'font_family':line.font_family,'font_size_pt':line.font_size_pt,
                                      'bold':line.bold,'italic':line.italic})
        fitted=fit_font(local,image,meta,family_hint=line.font_family) if len(local.text)>=4 else local
        updates={'font_size_pt':line.font_size_pt}
        if line.baseline_from_top_pt is not None:
            updates['baseline_from_top_pt']=max(0,line.bbox.y0+line.baseline_from_top_pt-local.bbox.y0)
        fitted=fitted.model_copy(update=updates)
        runs.append(fitted)
    return line.model_copy(update={'runs':runs})


def _word_regions(line,image,meta):
    from docpipe.ir import BBox,Line
    words=line.text.split()
    if len(words)<2 or line.rotation:
        return None
    sx,sy=image.width/meta.width_pt,image.height/meta.height_pt
    b=line.bbox
    left,top=max(0,int(b.x0*sx)),max(0,int(b.y0*sy))
    with image.crop((left,top,min(image.width,int(b.x1*sx+1)),min(image.height,int(b.y1*sy+1)))) as crop:
        ink=np.asarray(crop.convert('L'))<170
    columns=np.flatnonzero(ink.any(axis=0))
    groups=[]
    gap=max(3,round(ink.shape[0]*.18))
    for x in columns:
        if groups and x-groups[-1][-1]<=gap:
            groups[-1].append(int(x))
        else:
            groups.append([int(x)])
    if len(groups)!=len(words):
        return None
    regions=[]
    for word,group in zip(words,groups):
        ys=np.flatnonzero(ink[:,group[0]:group[-1]+1].any(axis=1))
        if not len(ys):
            return None
        local=Line(text=word,bbox=BBox(x0=(left+group[0])/sx,x1=(left+group[-1]+1)/sx,
            y0=(top+int(ys[0]))/sy,y1=(top+int(ys[-1])+1)/sy),
            font_family=line.font_family,font_size_pt=line.font_size_pt,bold=line.bold,italic=line.italic)
        regions.append(local)
    return regions


def _recover_text_lines(block,image,meta):
    from docpipe.ir import BBox,Line
    from docpipe.stages.table_visual_layout import split_at_visual_lines
    sx,sy=image.width/meta.width_pt,image.height/meta.height_pt
    b=block.bbox
    left,top=max(0,int(b.x0*sx)),max(0,int(b.y0*sy))
    right,bottom=min(image.width,int(b.x1*sx+1)),min(image.height,int(b.y1*sy+1))
    if right<=left or bottom<=top:
        return None
    with image.crop((left,top,right,bottom)) as crop:
        ink=np.asarray(crop.convert('L'))<170
    indices=np.flatnonzero(ink.sum(axis=1)>max(2,ink.shape[1]*.002))
    bands=[]
    for y in indices:
        if bands and y-bands[-1][1]<=3:
            bands[-1][1]=int(y)
        else:
            bands.append([int(y),int(y)])
    boxes=[]
    for start,end in bands:
        columns=np.flatnonzero(ink[start:end+1].sum(axis=0)>=2)
        if end-start<4 or len(columns)<8:
            continue
        boxes.append(BBox(x0=(left+int(columns[0]))/sx,x1=(left+int(columns[-1])+1)/sx,
                          y0=(top+start)/sy,y1=(top+end+1)/sy))
    chunks=split_at_visual_lines(block.text,[box.x1-box.x0 for box in boxes])
    return [Line(text=text,bbox=box) for text,box in zip(chunks,boxes)] if chunks else None
