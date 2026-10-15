from PIL import Image, ImageDraw
from docpipe.engines.base import PageMeta
from docpipe.ir import BBox, Block, BlockType, TableCell, TableData, Line
from docpipe.stages.graphic_fields import retain_graphic_fields


def test_blank_rule_preserved_without_grid_or_text():
    image = Image.new('RGB', (600, 800), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 580, 200), outline='black', width=2)
    draw.line((140, 100, 340, 100), fill='black', width=2)
    draw.text((40, 45), 'file_name.dat', fill='black')
    box = BBox(x0=20,y0=20,x1=580,y1=200)
    table = Block(id='table',type=BlockType.TABLE,bbox=box,order=0,
        table=TableData(n_rows=1,n_cols=1,cells=[TableCell(row=0,col=0,text='file_name.dat',bbox=box)]))
    result = retain_graphic_fields([table],image,PageMeta(0,600,800,0,72))
    assert result[0].table.cells[0].text == table.table.cells[0].text
    assert len(result)==2
    assert result[1].source_label=='graphic_field'
    assert result[1].bbox.y0==99
    assert result[1].bbox.y1==103
    assert result[1].bbox.x1-result[1].bbox.x0 < 210


def test_render_segments_leave_blank_field_clear_without_changing_text():
    image=Image.new('RGB',(600,800),'white')
    ImageDraw.Draw(image).line((180,100,300,100),fill='black',width=2)
    box=BBox(x0=20,y0=20,x1=580,y1=200)
    line=Line(text='Name value tail',bbox=BBox(x0=100,y0=90,x1=500,y1=103))
    table=Block(id='table',type=BlockType.TABLE,bbox=box,order=0,
        table=TableData(n_rows=1,n_cols=1,cells=[TableCell(row=0,col=0,text=line.text,bbox=box,lines=[line])]))
    result=retain_graphic_fields([table],image,PageMeta(0,600,800,0,72))
    actual=result[0].table.cells[0]
    assert actual.text==line.text and actual.lines[0].text==line.text
    assert ' '.join(run.text for run in actual.lines[0].runs)==line.text
    assert actual.lines[0].runs[0].bbox.x1 < 180
    assert actual.lines[0].runs[-1].bbox.x0 > 300
