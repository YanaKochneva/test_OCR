from docpipe.engines.base import LayoutOcrEngine, PageMeta


def create_engine(name: str, **kwargs) -> LayoutOcrEngine:
    if name != "docling":
        raise ValueError(f"Unsupported engine: {name}")
    from docpipe.engines.docling_engine import DoclingEngine
    return DoclingEngine(kwargs.get("mode", "native"))
