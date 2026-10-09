from docpipe.engines.base import LayoutOcrEngine, PageMeta
from docpipe.engines.fake_engine import FakeEngine


def create_engine(name: str, **kwargs) -> LayoutOcrEngine:
    if name == "glm_ocr":
        from docpipe.engines.glm_ocr_engine import GlmOcrEngine
        return GlmOcrEngine()
    if name == "fake": return FakeEngine(kwargs.get("fixtures"))
    if name == "docling":
        from docpipe.engines.docling_engine import DoclingEngine
        return DoclingEngine(kwargs.get("mode","native"))
    if name == "ppstructure":
        from docpipe.engines.ppstructure_engine import PPStructureEngine
        return PPStructureEngine(kwargs.get("lang","eslav"))
    if name == "paddleocr_vl":
        from docpipe.engines.paddleocr_vl_engine import PaddleOCRVLEngine
        return PaddleOCRVLEngine(kwargs.get("force_gpu",False))
    raise ValueError(f"Неизвестный engine: {name}")
