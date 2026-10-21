"""Local PP-OCRv6 Medium RU recognizer and its author-recommended padding."""

import os
from pathlib import Path

from docpipe.errors import ModelError

MODEL_REPO = "vladlinv/PP-OCRv6_medium_rec_ru_onnx"
MODEL_REVISION = "dff15ea4a253e702786597894b1d36fa8bfed7b1"


def model_files():
    folder = Path(
        os.getenv(
            "DOCPIPE_RU_REC_MODEL_DIR",
            str(Path(__file__).resolve().parents[3] / "models" / "ppocrv6-medium-ru"),
        )
    )
    model, keys = folder / "model.onnx", folder / "rec_keys.txt"
    if not model.is_file() or not keys.is_file():
        raise ModelError(
            f"PP-OCRv6 Medium RU is not installed in {folder}. "
            "Run scripts/download_russian_recognition.py explicitly; no automatic download is performed."
        )
    return model, keys


def recognition_params():
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion

    model, keys = model_files()
    params = {
        "Rec.model_path": str(model),
        "Rec.rec_keys_path": str(keys),
        "EngineConfig.onnxruntime.intra_op_num_threads": int(os.getenv("DOCPIPE_OCR_THREADS", "4")),
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
        "Rec.ocr_version": OCRVersion.PPOCRV6,
        "Rec.model_type": ModelType.MEDIUM,
        "Rec.lang_type": LangRec.CYRILLIC,
        "Rec.rec_img_shape": [3, 48, 640],
    }
    model_root = os.getenv("DOCPIPE_RAPIDOCR_MODEL_DIR")
    if model_root:
        params["Global.model_root_dir"] = str(Path(model_root).resolve())
        for stage, filename in (
            ("Det", "ch_PP-OCRv5_det_mobile.onnx"),
            ("Cls", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
        ):
            path = Path(model_root) / filename
            if not path.is_file():
                raise ModelError(f"Local RapidOCR model not found: {path}")
            params[f"{stage}.model_path"] = str(path.resolve())
    return params


def configure_padding(reader):
    rec = reader.text_rec
    if getattr(rec, "_docpipe_ru_padding", False):
        return reader
    resize = rec.resize_norm_img
    rec.rec_image_shape = [3, 48, 48]

    def padded(image, ratio):
        return resize(image, max(640 / 48, 1.5 * ratio))

    rec.resize_norm_img = padded
    rec._docpipe_ru_padding = True
    return reader
