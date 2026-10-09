from types import SimpleNamespace as NS

from docpipe.engines.base import PageMeta
from docpipe.engines.docling_engine import DoclingEngine
from docpipe.ir import BBox, Block, BlockType
from docpipe.stages.ocr_recovery import recover_unassigned_lines


def box(x=10, y=100):
    return BBox(x0=x, y0=y, x1=x + 100, y1=y + 20)


def test_recovery_suppresses_existing_text_and_figure_and_low_confidence():
    blocks = [Block(id="text", type=BlockType.TEXT, bbox=box(), order=0, text="existing")]
    lines = [("existing", box(), .99), ("new line", box(y=150), .99),
             ("noise", box(y=200), .1)]
    result = recover_unassigned_lines(blocks, lines, 800)
    assert [b.text for b in result] == ["existing", "new line"]
    assert result[1].source_label == "unassigned_ocr_line"


def cell(text, row=0, col=0, span=1, bbox=True):
    return NS(text=text, start_row_offset_idx=row, start_col_offset_idx=col,
              row_span=1, col_span=span,
              bbox=NS(l=10, t=100, r=110, b=120) if bbox else None)


def test_document_index_is_kept_as_ordered_text():
    item = NS(self_ref="index", text="", data=NS(table_cells=[
        cell("Second", row=1), cell("First", row=0), cell("3", row=0, col=1)]))
    result = DoclingEngine()._item_to_block(item, "document_index", box(), PageMeta(0, 600, 800, 0, 72))
    assert result.text == "First 3\nSecond"
    assert result.source_label == "document_index"
    assert result.type == BlockType.TEXT


def test_merged_grid_cells_are_not_duplicated():
    merged = cell("merged", span=2)
    item = NS(self_ref="table", text="", data=NS(grid=[[merged, merged]], num_rows=1, num_cols=2))
    result = DoclingEngine()._item_to_block(item, "table", box(), PageMeta(0, 600, 800, 0, 72))
    assert len(result.table.cells) == 1
    assert result.table.cells[0].colspan == 2


def test_cell_text_without_bbox_is_preserved():
    item = NS(self_ref="table", text="", data=NS(
        table_cells=[cell("important", bbox=False)], num_rows=1, num_cols=1))
    result = DoclingEngine()._item_to_block(item, "table", box(), PageMeta(0, 600, 800, 0, 72))
    assert result.table.cells[0].text == "important"
    assert result.table.cells[0].bbox == box()
