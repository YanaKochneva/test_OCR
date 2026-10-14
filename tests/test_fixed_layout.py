from docpipe.ir import BBox, Line, BlockType
from docpipe.renderers.fixed_layout import stable_document
from test_quality_regressions import block,document_with


def test_fixed_export_ignores_optical_font_guesses_without_changing_ir():
    box=BBox(x0=10,y0=100,x1=120,y1=113)
    line=Line(text='Original text',bbox=box,font_family='Comic Sans MS',
              font_size_pt=160,bold=True,italic=True,horizontal_scale_pct=350,
              font_match_error=.8,review_required=True,review_reason='Weak glyph fit')
    source=document_with([block('b',line.text,(10,100,120,113)).model_copy(update={'lines':[line]})])
    fixed=stable_document(source)
    actual=fixed.pages[0].blocks[0].lines[0]
    assert actual.text==line.text and actual.bbox==box
    assert actual.font_family=='Arial' and actual.font_size_pt==11
    assert actual.horizontal_scale_pct==100 and not actual.bold and not actual.italic
    assert not actual.review_required
    assert source.pages[0].blocks[0].lines[0].font_size_pt==160


def test_contents_uses_canonical_entries_not_broken_visual_split():
    heading=block('heading','Содержание',(10,50,100,65)).model_copy(update={'type':BlockType.HEADING})
    boxes=[BBox(x0=10,y0=100+i*14,x1=300,y1=111+i*14) for i in range(2)]
    entry=block('toc','1. First 3\n2. Second 4',(10,100,300,125)).model_copy(update={
        'lines':[Line(text='Wrong split',bbox=box) for box in boxes]})
    original=document_with([heading,entry])
    fixed=stable_document(original)
    rows=fixed.pages[0].blocks[1].lines
    assert rows[0].text.startswith('1. First ') and rows[0].text.endswith('3')
    assert rows[1].text.startswith('2. Second ') and rows[1].text.endswith('4')
    assert '..' in rows[0].text and [line.bbox for line in rows]==boxes
    assert original.pages[0].blocks[1].lines[0].text=='Wrong split'
