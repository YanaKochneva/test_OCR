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
        self.warnings: list[str] = []
        self.languages = list(languages or [os.getenv('DOCPIPE_OCR_LANGUAGE','cyrillic')])
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
        self._short_value_reader = None
        self.glm_review = False
        self._glm_reviewer = None
        self._glm_line_reviewer = None
        self._glm_line_failed = False

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
                from docpipe.stages.masking import mask_layout_figures
                text_image = (mask_layout_figures(image, blocks, page_meta)
                              if os.getenv('DOCPIPE_OCR_MASK_FIGURES', '0') == '1'
                              else image)
                blocks = refine_text_blocks(blocks, self._projected_lines(text_image,page_meta),page_meta.height_pt)
                blocks = self._refine_table_cells(blocks,image,page_meta)
                if os.getenv('DOCPIPE_OCR_TABLE_RECONCILIATION', '1') == '1':
                    from docpipe.stages.table_reconciliation import reconcile_table_lines
                    blocks = reconcile_table_lines(blocks, raw_lines)

            if self.glm_review:
                from docpipe.engines.glm_crop_reader import GlmCropReader
                from docpipe.stages.glm_review import review_blocks
                if self._glm_reviewer is None:
                    self._glm_reviewer = GlmCropReader()
                    self._glm_reviewer.load()
                blocks, events = review_blocks(
                    blocks, image, page_meta, self._glm_reviewer,
                    self._get_refinement_reader(), max_requests=2)
                for event in events:
                    if event.get('error'):
                        self.warnings.append(f"GLM review page {page_meta.index+1}: {event['error']}")
                        if event['error'] != 'GLM crop output truncated':
                            self.glm_review = False
                if events:
                    accepted = sum(event['accepted'] and event.get('candidate') != ' '.join(event['original'].split()) for event in events)
                    self.warnings.append(f'GLM review page {page_meta.index+1}: requests={len(events)}, corrections={accepted}; details in application log')

            return [
                block.model_copy(update={"order": index})
                for index, block in enumerate(blocks)
            ]

    def _get_refinement_reader(self):
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import OCRVersion, ModelType, LangRec
        if self._refinement_reader is None:
            params = {
                "Det.ocr_version":OCRVersion.PPOCRV5, "Det.model_type":ModelType.MOBILE,
                "Cls.ocr_version":OCRVersion.PPOCRV4, "Cls.model_type":ModelType.MOBILE,
                "Rec.ocr_version":OCRVersion.PPOCRV5, "Rec.model_type":ModelType.MOBILE,
                "Rec.lang_type":LangRec(self.languages[0]), "Global.text_score":.5,
            }
            self._refinement_reader = RapidOCR(params=params)
        return self._refinement_reader

    def prepare_raster(self, raster):
        if os.getenv('DOCPIPE_OCR_ORIENTATION', '0') == '0':
            return raster
        from docpipe.stages.orientation import correct_orientation
        corrected = correct_orientation(raster, self._get_refinement_reader)
        if corrected is not raster:
            import logging
            logging.getLogger(__name__).info(
                'Corrected page orientation: rotation=%s', corrected.rotation)
        return corrected

    def _projected_lines(self, image, meta):
        """Recognize horizontal ink bands missed by full-page detection."""
        from rapidocr import RapidOCR
        from rapidocr.utils.typings import OCRVersion, ModelType, LangRec
        self._get_refinement_reader()
        import numpy as np
        if (getattr(self,'_has_measured_cell_crop',False)
                and os.getenv('DOCPIPE_OCR_CLEAN_CELL_BORDERS','0')=='1'):
            from docpipe.stages.cell_borders import clean_proven_cell_borders
            image=clean_proven_cell_borders(image)
        dark=np.asarray(image.convert("L"))<170
        if getattr(self,'_inside_table_cell',False) and os.getenv('DOCPIPE_OCR_CLEAN_CELL_BORDERS','0')=='1':
            import cv2
            ink=dark.astype('uint8')
            vertical=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((max(30,image.height//3),1),np.uint8))
            horizontal=cv2.morphologyEx(ink,cv2.MORPH_OPEN,np.ones((1,max(30,image.width//3)),np.uint8))
            # Erase border remnants only; an internal digit "1" or underline
            # must not be classified as a table rule.
            if image.width>12:vertical[:,6:-6]=0
            if image.height>12:horizontal[6:-6,:]=0
            dark=dark & ~(vertical.astype(bool)|horizontal.astype(bool))
            # Use the cleaned image for recognition as well as line detection.
            cleaned=np.asarray(image.convert('RGB')).copy()
            cleaned[(vertical|horizontal).astype(bool)]=255
            image=Image.fromarray(cleaned)
        # Short numeric values have only a few dark pixels on each scan row.
        # A page-sized minimum silently excluded them in narrow table cells.
        sparse=(os.getenv('DOCPIPE_OCR_SMALL_VALUES','1')=='1'
                and getattr(self,'_inside_table_cell',False))
        active=dark.sum(axis=1)>(max(2,image.width*.003) if sparse else max(12,image.width*.008))
        indices=np.flatnonzero(active)
        bands=[]
        for y in indices:
            if bands and y-bands[-1][1]<=3:bands[-1][1]=int(y)
            else:bands.append([int(y),int(y)])
        output=[]
        for top,last in bands:
            bottom=last+1
            if bottom-top<(5 if sparse else 8) or bottom-top>max(80,image.height*.04):continue
            pixels=dark[top:bottom]
            # Exclude horizontal rules rather than transcribing table borders.
            if (pixels.sum(axis=1)>image.width*.6).any():continue
            columns=np.flatnonzero(pixels.sum(axis=0)>=2)
            if len(columns)<(3 if sparse else 10):continue
            left,right=int(columns[0]),int(columns[-1])+1
            crop=image.crop((max(0,left-6),max(0,top-6),min(image.width,right+6),min(image.height,bottom+6)))
            result=self._refinement_reader(crop,use_det=False,use_cls=False)
            text=result.txts[0].strip() if result.txts else ''
            score=float(result.scores[0]) if result.txts else 0.0
            if os.getenv('DOCPIPE_OCR_WORD_PASS','0')=='1' and score<.95 and '\\' not in text:
                from docpipe.stages.word_refinement import recognize_separated_words
                word_result=recognize_separated_words(crop,self._refinement_reader)
                if word_result is not None and word_result[1]>score+.02:
                    text,score=word_result
            if os.getenv('DOCPIPE_OCR_CONSENSUS','1')=='1':
                from docpipe.stages.recognition_consensus import recognize_with_consensus
                decision=recognize_with_consensus(crop,self._refinement_reader,(text,score))
                text,score=decision.text,decision.confidence
                if not decision.agreed:
                    import logging
                    logging.getLogger(__name__).warning(
                        'OCR candidates disagree: page=%s y=%s confidence=%.3f; primary retained',
                        meta.index+1,top,score)
            from docpipe.stages.word_refinement import refine_mixed_words
            glm_corroborated=False
            if (getattr(self,'_has_measured_cell_crop',False) and text and .65<=score<.85
                    and os.getenv('DOCPIPE_OCR_GLM_LINE_CONFIRMATION','0')=='1'
                    and not self._glm_line_failed and getattr(self,'_glm_line_attempts',0)<20):
                self._glm_line_attempts=getattr(self,'_glm_line_attempts',0)+1
                try:
                    if self._glm_line_reviewer is None:
                        from docpipe.engines.glm_crop_reader import GlmCropReader
                        self._glm_line_reviewer=GlmCropReader()
                        self._glm_line_reviewer.timeout=30
                        self._glm_line_reviewer.load()
                    alternative=self._glm_line_reviewer.read_single_line(crop)
                    from docpipe.stages.line_confirmation import same_visible_line
                    glm_corroborated=same_visible_line(text,alternative)
                except ModelError as exc:
                    self.warnings.append(f'GLM line confirmation unavailable: {exc}')
                    self._glm_line_failed=True
            alternate_corroborated=False
            if (getattr(self,'_inside_table_cell',False) and score<.85
                    and self.languages[0] in {'cyrillic','eslav'}
                    and os.getenv('DOCPIPE_OCR_ALTERNATE_LINES','0')=='1'
                    and getattr(self,'_alternate_line_attempts',0)<80):
                self._alternate_line_attempts=getattr(self,'_alternate_line_attempts',0)+1
                if self._short_value_reader is None:
                    language=LangRec.ESLAV if self.languages[0]!='eslav' else LangRec.CYRILLIC
                    self._short_value_reader=RapidOCR(params={
                        'Det.ocr_version':OCRVersion.PPOCRV5,'Det.model_type':ModelType.MOBILE,
                        'Cls.ocr_version':OCRVersion.PPOCRV4,'Cls.model_type':ModelType.MOBILE,
                        'Rec.ocr_version':OCRVersion.PPOCRV5,'Rec.model_type':ModelType.MOBILE,
                        'Rec.lang_type':language,'Global.text_score':.4})
                from docpipe.stages.alternate_recognition import recover_uncertain_line
                recovered=recover_uncertain_line(crop,(text,score),self._short_value_reader)
                if recovered is not None:
                    text,score=recovered
                    alternate_corroborated=True
            def word_reader(word_image,**kwargs):
                primary=self._refinement_reader(word_image,**kwargs)
                from docpipe.stages.word_refinement import is_mixed_word, select_word_result
                recheck = os.getenv('DOCPIPE_OCR_LATIN_RECHECK','0') == '1'
                if primary.txts and float(primary.scores[0])>=.95 and not (recheck and is_mixed_word(primary.txts[0])):
                    return primary
                if self._latin_reader is None:
                    params = {
                        'Det.ocr_version': OCRVersion.PPOCRV5, 'Det.model_type': ModelType.MOBILE,
                        'Cls.ocr_version': OCRVersion.PPOCRV4, 'Cls.model_type': ModelType.MOBILE,
                        "Rec.ocr_version":OCRVersion.PPOCRV5,"Rec.model_type":ModelType.MOBILE,
                        "Rec.lang_type":LangRec.LATIN,"Global.text_score":.75}
                    self._latin_reader=RapidOCR(params=params)
                alternative=self._latin_reader(word_image,**kwargs)
                if recheck:
                    return select_word_result(primary, alternative)
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
                        'Det.ocr_version':OCRVersion.PPOCRV5,'Det.model_type':ModelType.MOBILE,
                        'Cls.ocr_version':OCRVersion.PPOCRV4,'Cls.model_type':ModelType.MOBILE,
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
            minimum=float(os.getenv('DOCPIPE_OCR_MIN_LINE_CONFIDENCE','.85'))
            if (getattr(self,'_has_measured_cell_crop',False)
                    and os.getenv('DOCPIPE_OCR_MEASURED_CELL_RECOVERY','0')=='1'):
                minimum=.75
            corroborated = alternate_corroborated or glm_corroborated
            if (text and .55 <= score < minimum and len(text.split()) <= 3
                    and getattr(self, '_inside_table_cell', False)
                    and os.getenv('DOCPIPE_OCR_SHORT_VALUE_CONSENSUS', '1') == '1'):
                if self._short_value_reader is None:
                    # Use another trained language head, not repeated calls to
                    # the same model. Preserve its actual confidence in logs.
                    language = LangRec.ESLAV if self.languages[0] != 'eslav' else LangRec.CYRILLIC
                    self._short_value_reader = RapidOCR(params={
                        'Det.ocr_version':OCRVersion.PPOCRV5,'Det.model_type':ModelType.MOBILE,
                        'Cls.ocr_version':OCRVersion.PPOCRV4,'Cls.model_type':ModelType.MOBILE,
                        'Rec.ocr_version': OCRVersion.PPOCRV5,
                        'Rec.model_type': ModelType.MOBILE,
                        'Rec.lang_type': language, 'Global.text_score': .4,
                    })
                alternate = self._short_value_reader(crop, use_det=False, use_cls=False)
                from docpipe.stages.short_value_consensus import corroborates_short_value
                corroborated = corroborates_short_value(text, score, alternate)
                if corroborated:
                    import logging
                    logging.getLogger(__name__).warning(
                        'Recovered uncertain short cell value: page=%s text=%r confidence=%.3f alternate=%.3f',
                        meta.index + 1, text, score, float(alternate.scores[0]))
            if (score<minimum and not corroborated) or not text:continue
            box=BBox(x0=left*meta.width_pt/image.width,x1=right*meta.width_pt/image.width,
                     y0=top*meta.height_pt/image.height,y1=bottom*meta.height_pt/image.height)
            output.append((text,box,score))
        return output

    def _refine_table_cells(self, blocks, image, meta):
        """Read inside each cell so neighboring columns cannot contaminate text."""
        from rapidfuzz.distance import Levenshtein
        result=[]
        detection_retries = 0
        self._alternate_line_attempts = 0
        self._glm_line_attempts = 0
        sx,sy=image.width/meta.width_pt,image.height/meta.height_pt
        for block in blocks:
            if block.table is None:
                result.append(block);continue
            if os.getenv('DOCPIPE_OCR_MISSING_CELL_GRID', '0') == '1':
                from docpipe.stages.table_coverage import materialize_missing_cells
                block = materialize_missing_cells(block, image, meta)
            cells=[]
            from docpipe.stages.table_geometry import measured_cell_boxes
            measured=measured_cell_boxes(block,image,meta)
            reading={}
            if not measured and os.getenv('DOCPIPE_OCR_ADAPTIVE_CELL_CROPS','1')=='1':
                from docpipe.stages.table_geometry import adaptive_reading_boxes
                reading=adaptive_reading_boxes(block,image,meta)
            for cell in block.table.cells:
                box=measured.get((cell.row,cell.col),cell.bbox)
                if (cell.row,cell.col) in measured:
                    cell=cell.model_copy(update={'bbox':box})
                box=reading.get((cell.row,cell.col),box)
                if box.x1-box.x0<15 or box.y1-box.y0<5:
                    cells.append(cell);continue
                left,top=max(0,int(box.x0*sx)+2),max(0,int(box.y0*sy)+2)
                right,bottom=min(image.width,int(box.x1*sx)-2),min(image.height,int(box.y1*sy)-2)
                if right<=left or bottom<=top:
                    cells.append(cell);continue
                crop=image.crop((left,top,right,bottom))
                local=PageMeta(meta.index,(right-left)/sx,(bottom-top)/sy,0,meta.dpi)
                previous=getattr(self,'_inside_table_cell',False)
                previous_geometry=getattr(self,'_has_measured_cell_crop',False)
                self._inside_table_cell=True
                self._has_measured_cell_crop=(cell.row,cell.col) in measured or (cell.row,cell.col) in reading
                try:
                    lines=self._projected_lines(crop,local)
                finally:
                    self._inside_table_cell=previous
                    self._has_measured_cell_crop=previous_geometry
                candidate=' '.join(t for t,b,s in lines)
                from docpipe.stages.cell_recovery import detected_cell_text, accepts_cell_candidate, equivalent_cell_words
                normal=lambda text:' '.join(text.casefold().split())
                similarity=Levenshtein.normalized_similarity(normal(cell.text),normal(candidate))
                eligible = accepts_cell_candidate(cell.text,candidate) or (
                    bool(candidate) and equivalent_cell_words(cell.text,candidate))
                if (not eligible and detection_retries < 20
                        and os.getenv('DOCPIPE_OCR_CELL_DETECTION_RECOVERY','1') == '1'):
                    detection_retries += 1
                    detected = detected_cell_text(crop, self._get_refinement_reader())
                    detected_similarity = Levenshtein.normalized_similarity(normal(cell.text),normal(detected))
                    if accepts_cell_candidate(cell.text,detected):
                        candidate, similarity = detected, detected_similarity
                if os.getenv('DOCPIPE_OCR_MERGE_CANDIDATES','0')=='1' and candidate and similarity>=.8:
                    from docpipe.stages.text_refinement import merge_without_deletions
                    candidate=merge_without_deletions(cell.text,candidate)
                if accepts_cell_candidate(cell.text,candidate):
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
            result.append(block.model_copy(update={'table':block.table.model_copy(update={'cells':cells, 'html': None})}))
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
                    cell_text = str(getattr(cell, "text", "") or "")
                    # Deduplicate repeated grid aliases, not distinct payloads
                    # that share a slot with an empty merged placeholder.
                    signature = (*cell_key, cell_text,
                                 getattr(cell, 'row_span', 1), getattr(cell, 'col_span', 1))
                    if signature in seen_cells:
                        continue
                    seen_cells.add(signature)

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
