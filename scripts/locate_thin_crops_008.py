"""Reconstruct pre-fix projected bands from source pixels, without OCR/gold."""
from pathlib import Path
import json
import numpy as np
import pypdfium2 as pdfium
from docpipe.config import AppConfig
from docpipe.render.raster import bounded_dpi, rasterize_page

out = Path('data/refinement_audit/original_candidates')
out.mkdir(parents=True, exist_ok=True)
path = next(Path(r'C:\Users\andre\Desktop\тесты\tables\doc_tbl_008').glob('*_no_text.pdf'))
records = []
with pdfium.PdfDocument(path) as pdf:
    for index in range(len(pdf)):
        page = pdf[index]
        width, height = page.get_size()
        raster = rasterize_page(page, bounded_dpi(width, height, 300, AppConfig().limits), AppConfig().limits)
        image = raster.image
        dark = np.asarray(image.convert('L')) < 170
        bands = []
        for y in np.flatnonzero(dark.sum(axis=1) > max(12, image.width * .008)):
            if bands and y - bands[-1][1] <= 3: bands[-1][1] = int(y)
            else: bands.append([int(y), int(y)])
        for top, last in bands:
            bottom = last + 1
            if bottom-top < 8 or bottom-top > max(80, image.height*.04): continue
            pixels = dark[top:bottom]
            if (pixels.sum(axis=1) > image.width*.6).any(): continue
            columns = np.flatnonzero(pixels.sum(axis=0) >= 2)
            if len(columns) < 10: continue
            box = (max(0,int(columns[0])-6), max(0,top-6),
                   min(image.width,int(columns[-1])+7), min(image.height,bottom+6))
            w,h = box[2]-box[0],box[3]-box[1]
            ratio = min(1,2000/max(w,h))
            reduced = [round(int(v*ratio)/32)*32 for v in (w,h)]
            if min(reduced) == 0:
                stem = f'p{index+1}_{top}'
                with image.crop(box) as crop: crop.save(out / f'{stem}.png')
                records.append(dict(page=index+1, stage='original_projected_lines', box_px=box,
                                    original_size=(w,h), reduced_size=reduced, image=f'{stem}.png'))
        image.close()
        page.close()
(out/'candidates.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
print(json.dumps(records))
