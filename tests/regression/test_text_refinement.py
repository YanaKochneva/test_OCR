from docpipe.ir import BBox,Block,BlockType
from docpipe.stages.text_refinement import refine_text_blocks
from docpipe.stages.text_refinement import preserves_existing_words


def test_longer_candidate_cannot_delete_existing_words():
    assert not preserves_existing_words('one two three four','one three four additional much longer words')
    assert preserves_existing_words('one two four','one two three four')
    assert preserves_existing_words('one typo four','one correct four')


def test_recovers_missing_line_and_word_without_duplicate():
    block=Block(id='p',type=BlockType.TEXT,order=0,bbox=BBox(x0=10,y0=40,x1=300,y1=60),text='указания основой при выполнении')
    lines=[('Целью настоящих указаний',BBox(x0=10,y0=15,x1=300,y1=30),.98),
           ('указания являются основой при выполнении',block.bbox,.98)]
    result=refine_text_blocks([block],lines,800)
    assert len(result)==2
    assert result[0].text=='Целью настоящих указаний'
    assert result[1].text=='указания являются основой при выполнении'


def test_unrelated_candidate_does_not_replace_text():
    box=BBox(x0=10,y0=40,x1=300,y1=60)
    block=Block(id='p',type=BlockType.TEXT,order=0,bbox=box,text='исходный текст документа')
    result=refine_text_blocks([block],[('совершенно иное содержимое',box,.99)],800)
    assert result[0].text==block.text


def test_low_confidence_candidate_does_not_replace_text():
    box=BBox(x0=10,y0=40,x1=300,y1=60)
    block=Block(id='p',type=BlockType.TEXT,order=0,bbox=box,text='исходный текст')
    result=refine_text_blocks([block],[('исходный текст добавление',box,.5)],800)
    assert result[0].text==block.text


def test_list_item_can_recover_missing_words():
    box = BBox(x0=10, y0=40, x1=300, y1=60)
    block = Block(id='li', type=BlockType.LIST_ITEM, order=0, bbox=box, text='требования к данным')
    result = refine_text_blocks([block], [('требования к исходным данным', box, .99)], 800)
    assert result[0].text == 'требования к исходным данным'
    assert result[0].type == BlockType.LIST_ITEM


def test_line_across_columns_cannot_replace_one_column():
    box = BBox(x0=10, y0=40, x1=150, y1=60)
    block = Block(id='p', type=BlockType.TEXT, order=0, bbox=box, text='исходный текст')
    wide = BBox(x0=10, y0=40, x1=270, y1=60)
    result = refine_text_blocks([block], [('исходный текст соседней колонки', wide, .99)], 800)
    assert result[0].text == block.text


def test_table_refinement_preserves_geometry_and_spans():
    from PIL import Image
    from docpipe.engines.docling_engine import DoclingEngine
    from docpipe.engines.base import PageMeta
    from docpipe.ir import TableCell,TableData
    box=BBox(x0=0,y0=0,x1=200,y1=100)
    old='Исходный текст данных модели MineralResources'
    cell=TableCell(row=0,col=0,rowspan=2,text=old,bbox=box)
    block=Block(id='t',type=BlockType.TABLE,order=0,bbox=box,table=TableData(n_rows=2,n_cols=1,cells=[cell]))
    engine=DoclingEngine()
    engine._projected_lines=lambda image,meta:[(old+'.',box,.99)]
    result=engine._refine_table_cells([block],Image.new('RGB',(200,100)),PageMeta(0,200,100,0,72))
    actual=result[0].table.cells[0]
    assert actual.text==old+'.'
    assert actual.bbox==cell.bbox
    assert actual.rowspan==2


def test_empty_cell_is_reread_but_blank_cell_stays_blank():
    from PIL import Image
    from docpipe.engines.docling_engine import DoclingEngine
    from docpipe.engines.base import PageMeta
    from docpipe.ir import TableCell,TableData
    box=BBox(x0=0,y0=0,x1=200,y1=100)
    block=Block(id='t',type=BlockType.TABLE,order=0,bbox=box,table=TableData(n_rows=1,n_cols=1,cells=[TableCell(row=0,col=0,text='',bbox=box)]))
    engine=DoclingEngine()
    engine._projected_lines=lambda image,meta:[('Recovered cell',box,.99)]
    args=([block],Image.new('RGB',(200,100)),PageMeta(0,200,100,0,72))
    assert engine._refine_table_cells(*args)[0].table.cells[0].text=='Recovered cell'
    engine._projected_lines=lambda image,meta:[]
    assert engine._refine_table_cells(*args)[0].table.cells[0].text==''


def test_cell_mode_is_reset_after_reader_failure():
    import pytest
    from PIL import Image
    from docpipe.engines.docling_engine import DoclingEngine
    from docpipe.engines.base import PageMeta
    from docpipe.ir import TableCell,TableData
    box=BBox(x0=0,y0=0,x1=200,y1=100)
    block=Block(id='t',type=BlockType.TABLE,order=0,bbox=box,table=TableData(n_rows=1,n_cols=1,cells=[TableCell(row=0,col=0,text='',bbox=box)]))
    engine=DoclingEngine()
    def fail(*args):raise RuntimeError('reader failed')
    engine._projected_lines=fail
    with pytest.raises(RuntimeError):engine._refine_table_cells([block],Image.new('RGB',(200,100)),PageMeta(0,200,100,0,72))
    assert not engine._inside_table_cell
