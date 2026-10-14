import numpy as np
import pytest
from docpipe.engines.russian_recognition import configure_padding, model_files
from docpipe.errors import ModelError


def test_author_padding_for_short_and_long_lines():
    class Rec:
        rec_image_shape=[3,48,640]
        def resize_norm_img(self,image,ratio):
            return ratio
    class Reader:
        text_rec=Rec()
    reader=configure_padding(Reader())
    image=np.zeros((48,100,3),dtype=np.uint8)
    assert reader.text_rec.resize_norm_img(image,2)==640/48
    assert reader.text_rec.resize_norm_img(image,20)==30
    configure_padding(reader)
    assert reader.text_rec.resize_norm_img(image,20)==30


def test_missing_model_never_falls_back(monkeypatch,tmp_path):
    monkeypatch.setenv('DOCPIPE_RU_REC_MODEL_DIR',str(tmp_path))
    with pytest.raises(ModelError,match='no automatic download'):
        model_files()
