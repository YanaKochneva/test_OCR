"""Re-export saved recognition using source geometry; never run OCR or use gold."""
import json
import shutil
import sys
import time
from pathlib import Path

import pypdfium2 as pdfium

from docpipe.engines.base import PageMeta
from docpipe.evaluation import _ir_text
from docpipe.ir import Document
from docpipe.renderers import render_docx, render_pdf_positional, render_markdown, render_html, render_pdf_flow
from docpipe.stages.table_visual_layout import restore_table_visual_layout
from docpipe.stages.font_matching import restore_fonts
from docpipe.engines.docling_engine import DoclingEngine
from docpipe.verify.checks import verify_document


def main():
    report_path,out=map(Path,sys.argv[1:3])
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if out.exists():
        raise ValueError('Output must be new')
    out.mkdir(parents=True)
    render_only='--render-only' in sys.argv[3:]
    font_only='--font-only' in sys.argv[3:] or render_only
    reader=None if font_only else DoclingEngine()._get_refinement_reader()
    for index,case in enumerate(report['cases']):
        old=Path(case['output'])
        target=out/f'case-{index+1:04d}'
        target.mkdir()
        document=Document.model_validate_json((old/'document.json').read_text(encoding='utf-8'))
        before=_ir_text(document.model_dump())
        started=time.perf_counter()
        pages=[]
        with pdfium.PdfDocument(case['scan']) as pdf:
            for page in document.pages:
                source=pdf[page.index]
                image=source.render(scale=page.raster_dpi/72).to_pil()
                try:
                    meta=PageMeta(page.index,page.width_pt,page.height_pt,page.rotation,page.raster_dpi)
                    blocks=page.blocks if font_only else restore_table_visual_layout(page.blocks,image,meta,line_reader=reader)
                    if font_only:
                        from docpipe.stages.table_visual_layout import _rule_style
                        blocks=[block.model_copy(update={'table':block.table.model_copy(
                            update=_rule_style(block,image,meta))}) if block.table else block for block in blocks]
                    if not render_only:
                        blocks=restore_fonts(blocks,image,meta)
                    else:
                        from docpipe.stages.font_matching import guard_line_width,fit_font
                        def repair(line):
                            b=line.bbox
                            if b.y1-b.y0>4*(b.x1-b.x0) and b.x1-b.x0<page.width_pt*.08:
                                line=fit_font(line,image,meta).model_copy(update={'runs':None})
                            return guard_line_width(line)
                        blocks=[block.model_copy(update={
                            'lines':[repair(line) for line in block.lines] if block.lines else None,
                            **({'table':block.table.model_copy(update={'cells':[cell.model_copy(update={
                                'lines':[repair(line) for line in cell.lines] if cell.lines else None})
                                for cell in block.table.cells]})} if block.table else {})}) for block in blocks]
                    pages.append(page.model_copy(update={'blocks':blocks}))
                    print(f'{case["case"]} style pages: {page.index+1}/{len(document.pages)}',flush=True)
                finally:
                    image.close();source.close()
        document=document.model_copy(update={'pages':pages})
        assert _ir_text(document.model_dump())==before,'Layout stage changed recognized text'
        if (old/'images').exists():
            shutil.copytree(old/'images',target/'images')
        (target/'document.json').write_text(document.model_dump_json(indent=2),encoding='utf-8')
        render_pdf_positional(document,target/'document-positional.pdf',target)
        render_docx(document,target/'document.docx',target)
        render_markdown(document,target/'document.md')
        render_html(document,target/'document.html')
        render_pdf_flow(document,target/'document-flow.pdf',target)
        from docpipe.style_review import write_style_review
        case['style_review']=write_style_review(document,target)
        verification=verify_document(target)
        (target/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
        case.update(output=str(target.resolve()),verification=verification,
                    layout_processing_seconds=round(time.perf_counter()-started,3),recognition_changed=False)
        print(case['case'],verification['result'],flush=True)
    report['layout_note']='Source ink geometry and cell fills restored without OCR; recognized text and scores unchanged.'
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
