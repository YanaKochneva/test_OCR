import json

from docx import Document as WordDocument
from docpipe.evaluation import compare_docx_reference


def test_reference_comparison_reports_multiset_overlap(tmp_path):
    reference = tmp_path / "reference.docx"
    word = WordDocument()
    word.add_paragraph("Hello world")
    word.save(reference)
    ir = tmp_path / "document.json"
    ir.write_text(json.dumps({"pages": [{"blocks": [{"text": "hello world"}]}]}), encoding="utf-8")

    report = compare_docx_reference(reference, ir)

    assert report["matched_tokens"] == 2
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["f1"] == 1.0
    assert report["cer"] == 0.0
    assert report["wer"] == 0.0


def test_reference_comparison_preserves_paragraph_table_order(tmp_path):
    reference = tmp_path / "ordered.docx"
    word = WordDocument()
    word.add_paragraph("First paragraph")
    word.add_table(rows=1, cols=1).cell(0, 0).text = "Middle table"
    word.add_paragraph("Last paragraph")
    word.save(reference)

    ir = tmp_path / "ordered.json"
    ir.write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "blocks": [
                            {"text": "First paragraph"},
                            {"text": None, "table": {"cells": [{"text": "Middle table"}]}},
                            {"text": "Last paragraph"},
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    report = compare_docx_reference(reference, ir)
    assert report["cer"] == 0.0
    assert report["wer"] == 0.0
