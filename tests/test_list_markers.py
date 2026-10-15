from PIL import Image, ImageDraw
from docpipe.engines.base import PageMeta
from docpipe.ir import BBox, Block, BlockType, Line
from docpipe.stages.list_markers import restore_list_markers


def test_only_visible_dash_is_restored():
    image=Image.new('RGB',(600,800),'white');draw=ImageDraw.Draw(image)
    draw.rectangle((104,107,108,108),fill='black')
    draw.rectangle((113,102,119,113),fill='black')
    box=BBox(x0=100,y0=100,x1=400,y1=115)
    item=Block(id='list',type=BlockType.LIST_ITEM,bbox=box,order=0,
               text='Example',lines=[Line(text='Example',bbox=box)])
    meta=PageMeta(0,600,800,0,72)
    actual=restore_list_markers([item],image,meta)[0]
    assert actual.text=='- Example'
    assert actual.lines[0].text=='- Example'
    assert item.text=='Example'
    assert restore_list_markers([actual],image,meta)[0].text=='- Example'
    assert restore_list_markers([item],Image.new('RGB',(600,800),'white'),meta)[0].text=='Example'
