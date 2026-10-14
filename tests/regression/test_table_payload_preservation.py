from docpipe.ir import BBox,Block,BlockType,TableCell,TableData
from docpipe.renderers.table_layout import build_table_layout


def test_populated_cell_under_merged_placeholder_is_not_lost():
    box=BBox(x0=0,y0=0,x1=200,y1=100)
    block=Block(id='table',type=BlockType.TABLE,order=0,bbox=box,
        table=TableData(n_rows=2,n_cols=1,cells=[
            TableCell(row=0,col=0,rowspan=2,text='',bbox=box),
            TableCell(row=1,col=0,text='123.45',bbox=box)]))
    layout=build_table_layout(block)
    assert layout.cells[0].text=='123.45'


def test_distinct_values_in_overlapping_cells_are_preserved():
    box=BBox(x0=0,y0=0,x1=200,y1=100)
    block=Block(id='table',type=BlockType.TABLE,order=0,bbox=box,
        table=TableData(n_rows=1,n_cols=1,cells=[
            TableCell(row=0,col=0,text='First value',bbox=box),
            TableCell(row=0,col=0,text='Second value',bbox=box)]))
    assert build_table_layout(block).cells[0].text=='First value\nSecond value'
