import pytest

from docpipe.engines.docling_engine import DoclingEngine


def test_docling_rapidocr_uses_selected_single_language(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr('docpipe.engines.russian_recognition.model_files',lambda:(Path('model.onnx'),Path('rec_keys.txt')))
    monkeypatch.setattr('docpipe.engines.russian_recognition.recognition_params',lambda:{'Rec.rec_img_shape':[3,48,640]})
    class Options:
        def __init__(self, *, lang, use_gpu, rec_model_path, rec_keys_path, rapidocr_params):
            self.lang = lang
            self.use_gpu = use_gpu

    engine = DoclingEngine(languages=["eslav"])
    options = engine._make_rapidocr_options(Options)

    assert options.lang == ["eslav"]
    assert options.use_gpu is False


def test_docling_rejects_multiple_rapidocr_languages():
    with pytest.raises(ValueError, match="one recognition language"):
        DoclingEngine(languages=["eslav", "en"])
