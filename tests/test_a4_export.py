from zipfile import ZipFile
from xml.etree import ElementTree as ET
import pypdfium2 as pdfium
from docpipe.ir import BBox,Line
from docpipe.renderers.page_size import a4_document,A4_WIDTH,A4_HEIGHT
from docpipe.renderers import render_docx,render_pdf_positional
from test_quality_regressions import block,document_with


def test_pixel_sized_scan_becomes_a4_without_mutating_ocr(tmp_path):
    box=BBox(x0=100,y0=100,x1=1400,y1=170)
    item=block('b','Original recognized text',(100,100,1400,170)).model_copy(update={
        'lines':[Line(text='Original recognized text',bbox=box)]})
    source=document_with([])
    source=source.model_copy(update={'pages':[source.pages[0].model_copy(update={
        'width_pt':1653,'height_pt':2339,'blocks':[item]})]})
    normalized=a4_document(source)
    assert normalized.pages[0].width_pt==A4_WIDTH
    assert normalized.pages[0].height_pt==A4_HEIGHT
    actual=normalized.pages[0].blocks[0]
    assert actual.text==item.text and actual.bbox.x1<510
    assert source.pages[0].width_pt==1653 and item.bbox==box
    render_docx(source,tmp_path/'document.docx')
    render_pdf_positional(source,tmp_path/'document.pdf')
    with ZipFile(tmp_path/'document.docx') as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        size=root.find('.//w:sectPr/w:pgSz',ns)
        assert abs(int(size.get('{'+ns['w']+'}w'))/20-A4_WIDTH)<.1
        assert abs(int(size.get('{'+ns['w']+'}h'))/20-A4_HEIGHT)<.1
    with pdfium.PdfDocument(tmp_path/'document.pdf') as pdf:
        assert abs(pdf[0].get_width()-A4_WIDTH)<.01
        assert abs(pdf[0].get_height()-A4_HEIGHT)<.01


def test_landscape_keeps_orientation():
    source=document_with([])
    source=source.model_copy(update={'pages':[source.pages[0].model_copy(update={
        'width_pt':2339,'height_pt':1653})]})
    page=a4_document(source).pages[0]
    assert page.width_pt==A4_HEIGHT and page.height_pt==A4_WIDTH
