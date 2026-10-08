import pytest

from docpipe.engines.docling_engine import DoclingEngine


def test_docling_rapidocr_uses_selected_single_language():
    class Options:
        def __init__(self, *, lang, use_gpu):
            self.lang = lang
            self.use_gpu = use_gpu

    engine = DoclingEngine(languages=["eslav"])
    options = engine._make_rapidocr_options(Options)

    assert options.lang == ["eslav"]
    assert options.use_gpu is False


def test_docling_rejects_multiple_rapidocr_languages():
    with pytest.raises(ValueError, match="one recognition language"):
        DoclingEngine(languages=["eslav", "en"])
