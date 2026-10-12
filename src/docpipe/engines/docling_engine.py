from __future__ import annotations

import inspect
import os
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from docpipe.engines.base import LayoutOcrEngine, PageMeta
from docpipe.errors import DependencyError, ModelError
from docpipe.ir import (
    BBox,
    Block,
    BlockType,
)


class DoclingEngine(LayoutOcrEngine):
    name = "docling"

    def __init__(
        self,
        mode: str = "native",
        languages: list[str] | None = None,
    ) -> None:
        if mode not in {"native", "layout_only"}:
            raise ValueError(
                f"Unsupported Docling mode: {mode!r}. "
                "Expected 'native' or 'layout_only'."
            )

        self.mode = mode
        self.languages = list(languages or ["eslav"])
        if len(self.languages) != 1:
            raise ValueError(
                "RapidOCR supports one recognition language per pass; "
                "configure exactly one language (for Russian, use 'eslav')."
            )

        self.version = "unknown"
        self._converter = None
        self._model_info: list[dict[str, Any]] = []
        self._loaded = False
        self._refinement_reader = None
        self._cyrillic_reader = None
        self._latin_reader = None

    def load(self) -> None:
        try:
            import docling
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                PdfPipelineOptions,
                RapidOcrOptions,
                TableStructureOptions,
            )
            from docling.document_converter import (
                DocumentConverter,
                ImageFormatOption,
                PdfFormatOption,
            )
        except ImportError as exc:
            raise DependencyError(
                "Docling is not installed. "
                "Install it with the appropriate optional dependency."
            ) from exc

        from importlib.metadata import version
        self.version = version("docling")

        self._assert_public_api(
            PdfPipelineOptions=PdfPipelineOptions,
            RapidOcrOptions=RapidOcrOptions,
            TableStructureOptions=TableStructureOptions,
            DocumentConverter=DocumentConverter,
            ImageFormatOption=ImageFormatOption,
            PdfFormatOption=PdfFormatOption,
        )

        pipeline_options = PdfPipelineOptions()
        # Retain OCR lines until the one-page result has been reconciled with
        # layout blocks; otherwise assembly can silently discard unassigned text.
        if hasattr(pipeline_options, "generate_parsed_pages"):
            pipeline_options.generate_parsed_pages = True

        artifacts_path = os.getenv("DOCLING_ARTIFACTS_PATH")
        if artifacts_path:
            model_dir = Path(artifacts_path)
            if not model_dir.is_dir():
                raise ModelError(
                    f"Configured Docling artifacts directory does not exist: {model_dir}"
                )
            if hasattr(pipeline_options, "artifacts_path"):
                pipeline_options.artifacts_path = model_dir

        if self.mode == "layout_only":
            raise DependencyError(
                "Docling 'layout_only' mode is not implemented for this adapter."
            )

        # OCR
        pipeline_options.do_ocr = True
        pipeline_options.ocr_options = self._make_rapidocr_options(
            RapidOcrOptions
        )

        # Table structure recognition
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options = TableStructureOptions(
            do_cell_matching=True
        )

        # We rasterize pages ourselves before passing them to Docling.
        # Therefore there is no need to ask Docling to generate additional
        # page/picture images.
        if hasattr(pipeline_options, "generate_page_images"):
            pipeline_options.generate_page_images = False

        if hasattr(pipeline_options, "generate_picture_images"):
            pipeline_options.generate_picture_images = False

        format_option = PdfFormatOption(
            pipeline_options=pipeline_options
        )
        image_format_option = ImageFormatOption(
            pipeline_options=pipeline_options
        )

        self._converter = DocumentConverter(
            format_options={
                InputFormat.PDF: format_option,
                # analyze_page passes rasterized PNGs, which Docling routes
                # through IMAGE. Configure that pipeline with the same OCR
                # language and table options as the PDF pipeline.
                InputFormat.IMAGE: image_format_option,
            }
        )

        # Load and validate model artifacts before reporting the engine ready.
        # Docling's IMAGE pipeline is the one used by analyze_page().
        try:
            self._converter.initialize_pipeline(InputFormat.IMAGE)
        except Exception as exc:
            self._converter = None
            raise ModelError(
                "Docling IMAGE pipeline initialization failed; verify that "
                "the required model artifacts are installed and accessible."
            ) from exc

        self._model_info = self._discover_model_info(
            pipeline_options
        )

        self._loaded = True

    def analyze_page(
        self,
        image: Image.Image,
        page_meta: PageMeta,
    ) -> list[Block]:
        if not self._loaded or self._converter is None:
            raise DependencyError(
                "DoclingEngine is not loaded. Call load() first."
            )

        if not isinstance(image, Image.Image):
            raise TypeError(
                f"Expected PIL.Image.Image, got {type(image)!r}"
            )

        with tempfile.TemporaryDirectory(
            prefix="docpipe-docling-"
        ) as tmp:
            source = Path(tmp) / "page.png"

            image.convert("RGB").save(
                source,
                format="PNG",
            )

            try:
                result = self._converter.convert(source)
                document = result.document
            except Exception as exc:
                raise ModelError(
                    f"Docling failed to process page: {exc}"
                ) from exc

            blocks: list[Block] = []

            for item, _ in document.iterate_items():
                provs = getattr(item, "prov", None) or []

                # We process exactly one page at a time, therefore the
                # relevant provenance record is page 1 inside Docling's
                # temporary PNG document.
                prov = next(
                    (
                        p
                        for p in provs
                        if getattr(p, "page_no", None) == 1
                    ),
                    None,
                )

                if prov is None:
                    continue

                bbox = self._bbox_from_provenance(
                    prov,
                    page_meta.width_pt,
                    page_meta.height_pt,
                    image.width,
                    image.height,
                )

                label = self._label(item)

                block = self._item_to_block(
                    item,
                    label,
                    bbox,
                    page_meta,
                )

                if block is not None:
                    blocks.append(block)

            from types import SimpleNamespace
            from docpipe.stages.ocr_recovery import recover_unassigned_lines

            raw_lines = []
            for parsed_page in getattr(result, "pages", []):
                for cell in getattr(parsed_page, "cells", []):
                    cell_box = self._bbox_from_provenance(
                        SimpleNamespace(bbox=cell.to_bounding_box()),
                        page_meta.width_pt, page_meta.height_pt, image.width, image.height,
                    )
                    if (0 <= cell_box.x0 <= cell_box.x1 <= page_meta.width_pt
                            and 0 <= cell_box.y0 <= cell_box.y1 <= page_meta.height_pt):
                        raw_lines.append((cell.text, cell_box, float(cell.confidence)))
            blocks = recover_unassigned_lines(blocks, raw_lines, page_meta.height_pt)
            if os.getenv("DOCPIPE_OCR_LINE_REFINEMENT", "1") == "1":
                from docpipe.stages.text_refinement import refine_text_blocks
                blocks = refine_text_blocks(blocks, self._projected_lines(image,page_meta),page_meta.height_pt)
                blocks = self._refine_table_cells(blocks,image,page_meta)

            return [
                block.model_copy(update={"order": index})
                for index, block in enumerate(blocks)
            ]

    def _projected_lines(self, image, meta):
        """Recognize horizontal ink bands missed by full-page detection."""
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import OCRVersion, ModelType, LangRec
        if self._refinement_reader is None:
            self._refinement_reader = RapidOCR(params={
                "Det.ocr_version":OCRVersion.PPOCRV5, "Det.model_type":ModelType.MOBILE,
                "Cls.ocr_version":OCRVersion.PPOCRV4, "Cls.model_type":ModelType.MOBILE,
                "Rec.ocr_version":OCRVersion.PPOCRV5, "Rec.model_type":ModelType.MOBILE,
                "Rec.lang_type":LangRec(self.languages[0]), "Global.text_score":.75,
            })
        import numpy as np
        dark=np.asarray(image.convert("L"))<170
        active=dark.sum(axis=1)>max(12,image.width*.008)
        indices=np.flatnonzero(active)
        bands=[]
        for y in indices:
            if bands and y-bands[-1][1]<=3:bands[-1][1]=int(y)
            else:bands.append([int(y),int(y)])
        output=[]
        for top,last in bands:
            bottom=last+1
            if bottom-top<8 or bottom-top>max(80,image.height*.04):continue
            pixels=dark[top:bottom]
            # Exclude horizontal rules rather than transcribing table borders.
            if (pixels.sum(axis=1)>image.width*.6).any():continue
            columns=np.flatnonzero(pixels.sum(axis=0)>=2)
            if len(columns)<10:continue
            left,right=int(columns[0]),int(columns[-1])+1
            crop=image.crop((max(0,left-6),max(0,top-6),min(image.width,right+6),min(image.height,bottom+6)))
            result=self._refinement_reader(crop,use_det=False,use_cls=False)
            if not result.txts:continue
            text=result.txts[0].strip();score=float(result.scores[0])
            from docpipe.stages.word_refinement import refine_mixed_words
            def word_reader(word_image,**kwargs):
                primary=self._refinement_reader(word_image,**kwargs)
                if primary.txts and float(primary.scores[0])>=.95:return primary
                if self._latin_reader is None:
                    self._latin_reader=RapidOCR(params={
                        "Rec.ocr_version":OCRVersion.PPOCRV5,"Rec.model_type":ModelType.MOBILE,
                        "Rec.lang_type":LangRec.LATIN,"Global.text_score":.75})
                alternative=self._latin_reader(word_image,**kwargs)
                return alternative if alternative.txts and (not primary.txts or alternative.scores[0]>primary.scores[0]) else primary
            text=refine_mixed_words(crop,text,word_reader)
            if '\\' in text:
                from docpipe.stages.path_recognition import recognize_path_components
                path_result=recognize_path_components(crop,self._refinement_reader)
                if path_result is not None:
                    candidate,candidate_score=path_result
                    from rapidfuzz.distance import Levenshtein
                    if Levenshtein.normalized_similarity(text.casefold(),candidate.casefold())>=.7:
                        text,score=candidate,candidate_score
            if '\\' in text and any('А'<=c<='я' for c in text):
                if self._cyrillic_reader is None:
                    self._cyrillic_reader=RapidOCR(params={
                        "Rec.ocr_version":OCRVersion.PPOCRV5,
                        "Rec.model_type":ModelType.MOBILE,
                        "Rec.lang_type":LangRec.CYRILLIC,
                        "Global.text_score":.75,
                    })
                alternative=self._cyrillic_reader(crop,use_det=False,use_cls=False)
                if alternative.txts and float(alternative.scores[0])>=.85:
                    from docpipe.stages.mixed_scripts import choose_path_candidate
                    selected=choose_path_candidate(text,alternative.txts[0].strip())
                    if selected!=text:
                        text=selected;score=float(alternative.scores[0])
            if score<.85 or not text:continue
            box=BBox(x0=left*meta.width_pt/image.width,x1=right*meta.width_pt/image.width,
                     y0=top*meta.height_pt/image.height,y1=bottom*meta.height_pt/image.height)
            output.append((text,box,score))
        return output

    def _refine_table_cells(self, blocks, image, meta):
        """Read inside each cell so neighboring columns cannot contaminate text."""
        from rapidfuzz.distance import Levenshtein
        result=[]
        sx,sy=image.width/meta.width_pt,image.height/meta.height_pt
        for block in blocks:
            if block.table is None:
                result.append(block);continue
            cells=[]
            for cell in block.table.cells:
                box=cell.bbox
                if box.x1-box.x0<15 or box.y1-box.y0<5:
                    cells.append(cell);continue
                left,top=max(0,int(box.x0*sx)+2),max(0,int(box.y0*sy)+2)
                right,bottom=min(image.width,int(box.x1*sx)-2),min(image.height,int(box.y1*sy)-2)
                if right<=left or bottom<=top:
                    cells.append(cell);continue
                crop=image.crop((left,top,right,bottom))
                local=PageMeta(meta.index,(right-left)/sx,(bottom-top)/sy,0,meta.dpi)
                lines=self._projected_lines(crop,local)
                candidate=' '.join(t for t,b,s in lines)
                normal=lambda text:' '.join(text.casefold().split())
                similarity=Levenshtein.normalized_similarity(normal(cell.text),normal(candidate))
                if candidate and (not cell.text.strip() or (similarity>=.8 and len(candidate)>=len(cell.text)*.9)):
                    cell=cell.model_copy(update={'text':candidate})
                elif candidate and cell.text.strip():
                    import re
                    old_word=cell.text.split()[0];new_word=candidate.split()[0]
                    mixed=bool(re.search('[А-Яа-яЁё]',old_word) and re.search('[A-Za-z]',old_word))
                    if mixed and re.fullmatch('[A-Za-z]{4,}',new_word) and abs(len(new_word)-len(old_word))<=2:
                        # Keep the rest of a cell when full-cell reconciliation
                        # rejects a candidate; this word was reread from pixels.
                        cell=cell.model_copy(update={'text':new_word+cell.text[len(old_word):]})
                cells.append(cell)
            result.append(block.model_copy(update={'table':block.table.model_copy(update={'cells':cells})}))
        return result

    @staticmethod
    def _bbox_from_provenance(
        prov: Any,
        page_width_pt: float,
        page_height_pt: float,
        image_width_px: int,
        image_height_px: int,
    ) -> BBox:
        """
        Convert Docling provenance coordinates into our IR coordinates.

        Docling receives a rasterized PNG in this adapter, so provenance
        coordinates are expressed in image pixels.

        Docling commonly reports coordinates with BOTTOMLEFT origin:

            (0, 0) = bottom-left

        Our IR uses PDF points with TOPLEFT origin:

            (0, 0) = top-left

        Therefore we:

        1. Read the bbox in pixels.
        2. Convert BOTTOMLEFT -> TOPLEFT.
        3. Convert pixels -> PDF points.
        """

        raw = prov.bbox

        origin = str(
            getattr(
                getattr(raw, "coord_origin", None),
                "value",
                getattr(raw, "coord_origin", "TOPLEFT"),
            )
        )

        x0_px = float(raw.l)
        x1_px = float(raw.r)

        y0_px = float(raw.b)
        y1_px = float(raw.t)

        if origin.upper().endswith("BOTTOMLEFT"):
            y0_px, y1_px = (
                image_height_px - y1_px,
                image_height_px - y0_px,
            )
        else:
            y0_px, y1_px = (
                min(y0_px, y1_px),
                max(y0_px, y1_px),
            )

        scale_x = page_width_pt / image_width_px
        scale_y = page_height_pt / image_height_px

        return BBox(
            x0=x0_px * scale_x,
            y0=y0_px * scale_y,
            x1=x1_px * scale_x,
            y1=y1_px * scale_y,
        )

    def _item_to_block(
        self,
        item: Any,
        label: str,
        bbox: BBox,
        page_meta: PageMeta,
    ) -> Block | None:
        mapping = {
            "text": BlockType.TEXT,
            "paragraph": BlockType.TEXT,
            "title": BlockType.HEADING,
            "section_header": BlockType.HEADING,
            "list_item": BlockType.LIST_ITEM,
            "caption": BlockType.CAPTION,
            "formula": BlockType.FORMULA,
            "page_header": BlockType.HEADER,
            "page_footer": BlockType.FOOTER,
            "page_number": BlockType.PAGE_NUMBER,
            "footnote": BlockType.FOOTNOTE,
            "document_index": BlockType.TEXT,
            "picture": BlockType.FIGURE,
            "chart": BlockType.FIGURE,
            "table": BlockType.TABLE,
        }

        block_type = mapping.get(label)

        if block_type is None and self._extract_text(item):
            block_type = BlockType.TEXT
        if block_type is None:
            return None

        text = self._extract_text(item)
        if label == "document_index":
            rows: dict[int, list[tuple[int, str]]] = {}
            seen = set()
            for cell in getattr(getattr(item, "data", None), "table_cells", []):
                key = (cell.start_row_offset_idx, cell.start_col_offset_idx)
                if key not in seen:
                    seen.add(key)
                    rows.setdefault(key[0], []).append((key[1], cell.text))
            text = "\n".join(" ".join(t for _, t in sorted(row))
                             for _, row in sorted(rows.items()))

        # Docling's own reference, e.g. "#/texts/2".
        # Fall back to Python object identity if unavailable.
        item_id = getattr(item, "self_ref", None)

        if item_id is None:
            item_id = f"docling-{label}-{id(item)}"
        else:
            item_id = str(item_id)

        if block_type == BlockType.TABLE:
            table_data = getattr(item, "data", None)

            if table_data is None:
                raise ValueError(
                    f"Docling table {item_id} has no data"
                )

            from docpipe.ir import TableCell, TableData

            cells: list[TableCell] = []

            canonical_cells = getattr(table_data, "table_cells", None)
            grid = [canonical_cells] if canonical_cells is not None else (getattr(table_data, "grid", None) or [])
            seen_cells = set()

            for row_idx, row in enumerate(grid):
                for col_idx, cell in enumerate(row):
                    if cell is None:
                        continue
                    cell_key = (
                        getattr(cell, "start_row_offset_idx", row_idx),
                        getattr(cell, "start_col_offset_idx", col_idx),
                    )
                    if cell_key in seen_cells:
                        continue
                    seen_cells.add(cell_key)

                    cell_text = str(getattr(cell, "text", "") or "")

                    cell_bbox = getattr(cell, "bbox", None)

                    # Retain actual text even when cell geometry is absent.
                    # Empty grid placeholders still have nothing to preserve.
                    if cell_bbox is None:
                        from types import SimpleNamespace
                        rows = max(1, int(getattr(table_data, "num_rows", 1)))
                        cols = max(1, int(getattr(table_data, "num_cols", 1)))
                        row, col = cell_key
                        scale = page_meta.dpi / 72
                        cell_bbox = SimpleNamespace(
                            l=(bbox.x0 + (bbox.x1 - bbox.x0) * col / cols) * scale,
                            r=(bbox.x0 + (bbox.x1 - bbox.x0) * min(cols, col + getattr(cell, "col_span", 1)) / cols) * scale,
                            t=(bbox.y0 + (bbox.y1 - bbox.y0) * row / rows) * scale,
                            b=(bbox.y0 + (bbox.y1 - bbox.y0) * min(rows, row + getattr(cell, "row_span", 1)) / rows) * scale,
                        )

                    cell_x0 = float(cell_bbox.l)
                    cell_x1 = float(cell_bbox.r)
                    cell_y0 = float(cell_bbox.t)
                    cell_y1 = float(cell_bbox.b)

                    # Docling table-cell coordinates are already TOPLEFT
                    # pixel coordinates for the rasterized page.
                    image_width_px = page_meta.width_pt * page_meta.dpi / 72
                    image_height_px = page_meta.height_pt * page_meta.dpi / 72

                    scale_x = page_meta.width_pt / image_width_px
                    scale_y = page_meta.height_pt / image_height_px

                    cell_ir_bbox = BBox(
                        x0=cell_x0 * scale_x,
                        y0=cell_y0 * scale_y,
                        x1=cell_x1 * scale_x,
                        y1=cell_y1 * scale_y,
                    )

                    start_row = int(
                        getattr(cell, "start_row_offset_idx", row_idx)
                    )
                    start_col = int(
                        getattr(cell, "start_col_offset_idx", col_idx)
                    )

                    rowspan = max(
                        1,
                        int(getattr(cell, "row_span", 1)),
                    )
                    colspan = max(
                        1,
                        int(getattr(cell, "col_span", 1)),
                    )

                    cells.append(
                        TableCell(
                            row=start_row,
                            col=start_col,
                            rowspan=rowspan,
                            colspan=colspan,
                            text=cell_text,
                            bbox=cell_ir_bbox,
                        )
                    )

            return Block(
                id=item_id,
                type=block_type,
                bbox=bbox,
                order=0,
                text=None,
                table=TableData(
                    cells=cells,
                    n_rows=int(getattr(table_data, "num_rows", 0)),
                    n_cols=int(getattr(table_data, "num_cols", 0)),
                ),
            )

        if block_type == BlockType.FIGURE:
            width_px = max(
                1,
                round(
                    (bbox.x1 - bbox.x0)
                    * page_meta.dpi
                    / 72
                ),
            )

            height_px = max(
                1,
                round(
                    (bbox.y1 - bbox.y0)
                    * page_meta.dpi
                    / 72
                ),
            )

            return Block(
                id=item_id,
                type=block_type,
                bbox=bbox,
                order=0,
                text=None,
                figure={
                    "file": "",
                    "format": "png",
                    "width_px": width_px,
                    "height_px": height_px,
                },
            )

        return Block(
            id=item_id,
            type=block_type,
            bbox=bbox,
            order=0,
            text=text,
            source_label=label,
        )

    @staticmethod
    def _label(item: Any) -> str:
        """
        Return a normalized Docling item label.
        """

        label = getattr(item, "label", None)

        if label is None:
            return "text"

        value = getattr(label, "value", label)

        return str(value).lower()

    @staticmethod
    def _extract_text(item: Any) -> str:
        """
        Extract textual content from a Docling item.
        """

        for attribute in (
            "text",
            "orig",
            "content",
        ):
            value = getattr(item, attribute, None)

            if value is not None:
                text = str(value).strip()

                if text:
                    return text

        return ""

    def _make_rapidocr_options(self, cls: Any) -> Any:
        """
        Build RapidOcrOptions while remaining compatible with
        different Docling versions.
        """

        params = inspect.signature(cls).parameters

        kwargs: dict[str, Any] = {}

        if "lang" in params:
            kwargs["lang"] = self.languages

        if "use_gpu" in params:
            kwargs["use_gpu"] = False

        if "mode" in params:
            try:
                # With postponed annotations, inspect.signature() often
                # returns a string here. Import the enum itself so OCR cannot
                # silently fall back to Docling's PDF-aware default mode.
                from docling.datamodel.pipeline_options import OcrMode

                kwargs["mode"] = OcrMode.FULL_PAGE
            except (ImportError, AttributeError):
                pass

        if "mode" not in kwargs and "force_full_page_ocr" in params:
            kwargs["force_full_page_ocr"] = True

        return cls(**kwargs)

    @staticmethod
    def _assert_public_api(**objects: Any) -> None:
        """
        Basic sanity check that the imported Docling API objects exist.
        """

        missing = [
            name
            for name, value in objects.items()
            if value is None
        ]

        if missing:
            raise DependencyError(
                "Docling public API is incomplete. "
                f"Missing: {', '.join(missing)}"
            )

    @staticmethod
    def _discover_model_info(
        pipeline_options: Any,
    ) -> list[dict[str, Any]]:
        """
        Collect lightweight model/config information for IR metadata.
        """

        result: list[dict[str, Any]] = []

        ocr_options = getattr(
            pipeline_options,
            "ocr_options",
            None,
        )

        if ocr_options is not None:
            result.append(
                {
                    "component": "ocr",
                    "class": type(ocr_options).__name__,
                    "config": str(ocr_options),
                }
            )

        table_options = getattr(
            pipeline_options,
            "table_structure_options",
            None,
        )

        if table_options is not None:
            result.append(
                {
                    "component": "table_structure",
                    "class": type(table_options).__name__,
                    "config": str(table_options),
                }
            )

        return result

    @property
    def model_info(self) -> list[dict[str, Any]]:
        return list(self._model_info)
