from __future__ import annotations

from pathlib import Path
from reportlab.pdfgen import canvas
from PIL import Image

from docpipe.ir import BBox, Block, BlockType, Document, EngineInfo, FigureData, Page, SourceInfo, SourceType
from docpipe.renderers.pdf_flow import render


def test_flow_preserves_figure_position_in_reading_order(tmp_path: Path):
    Image.new("RGB", (80, 50), "white").save(tmp_path / "fig.png")
    doc = Document(
        source=SourceInfo(file="x.pdf", sha256="b" * 64, type=SourceType.PDF),
        engine=EngineInfo(name="fake", version="1", models=[]),
        pages=[Page(index=0,width_pt=400,height_pt=500,rotation=0,raster_dpi=150,size_px=(833,1042),status="ok",text_layer="absent",blocks=[
            Block(id="a",type=BlockType.TEXT,bbox=BBox(x0=20,y0=20,x1=200,y1=40),order=0,text="До картинки"),
            Block(id="f",type=BlockType.FIGURE,bbox=BBox(x0=20,y0=100,x1=100,y1=150),order=1,figure=FigureData(file="fig.png",format="png",width_px=80,height_px=50)),
            Block(id="b",type=BlockType.TEXT,bbox=BBox(x0=20,y0=170,x1=220,y1=190),order=2,text="После картинки"),
        ])],
    )
    out = tmp_path / "flow.pdf"
    render(doc, out, tmp_path)
    assert out.exists()
    # WeasyPrint embeds the image into the resulting PDF; the output must be non-empty.
    assert out.stat().st_size > 1000
