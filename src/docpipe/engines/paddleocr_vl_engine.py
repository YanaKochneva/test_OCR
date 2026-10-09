"""Optional structured VLM lane using PaddleOCR's public prediction API."""
from __future__ import annotations

import os
from typing import Any

from docpipe.engines.ppstructure_engine import PPStructureEngine
from docpipe.errors import DependencyError, ModelError
from docpipe.ir import BBox, TableCell, TableData


def table_from_html(content: str, box: BBox) -> TableData:
    """Retain topology/spans; per-cell geometry is inferred, not measured."""
    from lxml import html
    root = html.fromstring(content)
    rows = root.xpath('.//tr')
    if not rows or len(rows) > 512:
        raise ModelError("VLM table has no rows or exceeds the row limit")
    occupied = set()
    placements = []
    for row, tr in enumerate(rows):
        col = 0
        for td in tr.xpath('./td|./th'):
            while (row, col) in occupied:
                col += 1
            rowspan = int(td.get("rowspan", "1"))
            colspan = int(td.get("colspan", "1"))
            if not (1 <= rowspan <= len(rows) - row and 1 <= colspan <= 128 and col + colspan <= 128):
                raise ModelError("VLM table contains invalid spans")
            placements.append((row, col, rowspan, colspan, td.text_content()))
            occupied.update((r, c) for r in range(row, row + rowspan)
                            for c in range(col, col + colspan))
            col += colspan
    cols = max((c + cs for _, c, _, cs, _ in placements), default=0)
    if not cols:
        raise ModelError("VLM table contains no cells")
    cells = [TableCell(row=r, col=c, rowspan=rs, colspan=cs, text=text,
             bbox=BBox(x0=box.x0 + (box.x1 - box.x0) * c / cols,
                       x1=box.x0 + (box.x1 - box.x0) * (c + cs) / cols,
                       y0=box.y0 + (box.y1 - box.y0) * r / len(rows),
                       y1=box.y0 + (box.y1 - box.y0) * (r + rs) / len(rows)))
             for r, c, rs, cs, text in placements]
    return TableData(html=content, n_rows=len(rows), n_cols=cols, cells=cells)


class PaddleOCRVLEngine(PPStructureEngine):
    name = "paddleocr_vl"

    def __init__(self, force_gpu: bool = False):
        super().__init__(gpu=True if force_gpu else False)

    def load(self) -> None:
        try:
            import paddleocr
            from paddleocr import PaddleOCRVL
        except ImportError as exc:
            raise DependencyError("Install docpipe[vl] to enable PaddleOCR-VL") from exc
        kwargs: dict[str, Any] = {
            "device": self._resolve_device(),
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
        }
        server = os.getenv("DOCPIPE_VL_SERVER_URL")
        if server:
            kwargs.update(vl_rec_backend="vllm-server", vl_rec_server_url=server)
        try:
            self._pipeline = PaddleOCRVL(**kwargs)
        except Exception as exc:
            raise ModelError(f"PaddleOCR-VL initialization failed: {exc}") from exc
        self.version = str(getattr(paddleocr, "__version__", "unknown"))
        self._loaded = True

    @classmethod
    def _to_blocks(cls, data, page_meta, width_px, height_px):
        blocks = super()._to_blocks(data, page_meta, width_px, height_px)
        for index, block in enumerate(blocks):
            if block.table is not None:
                if "<table" not in (block.text or "").lower():
                    raise ModelError("VLM table response lacks supported HTML structure")
                blocks[index] = block.model_copy(update={
                    "table": table_from_html(block.text, block.bbox), "text": None,
                    "source_label": "vl_table_inferred_cell_geometry",
                })
        return blocks
