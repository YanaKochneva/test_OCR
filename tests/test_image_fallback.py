import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image,ImageDraw
from reportlab.pdfgen import canvas
from docpipe.config import AppConfig
from docpipe.ir import Block,BBox,TableData,TableCell,SourceInfo
from docpipe.render.raster import RasterPage
from docpipe.stages.image_fallback import fallback_unreadable,_replace_regions,has_fallback
from docpipe.output import render_bundle
from docpipe.review import review_after_render
from docpipe.verify.checks import verify_document
from docpipe.renderers.pdf_positional import render as real_pdf
from test_regressions import document


class ImageFallbackTests(unittest.TestCase):
    def raster(self):
        image=Image.new('RGB',(595,842),'white')
        ImageDraw.Draw(image).text((35,40),'VISIBLE SOURCE',fill='black')
        return RasterPage(image=image,width_pt=595.28,height_pt=841.89,rotation=0,dpi=72)

    def test_low_confidence_text_becomes_a_region_image(self):
        block=document().pages[0].blocks[0].model_copy(update={'confidence':.2})
        raster=self.raster()
        try:
            with tempfile.TemporaryDirectory() as folder:
                result=fallback_unreadable([block],raster,AppConfig(),Path(folder),0)
                self.assertEqual(result[0].type,'figure');self.assertIsNone(result[0].text)
                self.assertEqual(result[0].bbox,block.bbox)
                with Image.open(Path(folder)/result[0].figure.file) as image:
                    self.assertLess(image.height,raster.image.height/2)
                report=json.loads((Path(folder)/'image-fallback.json').read_text(encoding='utf-8'))
                self.assertEqual(report['regions'][0]['original_ocr_text'],block.text)
        finally:raster.image.close()

    def test_whole_page_fallback_is_forbidden(self):
        block=document().pages[0].blocks[0].model_copy(update={'confidence':.1,
            'bbox':BBox(x0=0,y0=0,x1=595.28,y1=841.89)})
        raster=self.raster()
        try:
            with tempfile.TemporaryDirectory() as folder:
                result=fallback_unreadable([block],raster,AppConfig(),Path(folder),0)
                self.assertEqual(result[0].type,'text')
                self.assertFalse((Path(folder)/'images').exists())
        finally:raster.image.close()

    def test_only_failed_cell_becomes_an_image_above_the_table(self):
        cells=[TableCell(row=0,col=0,text='BAD',bbox=BBox(x0=30,y0=30,x1=150,y1=65)),
               TableCell(row=0,col=1,text='GOOD',bbox=BBox(x0=150,y0=30,x1=300,y1=65))]
        block=Block(id='table',type='table',order=5,bbox=BBox(x0=30,y0=30,x1=300,y1=65),
            table=TableData(n_rows=1,n_cols=2,cells=cells))
        raster=self.raster()
        try:
            with tempfile.TemporaryDirectory() as folder:
                result,count=_replace_regions([block],{('table',(0,0)):'test'},raster,Path(folder),0)
                self.assertEqual(count,1)
                self.assertEqual([c.text for c in result[0].table.cells],['','GOOD'])
                self.assertGreater(result[1].order,result[0].order)
                self.assertEqual(cells[0].text,'BAD')
        finally:raster.image.close()

    def test_empty_cell_with_visible_ink_is_cropped_but_blank_cell_is_preserved(self):
        cells=[TableCell(row=0,col=0,text='',bbox=BBox(x0=30,y0=30,x1=150,y1=65)),
               TableCell(row=0,col=1,text='',bbox=BBox(x0=150,y0=30,x1=300,y1=65))]
        block=Block(id='table',type='table',order=0,bbox=BBox(x0=30,y0=30,x1=300,y1=65),
            table=TableData(n_rows=1,n_cols=2,cells=cells))
        raster=self.raster()
        try:
            with tempfile.TemporaryDirectory() as folder:
                result=fallback_unreadable([block],raster,AppConfig(),Path(folder),0)
                figures=[b for b in result if b.figure]
                self.assertEqual(len(figures),1)
                self.assertEqual(figures[0].bbox,cells[0].bbox)
        finally:raster.image.close()

    def test_service_bundle_has_no_docx_and_no_docx_read_error(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)
            (out/'document.docx').write_bytes(b'stale previous output')
            cfg=AppConfig()
            doc=render_bundle(document(),cfg,out,['docx'])
            self.assertFalse((out/'document.docx').exists())
            report=review_after_render(doc,cfg,out)
            self.assertNotIn('docx_read_error',[i['kind'] for i in report['issues']])
            verification=verify_document(out)
            self.assertTrue(verification['ok'])
            self.assertEqual(next(c['status'] for c in verification['checks'] if c['check']=='docx_structure'),'SKIP')

    def test_export_loss_is_recovered_from_original_pixels_then_reverified(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);source=out/'source.pdf'
            pdf=canvas.Canvas(str(source),pagesize=(595.28,841.89))
            pdf.drawString(30,795,'KEEP ME');pdf.drawString(30,720,'OTHER');pdf.save()
            doc=document('KEEP ME')
            doc.source=SourceInfo(file=str(source),sha256='test',type='pdf')
            doc.pages[0].blocks.append(Block(id='other',type='text',order=1,text='OTHER',
                bbox=BBox(x0=30,y0=100,x1=450,y1=135)))
            def omit_failed_text(document,target,base):
                pages=[]
                for page in document.pages:
                    blocks=[b for b in page.blocks if not (b.id=='text' and b.type=='text')]
                    pages.append(page.model_copy(update={'blocks':blocks}))
                return real_pdf(document.model_copy(update={'pages':pages}),target,base)
            with patch('docpipe.renderers.render_pdf_positional',side_effect=omit_failed_text):
                result=render_bundle(doc,AppConfig(),out)
            self.assertTrue(has_fallback(result))
            self.assertEqual(result.pages[0].blocks[0].type,'figure')
            self.assertEqual(doc.pages[0].blocks[0].type,'text')
            report=review_after_render(result,AppConfig(),out)
            self.assertEqual(report['issues'],[])
            self.assertTrue(verify_document(out)['ok'])

if __name__=='__main__':unittest.main()
