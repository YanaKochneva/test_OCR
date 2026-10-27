"""Direct OCR for image uploads, without document-layout suppression."""
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from docpipe.engines.base import LayoutOcrEngine
from docpipe.ir import BBox, Block


class PhotoOcrEngine(LayoutOcrEngine):
    name = "rapidocr-photo"

    def load(self):
        import rapidocr
        from rapidocr import RapidOCR
        from docpipe.engines.russian_recognition import configure_padding, recognition_params

        params = recognition_params()
        # These artifacts are bundled by the production Docker build.
        model_root = Path(rapidocr.__file__).parent / "models"
        for stage, filename in (("Det", "ch_PP-OCRv5_det_mobile.onnx"),
                                ("Cls", "ch_ppocr_mobile_v2.0_cls_mobile.onnx")):
            key = f"{stage}.model_path"
            if key not in params:
                path = model_root / filename
                if not path.is_file():
                    from docpipe.errors import ModelError
                    raise ModelError(f"Local photo OCR model not found: {path}")
                params[key] = str(path)
        self.reader = configure_padding(RapidOCR(params=params))
        self.version = version("rapidocr")
        self.warnings = []
        self.refinement_incomplete = False
        self._result = None

    @staticmethod
    def _score(result):
        if result.boxes is None:
            return 0.0
        return sum(len(text.strip()) * float(score) ** 2 *
                   (0.15 if float(box[:, 1].max() - box[:, 1].min()) >
                    float(box[:, 0].max() - box[:, 0].min()) * 1.2 else 1.0)
                   for text, score, box in zip(result.txts or (), result.scores or (), result.boxes))

    def prepare_raster(self, raster):
        # Sparse labels cannot satisfy the document orientation gate.
        self._result = None
        trials = []
        for angle in (0, 90, 180, 270):
            image = raster.image.rotate(angle, expand=True)
            try:
                result = self.reader(image, use_cls=False)
                trials.append((self._score(result), angle, result))
            finally:
                image.close()
        best = max(trials, key=lambda item: item[0])
        # Cyrillic handwriting may score similarly upside down. Do not invert
        # an already horizontal photo for a small confidence improvement.
        if best[1] == 180 and best[0] < trials[0][0] * 1.7:
            best = trials[0]
        _, angle, self._result = best
        if not angle:
            return raster
        return replace(raster, image=raster.image.rotate(angle, expand=True),
                       width_pt=raster.height_pt if angle % 180 else raster.width_pt,
                       height_pt=raster.width_pt if angle % 180 else raster.height_pt,
                       rotation=(raster.rotation - angle) % 360)

    def analyze_page(self, image, page_meta):
        result, self._result = self._result, None
        if result is None:
            result = self.reader(image)
        self.warnings = ["Direct image OCR: handwriting and ambiguous markings require visual review."]
        self.refinement_incomplete = True
        if result.boxes is None:
            return []
        sx, sy = page_meta.width_pt / image.width, page_meta.height_pt / image.height
        blocks = []
        for box, text, score in zip(result.boxes, result.txts, result.scores):
            if not text.strip():
                continue
            blocks.append(Block(id=f"p{page_meta.index}_photo_{len(blocks)}", type="text",
                                order=len(blocks), text=text, confidence=float(score),
                                bbox=BBox(x0=max(0, float(box[:, 0].min()) * sx),
                                          y0=max(0, float(box[:, 1].min()) * sy),
                                          x1=min(page_meta.width_pt, float(box[:, 0].max()) * sx),
                                          y1=min(page_meta.height_pt, float(box[:, 1].max()) * sy))))
        return blocks


def image_engine(document_engine=None):
    """Reuse the reader under the caller's existing engine lock."""
    engine = getattr(document_engine, "_photo_engine", None)
    if engine is None:
        engine = PhotoOcrEngine()
        engine.load()
        if document_engine is not None:
            document_engine._photo_engine = engine
    return engine
