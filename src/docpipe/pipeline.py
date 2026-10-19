from __future__ import annotations

import gc
import hashlib
import logging
import math
import time
from pathlib import Path

import pypdfium2 as pdfium

from docpipe.config import AppConfig
from docpipe.engines import create_engine
from docpipe.engines.base import PageMeta
from docpipe.errors import InputError
from docpipe.io.normalize import normalize_input
from docpipe.io.textlayer import analyze_pdf
from docpipe.ir import Document, Page, SourceInfo, SourceType, PageStatus
from docpipe.render.raster import bounded_dpi, rasterize_page
from docpipe.stages.order import assign_order
from docpipe.stages.postprocess import postprocess
from docpipe.stages.figures import PixelBox, crop_and_save
from docpipe.stages.captions import attach_captions

LOG = logging.getLogger(__name__)


def _memory_failure(exc: Exception) -> bool:
    """Recognize wrapped allocation failures without swallowing unrelated errors."""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, MemoryError):
            return True
        if any(token in str(current).casefold() for token in
               ('out of memory', 'failed to allocate', 'unable to allocate', 'insufficient memory', 'bad allocation', 'bad_alloc')):
            return True
        current = current.__cause__ or current.__context__
    return False


def _save_figures(blocks, raster, out_dir, page_index):
    """One semantic region -> one asset; never reuse a neighbour's merged crop."""
    if out_dir is None:
        return blocks
    sx = raster.image.width / raster.width_pt
    sy = raster.image.height / raster.height_pt
    output = []
    for index, block in enumerate(blocks):
        if block.type.value != 'figure':
            output.append(block)
            continue
        box = block.bbox
        pixels = PixelBox(max(0, math.floor(box.x0 * sx)), max(0, math.floor(box.y0 * sy)),
                          min(raster.image.width, math.ceil(box.x1 * sx)),
                          min(raster.image.height, math.ceil(box.y1 * sy)))
        if pixels.x1 <= pixels.x0 or pixels.y1 <= pixels.y0:
            raise ValueError(f'Empty figure region: {block.id}, page {page_index + 1}')
        folder = out_dir / 'images' / f'p{page_index+1:03d}_b{index:03d}'
        _, path = crop_and_save(raster.image, [pixels], folder, page_index + 1)[0]
        data = block.figure.model_copy(update={
            'file': path.relative_to(out_dir).as_posix(), 'format': path.suffix[1:],
            'width_px': pixels.x1 - pixels.x0, 'height_px': pixels.y1 - pixels.y0,
        })
        output.append(block.model_copy(update={'figure': data, 'text': None}))
    return output


def _process_page(page, index, layer, engine, config, out_dir):
    width, height = page.get_size()
    dpi = bounded_dpi(width, height, config.default_dpi, config.limits)
    warnings = []
    if dpi != config.default_dpi:
        warnings.append(f'Raster DPI limited: requested={config.default_dpi}, actual={dpi}; '
                        f'pixel budget={config.limits.max_pixels_per_page}')
    reset_required = False
    for attempt in range(3):
        raster = None
        blocks = None
        try:
            if reset_required:
                reset = getattr(engine, 'reset_after_memory_error', None)
                if reset is not None:
                    reset()
                reset_required = False
            raster = rasterize_page(page, dpi, config.limits)
            prepare = getattr(engine, 'prepare_raster', None)
            if prepare is not None and config.render.correct_page_orientation:
                prepared = prepare(raster)
                if prepared.image is not raster.image:
                    raster.image.close()
                raster = prepared
            meta = PageMeta(index, raster.width_pt, raster.height_pt, raster.rotation, raster.dpi)
            engine.refinement_incomplete = False
            blocks = engine.analyze_page(raster.image, meta)
            from docpipe.stages.list_markers import restore_list_markers
            blocks = restore_list_markers(blocks, raster.image, meta)
            from docpipe.stages.table_visual_layout import restore_table_visual_layout
            reader_factory=getattr(engine,'_get_refinement_reader',None)
            blocks = restore_table_visual_layout(blocks, raster.image, meta,
                line_reader=reader_factory() if reader_factory else None)
            if config.render.formatting_reference:
                from docpipe.stages.format_standard import apply_standard, extract_standard
                blocks = apply_standard(blocks, extract_standard(config.render.formatting_reference))
            from docpipe.stages.graphic_fields import retain_graphic_fields
            blocks = retain_graphic_fields(blocks, raster.image, meta)
            blocks = _save_figures(blocks, raster, out_dir, index)
            if engine.name not in {'paddleocr_vl', 'ppstructure'}:
                blocks = assign_order(blocks, raster.width_pt)
            blocks = postprocess(attach_captions(blocks), config.render.headers_footers)
            from docpipe.stages.image_fallback import fallback_unreadable
            blocks = fallback_unreadable(blocks,raster,config,out_dir,index)
            for name in ('_refinement_reader', '_short_value_reader', '_latin_reader', '_cyrillic_reader'):
                reader = getattr(engine, name, None)
                failures = getattr(reader, 'failures', []) if reader is not None else []
                if failures:
                    engine.refinement_incomplete = True
                    warnings.extend(f'Unread OCR refinement: {event!r}' for event in failures)
            return Page(index=index, width_pt=raster.width_pt, height_pt=raster.height_pt,
                        rotation=raster.rotation, raster_dpi=raster.dpi,
                        size_px=raster.image.size, status=(PageStatus.DEGRADED
                            if getattr(engine, 'refinement_incomplete', False) else PageStatus.OK),
                        text_layer=layer, blocks=blocks,
                        warnings=warnings + list(getattr(engine, 'warnings', [])))
        except Exception as exc:
            detail = f'{type(exc).__name__}: {str(exc) or repr(exc)}'
            LOG.exception('Page %s failed, attempt=%s, DPI=%s: %s', index+1, attempt+1, dpi, detail)
            warnings.append(f'Page {index+1}, attempt {attempt+1}, DPI {dpi}: {detail}')
            next_dpi = max(1, math.floor(dpi * .7))
            if _memory_failure(exc) and attempt < 2 and next_dpi < dpi:
                reset_required = True
                # First release model state and retry at the original resolution.
                # Only the final attempt lowers DPI, preserving page geometry.
                if attempt:
                    dpi = next_dpi
                continue
            return Page(index=index, width_pt=width, height_pt=height,
                        rotation=page.get_rotation() or 0, raster_dpi=dpi,
                        size_px=(math.ceil(width*dpi/72), math.ceil(height*dpi/72)),
                        status=PageStatus.FAILED, text_layer=layer, blocks=[],
                        warnings=warnings)
        finally:
            if raster is not None:
                raster.image.close()
            blocks = None
            gc.collect()
    raise RuntimeError('Page retry loop exited unexpectedly')


def parse(path: str | Path, config: AppConfig | None = None, engine: str = 'docling',
          fixtures: Path | None = None, out_dir: Path | None = None, engine_instance=None,
          progress_callback=None) -> Document:
    config = config or AppConfig()
    source = Path(path)
    pdf_path = normalize_input(source, config)
    started = time.perf_counter()
    try:
        with pdfium.PdfDocument(str(pdf_path)) as document:
            if len(document) > config.limits.max_pages:
                raise InputError('Число страниц превышает лимит')
        statuses = analyze_pdf(pdf_path, config.text_layer)
        eng = engine_instance or create_engine(engine, fixtures=fixtures)
        if engine_instance is None:
            eng.load()
        pages = []
        with pdfium.PdfDocument(str(pdf_path)) as document:
            total = len(document)
            for index in range(total):
                page = None
                try:
                    page = document[index]
                    pages.append(_process_page(page, index, statuses[index], eng, config, out_dir))
                except Exception as exc:
                    # Include allocation/metadata failures occurring before the
                    # page retry loop; never abort all following pages silently.
                    LOG.exception('Cannot initialize page %s', index+1)
                    try:
                        width, height = page.get_size() if page is not None else (1, 1)
                        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
                            width, height = 1, 1
                    except Exception:
                        width, height = 1, 1
                    pages.append(Page(index=index, width_pt=width, height_pt=height,
                        rotation=0, raster_dpi=config.default_dpi, size_px=(0, 0),
                        status=PageStatus.FAILED, text_layer=statuses[index], blocks=[],
                        warnings=[f'Page initialization failed: {type(exc).__name__}: '
                                  f'{str(exc) or repr(exc)}']))
                finally:
                    if page is not None:
                        page.close()
                if progress_callback:
                    progress_callback(index + 1, total)
        from docpipe.stages.repeated_margins import classify_repeated_margins
        pages = classify_repeated_margins(pages, config.render.headers_footers)
        result = Document(source=SourceInfo(file=str(source), sha256=_sha256(source),
                        type=SourceType.PDF if source.suffix.lower() == '.pdf' else SourceType.IMAGE),
                        engine=eng.info(), pages=pages, timings={'parse_total': time.perf_counter()-started})
        from docpipe.review import review_before_render
        result, _ = review_before_render(result, config, out_dir)
        return result
    finally:
        if pdf_path != source:
            pdf_path.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()
