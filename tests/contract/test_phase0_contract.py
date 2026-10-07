from docpipe.ir import Block, BlockType, BBox, Page, PageStatus, TextLayerStatus


def test_synthetic_page_contract():
    block = Block(id="b1", type=BlockType.TEXT, bbox=BBox(x0=1, y0=2, x1=50, y1=20), order=0, text="Привет")
    page = Page(index=0, width_pt=100, height_pt=100, rotation=0, raster_dpi=300, size_px=(1000, 1000), status=PageStatus.OK, text_layer=TextLayerStatus.ABSENT, blocks=[block])
    assert page.blocks[0].bbox.x0 >= 0
    assert page.blocks[0].bbox.y1 <= page.height_pt
    assert len({b.order for b in page.blocks}) == len(page.blocks)
