import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docpipe.ir import BBox, Block, BlockType, Document, EngineInfo, FigureData, Page, PageStatus, SourceInfo, SourceType, TextLayerStatus


def test_ir_json_is_deterministic():
    page = Page(index=0, width_pt=100, height_pt=100, raster_dpi=300, size_px=(1000, 1000), status=PageStatus.OK, text_layer=TextLayerStatus.ABSENT)
    doc = Document(source=SourceInfo(file="x.pdf", sha256="a" * 64, type=SourceType.PDF), engine=EngineInfo(name="fake", version="0"), pages=[page])
    assert doc.model_dump_json() == doc.model_dump_json()


def test_figure_requires_figure_payload_and_no_text():
    with pytest.raises(ValidationError):
        Block(id="f1", type=BlockType.FIGURE, bbox=BBox(x0=0, y0=0, x1=10, y1=10), order=0)
    with pytest.raises(ValidationError):
        Block(id="f1", type=BlockType.FIGURE, bbox=BBox(x0=0, y0=0, x1=10, y1=10), order=0, text="x", figure=FigureData(file="x.png", format="png", width_px=10, height_px=10))


def test_page_rejects_duplicate_order():
    kwargs = dict(bbox=BBox(x0=0, y0=0, x1=10, y1=10), order=0)
    with pytest.raises(ValidationError):
        Page(index=0, width_pt=100, height_pt=100, raster_dpi=300, size_px=(1000, 1000), status=PageStatus.OK, text_layer=TextLayerStatus.ABSENT, blocks=[Block(id="a", type=BlockType.TEXT, text="a", **kwargs), Block(id="b", type=BlockType.TEXT, text="b", **kwargs)])


def test_schema_exists():
    path = Path(__file__).parents[2] / "docs" / "ir.schema.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["title"] == "Document"
