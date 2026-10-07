from __future__ import annotations
from typing import Any
from docpipe.errors import DependencyError
from docpipe.engines.base import LayoutOcrEngine, PageMeta
from docpipe.ir import Block

class PaddleOCRVLEngine(LayoutOcrEngine):
    name="paddleocr_vl"
    def __init__(self, force_gpu=False): self.force_gpu=force_gpu
    def load(self) -> None:
        try:
            import paddleocr  # type: ignore[import-not-found]
            self.version=getattr(paddleocr,"__version__","unknown")
        except ImportError as exc:
            raise DependencyError("PaddleOCR не установлен. Установите: pip install 'docpipe[vl]'") from exc
        raise DependencyError("PaddleOCR-VL требует отдельной проверки публичного API и GPU-режима")
    def analyze_page(self, image: Any, page_meta: PageMeta) -> list[Block]:
        raise DependencyError("PaddleOCR-VL не загружен")
