import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import pypdfium2 as pdfium

from docpipe.ir import BBox, Block, Document, EngineInfo, FigureData, Page, SourceInfo, TableCell, TableData
from docpipe.renderers.pdf_flow import render
from docpipe.verify.checks import _figure_leakage, verify_document


def document(blocks):
    return Document(source=SourceInfo(file='scan.pdf', sha256='0'*64, type='pdf'),
                    engine=EngineInfo(name='docling', version='test'), pages=[
                        Page(index=0, width_pt=595, height_pt=842, raster_dpi=300,
                             size_px=(2480,3508), status='ok', text_layer='absent', blocks=blocks)])


class ExportFailureTests(unittest.TestCase):
    def test_reportlab_preserves_cyrillic_angles_ampersands_and_literal_markup(self):
        text = 'ОПЛАТА<RUB\nОДОБРЕНО <RC:00\nАдрес <user@example.com> & A&B\n<b>текст</b> &lt;'
        block = Block(id='text', type='text', order=0, text=text,
                      bbox=BBox(x0=30,y0=30,x1=500,y1=150))
        cell = TableCell(row=0,col=0,text='Сумма < 100 & НДС',bbox=BBox(x0=30,y0=160,x1=500,y1=190))
        table = Block(id='table',type='table',order=1,bbox=cell.bbox,
                      table=TableData(n_rows=1,n_cols=1,cells=[cell]))
        doc = document([block,table]); original = doc.model_dump_json()
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules,{'weasyprint':None}):
            out = Path(folder)/'flow.pdf';render(doc,out,Path(folder))
            with pdfium.PdfDocument(out) as pdf:
                page=pdf[0];textpage=page.get_textpage()
                try: extracted=textpage.get_text_range()
                finally: textpage.close();page.close()
            for expected in ('ОПЛАТА<RUB','ОДОБРЕНО <RC:00','<user@example.com>',
                             'A&B','<b>текст</b>','&lt;','Сумма < 100 & НДС'):
                self.assertIn(expected,extracted)
            self.assertFalse(out.with_suffix('.flow.html').exists())
        self.assertEqual(doc.model_dump_json(),original)

    def pictogram_document(self,text='Х',width=15,height=30,label=None):
        figure = Block(id='picture',type='figure',order=0,
                       bbox=BBox(x0=100,y0=100,x1=100+width,y1=100+height),
                       source_label=label,figure=FigureData(file='icon.png',format='png',width_px=64,height_px=124))
        block = Block(id='glyph',type='text',order=1,text=text,
                      bbox=BBox(x0=105,y0=124,x1=113,y1=132))
        return document([figure,block])

    def test_small_pictogram_glyph_is_preserved_and_requires_review(self):
        doc=self.pictogram_document();original=doc.model_dump_json()
        self.assertEqual(_figure_leakage(doc),[])
        self.assertEqual(_figure_leakage(doc,pictograms=True),[{'page':1,'block':'glyph','figure':'picture'}])
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);Image.new('RGB',(64,124),'white').save(out/'icon.png')
            (out/'document.json').write_text(doc.model_dump_json(),encoding='utf-8')
            report=verify_document(out)
            checks={item['check']:item for item in report['checks']}
            self.assertEqual(checks['figure_leakage']['status'],'PASS')
            self.assertEqual(checks['figure_pictogram_overlap']['status'],'WARN')
        self.assertEqual(doc.model_dump_json(),original)
        self.assertEqual(doc.pages[0].blocks[1].text,'Х')

    def test_real_words_numbers_and_large_graphics_still_block(self):
        for text,width,height,label in [('ТЕКСТ',15,30,None),('1',15,30,None),
                                        ('Х',100,100,None),('Х',15,30,'ocr_image_fallback'),
                                        ('Х',15,30,'graphic_field')]:
            with self.subTest(text=text,width=width,label=label):
                doc=self.pictogram_document(text,width,height,label)
                self.assertEqual(len(_figure_leakage(doc)),1)
                self.assertEqual(_figure_leakage(doc,pictograms=True),[])


if __name__=='__main__':
    unittest.main()
