"""Local PP-OCRv6 Medium RU recognizer and its author-recommended padding."""
import os
from pathlib import Path

from docpipe.errors import ModelError

MODEL_REPO = 'vladlinv/PP-OCRv6_medium_rec_ru_onnx'
MODEL_REVISION = 'dff15ea4a253e702786597894b1d36fa8bfed7b1'


def model_files():
    folder = Path(os.getenv('DOCPIPE_RU_REC_MODEL_DIR', str(Path(__file__).resolve().parents[3]/'models'/'ppocrv6-medium-ru')))
    model, keys = folder/'model.onnx', folder/'rec_keys.txt'
    if not model.is_file() or not keys.is_file():
        raise ModelError(f'PP-OCRv6 Medium RU is not installed in {folder}. '
                         'Run scripts/download_russian_recognition.py explicitly; no automatic download is performed.')
    return model, keys


def recognition_params():
    from rapidocr.utils.typings import OCRVersion, ModelType, LangRec
    model, keys = model_files()
    return {'Rec.model_path':str(model), 'Rec.rec_keys_path':str(keys),
            'EngineConfig.onnxruntime.intra_op_num_threads':int(os.getenv('DOCPIPE_OCR_THREADS','4')),
            'EngineConfig.onnxruntime.inter_op_num_threads':1,
            'Rec.ocr_version':OCRVersion.PPOCRV6, 'Rec.model_type':ModelType.MEDIUM,
            'Rec.lang_type':LangRec.CYRILLIC, 'Rec.rec_img_shape':[3,48,640]}


def configure_padding(reader):
    rec = reader.text_rec
    if getattr(rec, '_docpipe_ru_padding', False):
        return reader
    resize = rec.resize_norm_img
    rec.rec_image_shape = [3,48,48]
    def padded(image, ratio):
        return resize(image, max(640/48, 1.5*ratio))
    rec.resize_norm_img = padded
    rec._docpipe_ru_padding = True
    return reader
