from docpipe.ir import Document, EngineInfo, SourceInfo, SourceType


def test_document_roundtrip():
    doc = Document(source=SourceInfo(file="input.png", sha256="0" * 64, type=SourceType.IMAGE), engine=EngineInfo(name="fake", version="0"))
    restored = Document.model_validate_json(doc.model_dump_json())
    assert restored == doc
