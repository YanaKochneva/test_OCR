from docpipe.verify.checks import verify_document, words
from docpipe.ir import Document, EngineInfo, Page, PageStatus, SourceInfo, SourceType, TextLayerStatus


def test_words_normalizes_cyrillic_yo():
    assert words("Ёлка ёжик") == ["елка", "ежик"]


def test_verify_rejects_directory_without_document(tmp_path):
    report = verify_document(tmp_path)

    assert report["ok"] is False
    assert report["result"] == "FAIL"
    assert report["checks"][0]["check"] == "ir_valid"


def test_verify_rejects_empty_real_ocr_result(tmp_path):
    doc = Document(
        source=SourceInfo(file="scan.pdf", sha256="a" * 64, type=SourceType.PDF),
        engine=EngineInfo(name="docling", version="2.134.0"),
        pages=[
            Page(
                index=0,
                width_pt=100,
                height_pt=100,
                raster_dpi=300,
                size_px=(417, 417),
                status=PageStatus.OK,
                text_layer=TextLayerStatus.ABSENT,
            )
        ],
    )
    ir_path = tmp_path / "document.json"
    ir_path.write_text(doc.model_dump_json(), encoding="utf-8")

    report = verify_document(ir_path)
    ocr_check = next(item for item in report["checks"] if item["check"] == "ocr_text")
    assert ocr_check["status"] == "FAIL"
    assert report["result"] == "FAIL"
