from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from docpipe.ir import Block
from typing import Any


@dataclass(frozen=True)
class PageMeta:
    index: int
    width_pt: float
    height_pt: float
    rotation: int
    dpi: int


class LayoutOcrEngine(ABC):
    name: str = "unknown"
    version: str = "unknown"

    @abstractmethod
    def load(self) -> None: ...

    @abstractmethod
    def analyze_page(self, image: Any, page_meta: PageMeta) -> list[Block]: ...

    def info(self):
        from docpipe.ir import EngineInfo

        return EngineInfo(name=self.name, version=self.version)


__all__ = ["LayoutOcrEngine", "PageMeta", "create_engine"]


def create_engine(name: str) -> LayoutOcrEngine:
    if name != "docling":
        raise ValueError(f"Unsupported engine: {name}")
    from docpipe.engines.docling_engine import DoclingEngine

    return DoclingEngine()
