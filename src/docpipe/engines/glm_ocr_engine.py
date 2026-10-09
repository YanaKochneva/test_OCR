"""GLM-OCR SDK server adapter (layout + VLM, normalized 0..1000 boxes)."""
from __future__ import annotations

import base64
import io
import json
import math
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from docpipe.engines.base import LayoutOcrEngine, PageMeta
from docpipe.engines.paddleocr_vl_engine import table_from_html
from docpipe.errors import ModelError
from docpipe.ir import BBox, Block, BlockType, FigureData


class GlmOcrEngine(LayoutOcrEngine):
    name = "glm_ocr"
    version = "sdk-server"

    def __init__(self):
        self.url = os.getenv("DOCPIPE_GLM_SERVER_URL", "http://127.0.0.1:5002/glmocr/parse")
        try:
            self.timeout = float(os.getenv("DOCPIPE_GLM_TIMEOUT", "600"))
        except ValueError as exc:
            raise ModelError("DOCPIPE_GLM_TIMEOUT must be a positive number") from exc
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ModelError("DOCPIPE_GLM_TIMEOUT must be a positive number")

    def load(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ModelError("DOCPIPE_GLM_SERVER_URL must be an HTTP(S) SDK server URL")

    def analyze_page(self, image, page_meta: PageMeta) -> list[Block]:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
        request = Request(self.url, data=json.dumps({"images": [data_uri]}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024:
                raise ModelError("GLM-OCR response exceeds 32 MB")
            payload = json.loads(raw)
        except HTTPError as exc:
            raise ModelError(f"GLM-OCR server returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelError("GLM-OCR server unavailable; start SDK server and check DOCPIPE_GLM_SERVER_URL") from exc
        except (ValueError, UnicodeError) as exc:
            raise ModelError("GLM-OCR server returned invalid JSON") from exc
        return self.to_blocks(payload, page_meta, image.size)

    @staticmethod
    def to_blocks(payload, meta: PageMeta, size: tuple[int, int]) -> list[Block]:
        if not isinstance(payload, dict) or payload.get("error"):
            raise ModelError("GLM-OCR response is not a successful SDK result")
        pages = payload.get("json_result")
        if isinstance(pages, str):
            try:
                pages = json.loads(pages)
            except ValueError as exc:
                raise ModelError("Invalid GLM-OCR json_result") from exc
        if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], list):
            raise ModelError("Expected exactly one page in GLM-OCR json_result")
        blocks = []
        for item in pages[0]:
            if not isinstance(item, dict):
                raise ModelError("Invalid GLM-OCR region")
            coords = item.get("bbox_2d")
            if not isinstance(coords, list) or len(coords) != 4:
                raise ModelError("GLM-OCR region lacks layout coordinates; use full SDK pipeline")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1000 for v in coords):
                raise ModelError("GLM-OCR coordinates must be finite values in 0..1000")
            x0, y0, x1, y1 = coords
            if x1 <= x0 or y1 <= y0:
                raise ModelError("GLM-OCR returned an empty or inverted region")
            box = BBox(x0=x0*meta.width_pt/1000, y0=y0*meta.height_pt/1000,
                       x1=x1*meta.width_pt/1000, y1=y1*meta.height_pt/1000)
            label = str(item.get("native_label") or item.get("label") or "text")
            mapped = str(item.get("label", label))
            content = item.get("content") or ""
            if not isinstance(content, str):
                raise ModelError("GLM-OCR content must be a string")
            kind = {"doc_title": BlockType.HEADING, "paragraph_title": BlockType.HEADING,
                    "figure_title": BlockType.CAPTION, "header": BlockType.HEADER,
                    "footer": BlockType.FOOTER, "footnote": BlockType.FOOTNOTE,
                    "number": BlockType.PAGE_NUMBER}.get(label, BlockType.TEXT)
            kwargs = {}
            if mapped in {"image", "chart"} or label in {"image", "chart", "header_image", "footer_image"}:
                kind = BlockType.FIGURE
                kwargs["figure"] = FigureData(file=f"images/glm-{meta.index}-{len(blocks)}.png", format="png",
                    width_px=max(1, round((x1-x0)*size[0]/1000)), height_px=max(1, round((y1-y0)*size[1]/1000)))
                content = None
            elif mapped == "table":
                kind = BlockType.TABLE
                if "<table" not in content.lower():
                    raise ModelError("GLM-OCR table lacks HTML structure")
                kwargs["table"] = table_from_html(content, box)
                content = None
                label += ":inferred_cell_geometry"
            elif mapped == "formula":
                kind = BlockType.FORMULA
            elif not content.strip():
                continue
            if kind == BlockType.HEADING:
                content = content.lstrip("# ")
                if not content.strip():
                    continue
            blocks.append(Block(id=f"glm-{meta.index}-{len(blocks)}", type=kind, bbox=box,
                                order=len(blocks), text=content, source_label=label, **kwargs))
        if not blocks:
            raise ModelError("GLM-OCR returned no regions")
        return blocks
