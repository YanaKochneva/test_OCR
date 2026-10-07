from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docpipe.engines.base import LayoutOcrEngine, PageMeta
from docpipe.ir import Block

class FakeEngine(LayoutOcrEngine):
    name="fake"; version="1"
    def __init__(self, fixtures: Path | None = None): self.fixtures=fixtures
    def load(self) -> None: return None
    def analyze_page(self, image: Any, page_meta: PageMeta) -> list[Block]:
        if not self.fixtures: return []
        path=self.fixtures/f"page_{page_meta.index:03d}.json"
        if not path.exists(): return []
        raw=json.loads(path.read_text(encoding="utf-8"))
        return [Block.model_validate(x) for x in raw]
