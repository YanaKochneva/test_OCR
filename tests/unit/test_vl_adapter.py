from types import SimpleNamespace as NS

import pytest

from docpipe.engines.base import PageMeta
from docpipe.engines.paddleocr_vl_engine import PaddleOCRVLEngine, table_from_html
from docpipe.engines.ppstructure_engine import PPStructureEngine
from docpipe.errors import ModelError
from docpipe.ir import BBox


def test_html_table_preserves_spans_and_text():
    result = table_from_html('<table><tr><td rowspan="2">A</td><td>B</td></tr>'
                             '<tr><td>C</td></tr></table>', BBox(x0=0, y0=0, x1=100, y1=80))
    assert (result.n_rows, result.n_cols) == (2, 2)
    assert [c.text for c in result.cells] == ["A", "B", "C"]
    assert result.cells[0].rowspan == 2
    assert (result.cells[2].row, result.cells[2].col) == (1, 1)


def test_vl_maps_public_prediction_to_native_table():
    result = PaddleOCRVLEngine._to_blocks({"parsing_res_list": [{
        "block_bbox": [0, 0, 100, 80], "block_label": "table",
        "block_content": "<table><tr><td>Value</td></tr></table>"
    }]}, PageMeta(0, 100, 80, 0, 72), 100, 80)
    assert result[0].table.cells[0].text == "Value"
    assert result[0].text is None


def test_invalid_vlm_spans_are_rejected():
    with pytest.raises(ModelError):
        table_from_html('<table><tr><td rowspan="100">A</td></tr></table>',
                        BBox(x0=0, y0=0, x1=100, y1=80))


def test_paddle_public_result_wrapper_is_unwrapped():
    assert PPStructureEngine._result_json(NS(json={"res": {"parsing_res_list": []}})) == {"parsing_res_list": []}
