"""Addressed/whole-document OCR audit with measured memory and export checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
import traceback
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import psutil
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[1]
SUITE = Path(r'C:\Users\andre\.codex\worktrees\1521\test_OCR\reports\suite-20261014-131410\suite.json')


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def export_check(doc, out):
    class Parser(HTMLParser):
        def __init__(self): super().__init__(); self.parts = []
        def handle_data(self, value): self.parts.append(value)
    parser = Parser(); parser.feed((out/'document.html').read_text(encoding='utf-8'))
    normalize = lambda value: ' '.join(value.split())
    html_text = normalize(' '.join(parser.parts))
    with ZipFile(out/'document.docx') as archive:
        xml = ET.fromstring(archive.read('word/document.xml'))
        names = archive.namelist()
    ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    docx_text = normalize(' '.join(t.text or '' for t in xml.findall('.//w:t',ns)))
    missing = {'docx':[], 'html':[]}
    figures = []
    tables = 0
    for page in doc.pages:
        for block in page.blocks:
            if block.figure: figures.append(block.figure.file)
            if block.table: tables += 1
            texts = [c.text for c in block.table.cells] if block.table else [block.text or '']
            for text in texts:
                if not text.strip(): continue
                for kind, actual in [('docx',docx_text),('html',html_text)]:
                    if normalize(text) not in actual:
                        missing[kind].append({'page':page.index+1,'block':block.id,'text':text})
    result = dict(missing_text=missing, ir_tables=tables,
        native_docx_tables=len(xml.findall('.//w:tbl',ns)), ir_figures=len(figures),
        docx_image_references=len(xml.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}blip')),
        docx_media=len([n for n in names if n.startswith('word/media/')]),
        missing_figure_files=[f for f in figures if not (out/f).is_file()],
        missing_html_images=[f for f in figures if f not in (out/'document.html').read_text(encoding='utf-8')])
    save(out/'export-check.json', result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True)
    parser.add_argument('--pages', help='1-based comma-separated page numbers; omitted = whole document')
    parser.add_argument('--run', default='targeted')
    args = parser.parse_args()
    out = ROOT/'reports/img-memory-20261014'/args.run/args.case
    out.mkdir(parents=True, exist_ok=True)
    os.environ['DOCPIPE_OCR_DIAGNOSTICS'] = str(out/'crops')
    logging.basicConfig(filename=out/'run.log', level=logging.INFO, encoding='utf-8')
    case = next(c for c in json.loads(SUITE.read_text(encoding='utf-8'))['cases'] if c['case']==args.case)
    source = Path(case['scan'])
    selected = [int(v)-1 for v in args.pages.split(',')] if args.pages else None
    phase = {'stage':'loading','page':None}
    stop = threading.Event()
    proc = psutil.Process()
    samples = []
    start = time.perf_counter()
    def sample():
        with (out/'memory.jsonl').open('w',encoding='utf-8') as stream:
            while not stop.is_set():
                mem = proc.memory_info()
                row = dict(seconds=time.perf_counter()-start, **phase, rss=mem.rss,
                           private=getattr(mem,'private',None), vms=mem.vms,
                           system_available=psutil.virtual_memory().available)
                samples.append(row); stream.write(json.dumps(row)+'\n'); stream.flush()
                stop.wait(.5)
    monitor = threading.Thread(target=sample,daemon=True); monitor.start()
    ocr_events = out/'ocr-calls.jsonl'
    class Meter:
        def __init__(self, backend, context): self.backend=backend; self.context=context
        def __getattr__(self,name): return getattr(self.backend,name)
        def __call__(self,image,*positional,**kwargs):
            size = list(image.size) if hasattr(image,'width') else [int(image.shape[1]),int(image.shape[0])]
            event = dict(phase, context=self.context(), input_size=size,
                         use_det=kwargs.get('use_det'), started_seconds=time.perf_counter()-start)
            begin = time.perf_counter()
            try: return self.backend(image,*positional,**kwargs)
            except Exception as exc:
                event.update(exception_type=type(exc).__name__,exception=repr(exc),traceback=traceback.format_exc())
                raise
            finally:
                event['elapsed_seconds']=time.perf_counter()-begin
                with ocr_events.open('a',encoding='utf-8') as stream: stream.write(json.dumps(event,ensure_ascii=False)+'\n')
    import docpipe.engines.safe_rapidocr as safe
    original_factory = safe.RapidOCR
    def metered_factory(*positional,**kwargs):
        reader = original_factory(*positional,**kwargs)
        reader.reader = Meter(reader.reader,lambda:dict(reader.context))
        return reader
    safe.RapidOCR = metered_factory
    from docpipe.config import AppConfig
    from docpipe.engines.docling_engine import DoclingEngine
    from docpipe.pipeline import _process_page
    from docpipe.ir import Document,SourceInfo,SourceType,TextLayerStatus
    from docpipe.stages.repeated_margins import classify_repeated_margins
    from docpipe.renderers import render_docx,render_html,render_markdown,render_pdf_flow,render_pdf_positional
    from docpipe.verify.checks import verify_document
    from docpipe.benchmark import reference_text
    from docpipe.evaluation import compare_text,_ir_text
    from docpipe.io.textlayer import analyze_pdf
    engine = DoclingEngine(); engine.load()
    scales=[]
    for pipeline in engine._converter.initialized_pipelines.values():
        scales.append(pipeline.ocr_model.scale)
        pipeline.ocr_model.reader = Meter(pipeline.ocr_model.reader,lambda:{'stage':'primary'})
    config = AppConfig()
    statuses = analyze_pdf(source,config.text_layer)
    pages=[]; timings=[]
    parse_start=time.perf_counter()
    with pdfium.PdfDocument(source) as pdf:
        selected = selected if selected is not None else list(range(len(pdf)))
        for index in selected:
            phase.update(stage='ocr',page=index+1)
            page_start=time.perf_counter(); before=proc.memory_info().rss
            page = pdf[index]
            try:
                result=_process_page(page,index,statuses[index],engine,config,out)
            finally:
                page.close()
            pages.append(result)
            row=dict(page=index+1,status=result.status.value,size_px=result.size_px,dpi=result.raster_dpi,
                     seconds=time.perf_counter()-page_start,rss_before=before,rss_after=proc.memory_info().rss,
                     peak_rss=max((s['rss'] for s in samples if s['seconds']>=page_start-start),default=0),
                     text_chars=sum(len(c.text) for b in result.blocks if b.table for c in b.table.cells)+
                                sum(len(b.text or '') for b in result.blocks),
                     tables=sum(b.table is not None for b in result.blocks),
                     figures=sum(b.figure is not None for b in result.blocks),warnings=result.warnings)
            timings.append(row)
            save(out/'page-results.json',timings)
            save(out/'checkpoint.json',{'source':str(source),'pages':[p.model_dump(mode='json') for p in pages]})
            print(json.dumps(dict(case=args.case,page=index+1,status=row['status'],seconds=round(row['seconds'],2),
                                  peak_rss_mib=round(row['peak_rss']/2**20,1),rss_after_mib=round(row['rss_after']/2**20,1))),flush=True)
    if not args.pages: pages=classify_repeated_margins(pages,config.render.headers_footers)
    doc=Document(source=SourceInfo(file=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),type=SourceType.PDF),
                 engine=engine.info(),pages=pages,timings={'parse_total':time.perf_counter()-parse_start})
    (out/'document.json').write_text(doc.model_dump_json(indent=2),encoding='utf-8')
    phase.update(stage='exports',page=None)
    render_docx(doc,out/'document.docx',out); render_html(doc,out/'document.html')
    render_markdown(doc,out/'document.md'); render_pdf_flow(doc,out/'document-flow.pdf',out)
    render_pdf_positional(doc,out/'document-positional.pdf',out)
    exports=export_check(doc,out)
    verification=verify_document(out); save(out/'verification.json',verification)
    comparison={}
    if not args.pages:
        gold=reference_text(Path(case['reference']))
        comparison['current']=compare_text(gold,_ir_text(doc.model_dump(mode='json')))
        comparison['baseline']=case.get('text')
    elif Path(case['reference']).suffix.lower()=='.pdf':
        gold_pages=[]
        with pdfium.PdfDocument(case['reference']) as reference:
            for index in selected:
                page=reference[index]
                textpage=page.get_textpage()
                try: gold_pages.append(textpage.get_text_range())
                finally: textpage.close(); page.close()
        comparison['current']=compare_text(' '.join(gold_pages), _ir_text(doc.model_dump(mode='json')))
        comparison['scope']='Selected reference PDF pages only; reference used after OCR.'
    save(out/'comparison.json',comparison)
    phase.update(stage='done'); stop.set(); monitor.join(timeout=2)
    summary=dict(case=args.case,source=str(source),selected_pages=[i+1 for i in selected],ocr_scale=scales,
                 memory_peak_rss=max(s['rss'] for s in samples),memory_peak_private=max(s['private'] or 0 for s in samples),
                 seconds=time.perf_counter()-start,parse_seconds=doc.timings['parse_total'],
                 pages=timings,verification=verification,exports=exports,comparison=comparison)
    save(out/'summary.json',summary)
    print(json.dumps(dict(case=args.case,done=True,verification=verification['result'],
                          seconds=round(summary['seconds'],2))),flush=True)


if __name__=='__main__': main()
