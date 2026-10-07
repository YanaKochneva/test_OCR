from __future__ import annotations

import sys
import types

import numpy as np
from PIL import Image

from docpipe.engines.base import PageMeta
from docpipe.engines.ppstructure_engine import PPStructureEngine
from docpipe.ir import BlockType


def _install_fake_paddleocr(monkeypatch):
    class FakeResult:
        @property
        def json(self):
            return {
                "parsing_res_list": [
                    {"block_bbox": [20, 20, 220, 80], "block_label": "text", "block_content": "Привет мир"},
                    {"block_bbox": [20, 100, 380, 260], "block_label": "figure", "block_content": ""},
                    {"block_bbox": [20, 280, 380, 420], "block_label": "table", "block_content": ""},
                    {"block_bbox": [20, 440, 380, 500], "block_label": "figure_caption", "block_content": "Рисунок 1"},
                ],
                "table_res_list": [{
                    "cell_box_list": [
                        np.array([30, 290, 190, 340]), np.array([200, 290, 370, 340]),
                        np.array([30, 350, 190, 410]), np.array([200, 350, 370, 410]),
                    ],
                    "pred_html": "<table><tr><td>A</td><td>B</td></tr></table>",
                    "table_ocr_pred": {"rec_texts": ["A", "B", "C", "D"]},
                }],
                "formula_res_list": [],
                "overall_ocr_res": {},
            }

    class FakePipeline:
        def __init__(self, lang=None, device="cpu", use_doc_orientation_classify=False,
                     use_doc_unwarping=False, use_textline_orientation=False,
                     use_table_recognition=True, use_formula_recognition=True,
                     text_recognition_model_name=None):
            self.kwargs = {
                "lang": lang, "device": device,
                "use_doc_orientation_classify": use_doc_orientation_classify,
                "use_doc_unwarping": use_doc_unwarping,
                "use_textline_orientation": use_textline_orientation,
                "use_table_recognition": use_table_recognition,
                "use_formula_recognition": use_formula_recognition,
                "text_recognition_model_name": text_recognition_model_name,
            }

        def predict(self, image):
            assert isinstance(image, np.ndarray)
            return iter([FakeResult()])

    fake = types.ModuleType("paddleocr")
    fake.__version__ = "3.7.0"
    fake.PPStructureV3 = FakePipeline
    monkeypatch.setitem(sys.modules, "paddleocr", fake)
    return FakePipeline


def test_ppstructure_public_contract_and_ir(monkeypatch):
    pipeline = _install_fake_paddleocr(monkeypatch)
    engine = PPStructureEngine(lang="ru", gpu=False)
    engine.load()
    assert isinstance(engine._pipeline, pipeline)
    blocks = engine.analyze_page(
        Image.new("RGB", (400, 600)),
        PageMeta(index=0, width_pt=400, height_pt=600, rotation=0, dpi=72),
    )
    assert [b.type for b in blocks[:4]] == [
        BlockType.TEXT, BlockType.FIGURE, BlockType.TABLE, BlockType.CAPTION
    ]
    assert [b.order for b in blocks] == list(range(len(blocks)))
    assert all(0 <= b.bbox.x0 <= b.bbox.x1 <= 400 for b in blocks)
    assert all(0 <= b.bbox.y0 <= b.bbox.y1 <= 600 for b in blocks)
    assert blocks[2].table is not None
    assert blocks[2].table.html is not None
    assert pipeline


def test_ppstructure_defaults_are_russian_and_eslav_model_is_explicit(monkeypatch):
    pipeline = _install_fake_paddleocr(monkeypatch)
    engine = PPStructureEngine()
    engine.load()
    assert pipeline
    assert engine.lang == "ru"
    assert engine._pipeline.kwargs["lang"] == "ru"
    assert engine._pipeline.kwargs["text_recognition_model_name"] == "eslav_PP-OCRv5_mobile_rec"
    assert engine._pipeline.kwargs["device"] == "cpu"
