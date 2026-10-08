from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image

from docpipe.ir import BBox, Block, BlockType, Document, EngineInfo, FigureData, Page, SourceInfo, SourceType
from docpipe.renderers.docx import render as render_docx
from docpipe.renderers.pdf_flow import render as render_flow
from docpipe.renderers.pdf_positional import render as render_positional
from docpipe.renderers.pdf_searchable import render as render_searchable


def make_source(tmp_path: Path) -> tuple[Path, Path]:
    image = tmp_path / "fig.png"
    Image.new("RGB", (100, 60), "white").save(image)
    source = tmp_path / "source.pdf"
    c = canvas.Canvas(str(source), pagesize=(600, 800))
    c.drawString(60, 730, "Привет мир")
    c.drawImage(ImageReader(str(image)), 60, 500, width=100, height=60)
    c.drawString(60, 460, "Подпись рисунка")
    c.showPage(); c.save()
    return source, image


def make_document() -> Document:
    return Document(
        source=SourceInfo(file="source.pdf", sha256="a" * 64, type=SourceType.PDF),
        engine=EngineInfo(name="fake", version="1", models=[]),
        pages=[Page(
            index=0, width_pt=600, height_pt=800, rotation=0, raster_dpi=300,
            size_px=(2500, 3334), status="ok", text_layer="absent",
            blocks=[
                Block(id="t1", type=BlockType.TEXT, bbox=BBox(x0=60,y0=50,x1=250,y1=75), order=0, text="Привет мир"),
                Block(id="f1", type=BlockType.FIGURE, bbox=BBox(x0=60,y0=240,x1=160,y1=300), order=1,
                      figure=FigureData(file="fig.png", format="png", width_px=100, height_px=60, caption_id="Подпись рисунка")),
                Block(id="c1", type=BlockType.CAPTION, bbox=BBox(x0=60,y0=330,x1=220,y1=350), order=2, text="Подпись рисунка"),
            ],
        )],
    )


def test_pdf_flow(tmp_path: Path):
    source, _ = make_source(tmp_path)
    out = tmp_path / "flow.pdf"
    render_flow(make_document(), out, tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_pdf_positional(tmp_path: Path):
    out = tmp_path / "positional.pdf"
    render_positional(make_document(), out, tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_docx(tmp_path: Path):
    make_source(tmp_path)
    out = tmp_path / "document.docx"
    render_docx(make_document(), out, tmp_path)
    assert out.exists() and out.stat().st_size > 0


def test_searchable_pdf_requires_pikepdf(tmp_path: Path):
    if __import__("importlib.util").util.find_spec("pikepdf") is None:
        pytest.skip("pikepdf отсутствует: реальный searchable-PDF overlay не запускается")
    source, _ = make_source(tmp_path)
    out = tmp_path / "searchable.pdf"
    render_searchable(make_document(), source, out)
    assert out.exists() and out.stat().st_size > 0
