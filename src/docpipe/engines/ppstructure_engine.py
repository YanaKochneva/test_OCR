from __future__ import annotations

import inspect
import logging
from typing import Any

from docpipe.engines.base import LayoutOcrEngine, PageMeta
from docpipe.errors import DependencyError, ModelError
from docpipe.ir import BBox, Block, BlockType, EngineInfo, ModelInfo, TableCell, TableData

_LOG = logging.getLogger(__name__)


class PPStructureEngine(LayoutOcrEngine):
    """Адаптер публичного PP-StructureV3 API.

    Внутри адаптера остаются все знания о формате Result PaddleOCR.
    Наружу отдаётся только наш IR.
    """

    name = "ppstructure"

    def __init__(self, lang: str = "ru", gpu: bool | None = None) -> None:
        self.lang = lang
        self.gpu = gpu
        self.version = "unknown"
        self._pipeline: Any = None
        self._model_info: list[ModelInfo] = []
        self._loaded = False

    def load(self) -> None:
        try:
            import paddleocr  # type: ignore[import-not-found]
            from paddleocr import PPStructureV3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DependencyError(
                "PaddleOCR не установлен. Установите extras: pip install 'docpipe[paddle]'"
            ) from exc

        self.version = str(getattr(paddleocr, "__version__", "unknown"))
        self._assert_public_api(PPStructureV3)

        kwargs = self._constructor_kwargs(PPStructureV3)
        try:
            self._pipeline = PPStructureV3(**kwargs)
        except Exception as exc:
            raise ModelError(
                "Не удалось создать публичный PP-StructureV3 pipeline: "
                f"{exc}. Сигнатура: {inspect.signature(PPStructureV3)}"
            ) from exc

        self._model_info = [
            ModelInfo(role="layout", name="PP-DocLayout-L/default"),
            ModelInfo(role="ocr", name="eslav_PP-OCRv5_mobile_rec"),
            ModelInfo(role="table", name="PP-StructureV3 table recognition"),
        ]
        self._loaded = True
        _LOG.info(
            "PP-StructureV3 загружен: версия=%s, язык=%s, device=%s, модели=%s",
            self.version,
            self.lang,
            self._device(kwargs),
            [m.name for m in self._model_info],
        )

    def analyze_page(self, image: Any, page_meta: PageMeta) -> list[Block]:
        if not self._loaded or self._pipeline is None:
            raise DependencyError("PPStructureEngine не загружен: сначала вызовите load()")

        try:
            import numpy as np
            from PIL import Image
        except ImportError as exc:
            raise DependencyError("Для PPStructure требуется numpy и Pillow") from exc

        if isinstance(image, Image.Image):
            array = np.asarray(image.convert("RGB"))
        else:
            array = np.asarray(image)
            if array.ndim != 3:
                raise TypeError("PPStructureEngine ожидает RGB-изображение")

        try:
            prediction = next(iter(self._pipeline.predict(array)))
        except Exception as exc:
            raise ModelError(
                f"PP-StructureV3 не смог обработать страницу {page_meta.index}: {exc}"
            ) from exc

        data = self._result_json(prediction)
        return self._to_blocks(data, page_meta, array.shape[1], array.shape[0])

    def info(self) -> EngineInfo:
        return EngineInfo(name=self.name, version=self.version, models=self._model_info)

    @staticmethod
    def _assert_public_api(cls: Any) -> None:
        for name in ("__init__", "predict"):
            member = getattr(cls, name, None)
            if member is None:
                raise DependencyError(f"В установленном PaddleOCR отсутствует публичный API PPStructureV3.{name}")
            try:
                inspect.signature(member)
            except (TypeError, ValueError) as exc:
                raise DependencyError(
                    "Нельзя проверить публичную сигнатуру PP-StructureV3; "
                    "версия не прошла контрактную проверку"
                ) from exc

    def _constructor_kwargs(self, cls: Any) -> dict[str, Any]:
        params = inspect.signature(cls).parameters
        kwargs: dict[str, Any] = {}
        if "lang" in params:
            kwargs["lang"] = self.lang
        if "device" in params:
            kwargs["device"] = self._resolve_device()
        if "use_doc_orientation_classify" in params:
            kwargs["use_doc_orientation_classify"] = False
        if "use_doc_unwarping" in params:
            kwargs["use_doc_unwarping"] = False
        if "use_textline_orientation" in params:
            kwargs["use_textline_orientation"] = False
        if "use_table_recognition" in params:
            kwargs["use_table_recognition"] = True
        if "use_formula_recognition" in params:
            kwargs["use_formula_recognition"] = True
        if "text_recognition_model_name" in params:
            kwargs["text_recognition_model_name"] = "eslav_PP-OCRv5_mobile_rec"
        return kwargs

    def _resolve_device(self) -> str:
        if self.gpu is False:
            return "cpu"
        try:
            import paddle  # type: ignore[import-not-found]
            cuda = bool(paddle.device.is_compiled_with_cuda())
        except Exception:
            cuda = False
        if self.gpu is True and not cuda:
            raise DependencyError("GPU запрошен принудительно, но CUDA недоступна в Paddle")
        return "gpu:0" if cuda else "cpu"

    @staticmethod
    def _device(kwargs: dict[str, Any]) -> str:
        return str(kwargs.get("device", "auto"))

    @staticmethod
    def _result_json(result: Any) -> dict[str, Any]:
        raw = getattr(result, "json", None)
        if callable(raw):
            raw = raw()
        if not isinstance(raw, dict):
            raise ModelError(
                "PP-StructureV3 Result не предоставил публичный json-словарь; "
                "адаптер не использует приватные поля"
            )
        data = raw.get("res", raw)
        if not isinstance(data, dict):
            raise ModelError("Paddle Result.json prediction must be a dictionary")
        return data

    @classmethod
    def _to_blocks(
        cls, data: dict[str, Any], page_meta: PageMeta, width_px: int, height_px: int
    ) -> list[Block]:
        parsing = data.get("parsing_res_list", []) or []
        tables = data.get("table_res_list", []) or []
        formulas = data.get("formula_res_list", []) or []
        ocr = data.get("overall_ocr_res", {}) or {}
        blocks: list[Block] = []

        for idx, item in enumerate(parsing):
            bbox = cls._px_bbox(item.get("block_bbox"), page_meta, width_px, height_px)
            if bbox is None:
                continue
            label = str(item.get("block_label", "other")).lower()
            block_type = cls._map_label(label)
            text = item.get("block_content")
            if block_type == BlockType.FIGURE:
                block = Block(
                    id=f"pp-{page_meta.index}-{idx}",
                    type=block_type,
                    bbox=bbox,
                    order=idx,
                    confidence=None,
                    text=None,
                    figure={
                        "file": "",
                        "format": "png",
                        "width_px": max(1, round((bbox.x1 - bbox.x0) * width_px / page_meta.width_pt)),
                        "height_px": max(1, round((bbox.y1 - bbox.y0) * height_px / page_meta.height_pt)),
                    },
                    source_label=label,
                )
            elif block_type == BlockType.TABLE:
                table = cls._table_for_bbox(tables, bbox, page_meta, width_px, height_px)
                block = Block(
                    id=f"pp-{page_meta.index}-{idx}",
                    type=block_type,
                    bbox=bbox,
                    order=idx,
                    text=str(text) if text is not None else None,
                    table=table,
                    source_label=label,
                )
            else:
                block = Block(
                    id=f"pp-{page_meta.index}-{idx}",
                    type=block_type,
                    bbox=bbox,
                    order=idx,
                    text=str(text) if text is not None else None,
                    source_label=label,
                )
            blocks.append(block)

        if not blocks:
            blocks.extend(cls._ocr_fallback(ocr, page_meta, width_px, height_px))

        # Формулы могут отсутствовать в parsing_res_list; добавляем их только
        # если они ещё не покрыты layout-блоком.
        next_order = len(blocks)
        for formula in formulas:
            bbox = cls._px_bbox(formula.get("rec_polys"), page_meta, width_px, height_px)
            if bbox is None:
                continue
            blocks.append(Block(
                id=f"pp-{page_meta.index}-formula-{next_order}",
                type=BlockType.FORMULA,
                bbox=bbox,
                order=next_order,
                text=str(formula.get("rec_formula", "")),
                source_label="formula",
            ))
            next_order += 1
        return blocks

    @staticmethod
    def _map_label(label: str) -> BlockType:
        mapping = {
            "text": BlockType.TEXT,
            "paragraph": BlockType.TEXT,
            "title": BlockType.HEADING,
            "doc_title": BlockType.HEADING,
            "paragraph_title": BlockType.HEADING,
            "list": BlockType.LIST_ITEM,
            "list_item": BlockType.LIST_ITEM,
            "table": BlockType.TABLE,
            "figure": BlockType.FIGURE,
            "image": BlockType.FIGURE,
            "chart": BlockType.FIGURE,
            "seal": BlockType.FIGURE,
            "figure_title": BlockType.CAPTION,
            "figure_caption": BlockType.CAPTION,
            "table_title": BlockType.CAPTION,
            "table_caption": BlockType.CAPTION,
            "formula": BlockType.FORMULA,
            "header": BlockType.HEADER,
            "footer": BlockType.FOOTER,
            "page_number": BlockType.PAGE_NUMBER,
            "footnote": BlockType.FOOTNOTE,
        }
        return mapping.get(label, BlockType.OTHER)

    @staticmethod
    def _px_bbox(raw: Any, page_meta: PageMeta, width_px: int, height_px: int) -> BBox | None:
        if raw is None:
            return None
        try:
            values = raw.tolist() if hasattr(raw, "tolist") else raw
            if len(values) == 4 and all(isinstance(v, (int, float)) for v in values):
                x0, y0, x1, y1 = map(float, values)
            else:
                points = [(float(p[0]), float(p[1])) for p in values]
                x0 = min(p[0] for p in points)
                x1 = max(p[0] for p in points)
                y0 = min(p[1] for p in points)
                y1 = max(p[1] for p in points)
            return BBox(
                x0=max(0.0, min(page_meta.width_pt, x0 * page_meta.width_pt / width_px)),
                y0=max(0.0, min(page_meta.height_pt, y0 * page_meta.height_pt / height_px)),
                x1=max(0.0, min(page_meta.width_pt, x1 * page_meta.width_pt / width_px)),
                y1=max(0.0, min(page_meta.height_pt, y1 * page_meta.height_pt / height_px)),
            )
        except (TypeError, ValueError, IndexError):
            return None

    @classmethod
    def _table_for_bbox(
        cls, tables: list[Any], block_bbox: BBox, page_meta: PageMeta, width_px: int, height_px: int
    ) -> TableData:
        if not tables:
            return TableData()
        # PP-StructureV3 currently returns table results in recognition order;
        # the first result whose cell area intersects the layout box is used.
        best: dict[str, Any] | None = None
        best_score = 0.0
        for table in tables:
            cells = table.get("cell_box_list", []) or []
            if not cells:
                continue
            boxes = [cls._px_bbox(c, page_meta, width_px, height_px) for c in cells]
            boxes = [b for b in boxes if b is not None]
            if not boxes:
                continue
            union = BBox(
                x0=min(b.x0 for b in boxes), y0=min(b.y0 for b in boxes),
                x1=max(b.x1 for b in boxes), y1=max(b.y1 for b in boxes),
            )
            score = cls._iou(block_bbox, union)
            if score > best_score:
                best_score, best = score, table
        if best is None:
            return TableData()

        ocr = best.get("table_ocr_pred", {}) or {}
        texts = list(ocr.get("rec_texts", []) or [])
        cell_boxes = list(best.get("cell_box_list", []) or [])
        cells: list[TableCell] = []
        # Без приватного parser'а Paddle мы не придумываем rowspan/colspan:
        # геометрические ячейки получают детерминированные row/col по центрам.
        parsed: list[tuple[BBox, str]] = []
        for i, raw in enumerate(cell_boxes):
            box = cls._px_bbox(raw, page_meta, width_px, height_px)
            if box is not None:
                parsed.append((box, texts[i] if i < len(texts) else ""))
        ys = sorted({round((b.y0 + b.y1) / 2, 3) for b, _ in parsed})
        rows: list[float] = []
        for y in ys:
            if not rows or abs(y - rows[-1]) > max(1.0, page_meta.height_pt * 0.005):
                rows.append(y)
        for b, text in parsed:
            cy = (b.y0 + b.y1) / 2
            row = min(range(len(rows)), key=lambda i: abs(rows[i] - cy))
            cols = sorted({round((x.x0 + x.x1) / 2, 3) for x, _ in parsed if abs((x.y0+x.y1)/2-cy) <= max(1.0, page_meta.height_pt*0.005)})
            col = min(range(len(cols)), key=lambda i: abs(cols[i] - (b.x0+b.x1)/2)) if cols else 0
            cells.append(TableCell(row=row, col=col, text=text, bbox=b))
        return TableData(html=best.get("pred_html"), cells=cells, n_rows=len(rows), n_cols=max((c.col for c in cells), default=-1)+1)

    @staticmethod
    def _iou(a: BBox, b: BBox) -> float:
        x0, y0 = max(a.x0, b.x0), max(a.y0, b.y0)
        x1, y1 = min(a.x1, b.x1), min(a.y1, b.y1)
        inter = max(0.0, x1-x0) * max(0.0, y1-y0)
        area_a = (a.x1-a.x0)*(a.y1-a.y0)
        area_b = (b.x1-b.x0)*(b.y1-b.y0)
        return inter / (area_a + area_b - inter) if area_a + area_b - inter else 0.0

    @classmethod
    def _ocr_fallback(cls, ocr: dict[str, Any], page_meta: PageMeta, width_px: int, height_px: int) -> list[Block]:
        polys = list(ocr.get("rec_polys", []) or [])
        texts = list(ocr.get("rec_texts", []) or [])
        scores = list(ocr.get("rec_scores", []) or [])
        result: list[Block] = []
        for i, poly in enumerate(polys):
            bbox = cls._px_bbox(poly, page_meta, width_px, height_px)
            if bbox is None:
                continue
            result.append(Block(
                id=f"pp-{page_meta.index}-ocr-{i}",
                type=BlockType.TEXT,
                bbox=bbox,
                order=i,
                confidence=float(scores[i]) if i < len(scores) else None,
                text=texts[i] if i < len(texts) else "",
                source_label="ocr",
            ))
        return result
