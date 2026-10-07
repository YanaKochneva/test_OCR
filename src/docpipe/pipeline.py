from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pypdfium2 as pdfium

from docpipe.config import AppConfig
from docpipe.engines import create_engine
from docpipe.engines.base import PageMeta
from docpipe.errors import InputError, PageError
from docpipe.io.normalize import normalize_input
from docpipe.io.textlayer import analyze_pdf
from docpipe.ir import Document, EngineInfo, Page, SourceInfo, SourceType, PageStatus
from docpipe.render.raster import rasterize_page
from docpipe.stages.order import assign_order
from docpipe.stages.postprocess import postprocess
from docpipe.stages.figures import PixelBox, crop_and_save, filter_boxes, merge_boxes
from docpipe.stages.masking import mask_figures
from docpipe.stages.captions import attach_captions


def parse(path: str | Path, config: AppConfig | None = None, engine: str="fake", fixtures: Path | None=None, out_dir: Path | None=None, engine_instance=None, progress_callback=None) -> Document:
    config=config or AppConfig()
    source=Path(path)
    pdf_path=normalize_input(source,config)
    cleanup=pdf_path != source
    statuses=analyze_pdf(pdf_path,config.text_layer)
    eng=engine_instance or create_engine(engine,fixtures=fixtures)
    if engine_instance is None:
        eng.load()
    pages=[]; started=time.perf_counter()
    doc=pdfium.PdfDocument(str(pdf_path))
    try:
        if len(doc)>config.limits.max_pages: raise InputError("Число страниц превышает лимит")
        total_pages = len(doc)
        for i in range(total_pages):
            page=doc[i]
            try:
                raster=rasterize_page(page, config.default_dpi)
                meta=PageMeta(i,raster.width_pt,raster.height_pt,raster.rotation,raster.dpi)
                try:
                    blocks=eng.analyze_page(raster.image,meta)
                    # Figure-блоки приходят из адаптера в координатах страницы.
                    # Здесь они единственный раз переводятся в пиксели для вырезки.
                    sx=raster.image.width/raster.width_pt
                    sy=raster.image.height/raster.height_pt
                    figure_boxes=[]
                    for b in blocks:
                        if b.type.value == "figure":
                            figure_boxes.append(PixelBox(
                                max(0,int(b.bbox.x0*sx)-config.figures.padding_px),
                                max(0,int(b.bbox.y0*sy)-config.figures.padding_px),
                                min(raster.image.width,int(b.bbox.x1*sx)+config.figures.padding_px),
                                min(raster.image.height,int(b.bbox.y1*sy)+config.figures.padding_px)))
                    figure_boxes=filter_boxes(merge_boxes(figure_boxes,config.figures.merge_iou),raster.image.size,config.figures,len(blocks))
                    # Маска является отдельной стадией и используется как вход для
                    # последующего OCR-шага адаптера в реальных движках.
                    masked_image = mask_figures(raster.image, figure_boxes)
                    _ = masked_image
                    if out_dir and figure_boxes:
                        saved=crop_and_save(raster.image,figure_boxes,out_dir/"images",i+1)
                        figure_blocks=[b for b in blocks if b.type.value=="figure"]
                        for b,(pb,path) in zip(figure_blocks,saved):
                            rel=str(Path("images")/path.name).replace("\\","/")
                            data=b.figure.model_copy(update={"file":rel,"width_px":pb.x1-pb.x0,"height_px":pb.y1-pb.y0}) if b.figure else None
                            if data: blocks[blocks.index(b)]=b.model_copy(update={"figure":data,"text":None})
                    blocks=assign_order(blocks,raster.width_pt)
                    blocks=attach_captions(blocks)
                    blocks=postprocess(blocks,config.render.headers_footers)
                    pages.append(Page(index=i,width_pt=raster.width_pt,height_pt=raster.height_pt,rotation=raster.rotation,raster_dpi=raster.dpi,size_px=raster.image.size,status=PageStatus.OK,text_layer=statuses[i],blocks=blocks))
                    if progress_callback:
                        progress_callback(i + 1, total_pages)
                except Exception as exc:
                    pages.append(Page(index=i,width_pt=raster.width_pt,height_pt=raster.height_pt,rotation=raster.rotation,raster_dpi=raster.dpi,size_px=raster.image.size,status=PageStatus.FAILED,text_layer=statuses[i],blocks=[],warnings=[f"Страница не обработана: {exc}"]))
                    if progress_callback:
                        progress_callback(i + 1, total_pages)
            finally:
                page.close()
    finally: doc.close()
    if cleanup: pdf_path.unlink(missing_ok=True)
    elapsed=time.perf_counter()-started
    return Document(source=SourceInfo(file=str(source),sha256=_sha256(source),type=SourceType.PDF if source.suffix.lower()=='.pdf' else SourceType.IMAGE),engine=eng.info(),pages=pages,timings={"parse_total":elapsed})

def _sha256(path: Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
    return h.hexdigest()
