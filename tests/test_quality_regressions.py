import pytest
from docpipe.evaluation import compare_text
from docpipe.ir import BBox, Block, BlockType
from docpipe.stages.text_duplicates import remove_text_duplicates
from docpipe.stages.text_refinement import refine_text_blocks
from docpipe.ir import Document, SourceInfo, SourceType, EngineInfo, Page, TextLayerStatus


def block(name, text, box):
    return Block(id=name, type=BlockType.TEXT, text=text,
                 bbox=BBox(x0=box[0], y0=box[1], x1=box[2], y1=box[3]), order=0)


def test_scoped_score_keeps_standard_errors_and_exclusion_count():
    result = compare_text('alpha beta', 'alpha beta extra')
    assert result['cer'] > 0
    assert result['reference_scoped_cer'] == 0
    assert result['excluded_inserted_chars'] == 6
    assert result['excluded_inserted_words'] == 1
    assert result['reference_scoped_wer'] == 0
    assert not result['document_exact_match']


def test_scoped_score_does_not_exclude_missing_content():
    result = compare_text('alpha beta gamma', 'alpha beta')
    assert result['reference_scoped_cer'] > 0
    assert result['reference_scoped_wer'] > 0


def test_docling_does_not_flip_cyrillic_lines_by_default(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr('docpipe.engines.russian_recognition.model_files',lambda:(Path('model.onnx'),Path('rec_keys.txt')))
    monkeypatch.setattr('docpipe.engines.russian_recognition.recognition_params',lambda:{'Rec.rec_img_shape':[3,48,640]})
    from docpipe.engines.docling_engine import DoclingEngine
    class Options:
        def __init__(self, *, lang, use_cls, rec_model_path, rec_keys_path, rapidocr_params):
            self.use_cls=use_cls
    monkeypatch.delenv('DOCPIPE_OCR_LINE_CLASSIFICATION',raising=False)
    assert DoclingEngine()._make_rapidocr_options(Options).use_cls is False
    monkeypatch.setenv('DOCPIPE_OCR_LINE_CLASSIFICATION','1')
    assert DoclingEngine()._make_rapidocr_options(Options).use_cls is True


def test_overlapping_assembly_duplicate_removed_but_other_page_text_retained():
    blocks = [block('assembled', 'DirectorAlice', (10, 100, 200, 120)),
              block('left', 'Director', (10, 103, 70, 120)),
              block('right', 'Alice', (140, 103, 200, 120)),
              block('repeated', 'DirectorAlice', (10, 300, 200, 320))]
    assert [b.id for b in remove_text_duplicates(blocks)] == ['left', 'right', 'repeated']


def test_incomplete_duplicate_does_not_remove_original():
    blocks = [block('full', 'Director Alice Bob', (10, 100, 200, 120)),
              block('left', 'Director', (10, 103, 70, 120)),
              block('right', 'Alice', (140, 103, 200, 120))]
    assert len(remove_text_duplicates(blocks)) == 3


def test_complete_geometric_reread_corrects_garbage_without_moving_box():
    original = block('text', 'one two three four five six xx yy zz', (10, 100, 300, 125))
    lines = [('one two three four five six', BBox(x0=10, y0=101, x1=290, y1=112), .99),
             ('seven eight nine', BBox(x0=10, y0=115, x1=200, y1=124), .99)]
    result = refine_text_blocks([original], lines, 800)[0]
    assert result.text == 'one two three four five six seven eight nine'
    assert result.bbox == original.bbox
    assert len(result.lines) == 2


def test_neighboring_column_does_not_replace_text():
    original = block('text', 'one two three four five six xx yy zz', (10, 100, 300, 125))
    lines = [('one two three four five six seven eight nine',
              BBox(x0=10, y0=101, x1=600, y1=124), .99)]
    assert refine_text_blocks([original], lines, 800)[0].text == original.text


def document_with(blocks):
    blocks=[b.model_copy(update={'order':i}) for i,b in enumerate(blocks)]
    return Document(source=SourceInfo(file='missing-scan.pdf', sha256='a'*64, type=SourceType.PDF),
                    engine=EngineInfo(name='fake',version='test'),
                    pages=[Page(index=0,width_pt=300,height_pt=800,raster_dpi=72,status='ok',
                                size_px=(300,800),text_layer=TextLayerStatus.ABSENT,blocks=blocks)])


def test_compatibility_pdf_never_reads_or_embeds_source_page(tmp_path):
    import pikepdf
    from docpipe.renderers.pdf_searchable import render
    doc = document_with([block('visible','Readable text',(10,100,200,130))])
    path = render(doc,tmp_path/'nonexistent-source.pdf',tmp_path/'result.pdf')
    with pikepdf.Pdf.open(path) as pdf:
        assert not pdf.pages[0].obj.Resources.get('/XObject')
        assert all(not (str(instruction.operator)=='Tr' and int(instruction.operands[0])==3)
                   for instruction in pikepdf.parse_content_stream(pdf.pages[0]))


def test_docx_objects_stay_at_ir_coordinates(tmp_path):
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from docpipe.renderers.docx import render
    doc = document_with([block('long','A long text '*30,(10,100,200,130)),
                         block('next','Next block',(10,135,200,150))])
    path = render(doc,tmp_path/'result.docx')
    ns={'wp':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'}
    with ZipFile(path) as archive:
        root=ET.fromstring(archive.read('word/document.xml'))
    anchors=root.findall('.//wp:anchor',ns)
    from docpipe.renderers.page_size import A4_HEIGHT
    actual=[int(a.find('wp:positionV/wp:posOffset',ns).text)/12700 for a in anchors]
    assert actual==pytest.approx([100*A4_HEIGHT/800,135*A4_HEIGHT/800],abs=.001)


def test_visual_line_partition_preserves_recognized_words():
    from docpipe.stages.table_visual_layout import split_at_visual_lines
    text='alpha beta gamma delta epsilon zeta'
    lines=split_at_visual_lines(text,[100,100,100])
    assert len(lines)==3
    assert ' '.join(lines)==text
    assert split_at_visual_lines('one',[10,10]) is None


def test_pixel_geometry_restores_cell_fill_without_rewriting_text():
    from PIL import Image,ImageDraw,ImageFont
    from docpipe.engines.base import PageMeta
    from docpipe.ir import TableCell,TableData
    from docpipe.stages.table_visual_layout import restore_table_visual_layout
    from docpipe.renderers.fonts import require_font
    image=Image.new('RGB',(400,200),'white')
    draw=ImageDraw.Draw(image)
    draw.rectangle((1,1,398,99),fill='#ffcc00')
    for x in (1,200,398):draw.line((x,1,x,198),fill='black',width=2)
    for y in (1,100,198):draw.line((1,y,398,y),fill='black',width=2)
    font=ImageFont.truetype(str(require_font()),20)
    texts=['alpha beta','value','gamma delta','other']
    cells=[]
    for index,text in enumerate(texts):
        row,col=divmod(index,2)
        draw.text((col*200+10,row*100+30),text,fill='black',font=font)
        cells.append(TableCell(row=row,col=col,text=text,
            bbox=BBox(x0=col*200,y0=row*100,x1=(col+1)*200,y1=(row+1)*100)))
    original=Block(id='table',type=BlockType.TABLE,order=0,bbox=BBox(x0=0,y0=0,x1=400,y1=200),
                   table=TableData(n_rows=2,n_cols=2,cells=cells))
    result=restore_table_visual_layout([original],image,PageMeta(0,400,200,0,72))[0]
    assert [c.text for c in result.table.cells]==texts
    assert result.table.cells[0].background_color=='#ffcc00'
    assert result.table.cells[2].background_color is None
    assert result.table.cells[0].lines
