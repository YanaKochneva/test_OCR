import json
from zipfile import ZipFile

import pikepdf

from docpipe.ir import BBox,Line
from docpipe.renderers.docx import render as render_docx
from docpipe.renderers.pdf_positional import render as render_pdf
from docpipe.style_review import review_items,write_style_review
from test_quality_regressions import block,document_with


def test_review_markers_are_exported_without_changing_text(tmp_path):
    line=Line(text='Needs review',bbox=BBox(x0=10,y0=100,x1=120,y1=113),
              font_family='Arial',font_size_pt=11,review_required=True,
              review_reason='Several fonts have similar glyph shapes')
    original=block('line',line.text,(10,100,120,113)).model_copy(update={'lines':[line]})
    doc=document_with([original])
    assert len(review_items(doc))==1
    report=write_style_review(doc,tmp_path)
    assert report['status']=='preliminary'
    assert json.loads((tmp_path/'style-review.json').read_text(encoding='utf-8'))['review_required']
    pdf=render_pdf(doc,tmp_path/'draft.pdf')
    word=render_docx(doc,tmp_path/'draft.docx')
    with pikepdf.Pdf.open(pdf) as output:
        assert len(output.pages[0].Annots)==1
        assert str(output.pages[0].Annots[0].Subtype)=='/Highlight'
    with ZipFile(word) as output:
        assert 'word/comments.xml' in output.namelist()
        assert 'Несколько шрифтов' in output.read('word/comments.xml').decode('utf-8')
    assert original.text=='Needs review'


def test_inline_review_is_reported_as_one_line():
    word=Line(text='word',bbox=BBox(x0=10,y0=100,x1=40,y1=113),
              review_required=True,review_reason='Weak fit')
    line=Line(text='word',bbox=word.bbox,runs=[word])
    doc=document_with([block('line',line.text,(10,100,40,113)).model_copy(update={'lines':[line]})])
    assert len(review_items(doc))==1
