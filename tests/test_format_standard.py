from docx import Document
from docx.shared import Pt
from docpipe.stages.format_standard import extract_standard, apply_standard
from docpipe.ir import Block, BlockType, BBox, Line


def test_standard_is_external_and_preserves_source(tmp_path):
    ref=tmp_path/'reference.docx'
    doc=Document()
    doc.styles['Normal'].font.name='Arial'
    doc.styles['Normal'].font.size=Pt(12)
    doc.add_paragraph('Reference content must never appear in output')
    doc.save(ref)
    profile=extract_standard(ref)
    box=BBox(x0=10,y0=20,x1=15,y1=30)
    line=Line(text='Original recognized content',bbox=box,font_family='Wrong',bold=True,runs=[])
    block=Block(id='b',type=BlockType.TEXT,order=0,bbox=box,text=line.text,lines=[line])
    result=apply_standard([block],profile)[0]
    assert result.text==block.text and result.bbox==block.bbox
    assert result.lines[0].text==line.text and result.lines[0].bbox==box
    assert result.lines[0].font_family=='Arial'
    assert result.lines[0].font_size_pt==12
    assert not result.lines[0].bold
    assert result.lines[0].horizontal_scale_pct==100
    assert result.lines[0].review_required
    assert 'Reference content' not in str(profile)
