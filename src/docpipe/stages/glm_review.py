"""Bounded crop review; GLM never supplies page geometry or a replacement table."""
from __future__ import annotations

import logging
import re
import json
import os
from pathlib import Path

import numpy as np
from rapidfuzz.distance import Levenshtein

from docpipe.engines.base import PageMeta
from docpipe.engines.glm_crop_reader import GlmCropOutputError
from docpipe.errors import ModelError
from docpipe.ir import BlockType

log = logging.getLogger(__name__)


def record(event):
    log.info('GLM review: %s', event)
    path = os.getenv('DOCPIPE_GLM_REVIEW_LOG')
    if path:
        try:
            with Path(path).open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
        except OSError:
            log.exception('Cannot write GLM review audit')


def normalized(text):
    return ' '.join(text.split())


def safe_candidate(original, candidate, witness, confidence):
    """Require independent visual agreement, preserving digits and path tokens."""
    original, candidate, witness = map(normalized, (original, candidate, witness))
    if not candidate or len(candidate) > 2000 or confidence < .85:
        return False
    if candidate != witness:
        return False
    if not original:
        return True
    if re.findall(r'\d+(?:[.,]\d+)*', original) != re.findall(r'\d+(?:[.,]\d+)*', candidate):
        return False
    if re.search(r'[\\/@]|https?://', original) and original != candidate:
        return False
    if len(candidate) < len(original) * .8 or len(candidate) > len(original) * 1.3:
        return False
    return Levenshtein.normalized_similarity(original.casefold(), candidate.casefold()) >= .7


def review_blocks(blocks, image, meta, glm, reader, max_requests=6):
    result = [b.model_copy(deep=True) for b in blocks]
    requests = 0
    events = []
    for block in result:
        targets = [(cell, cell.bbox, f'{block.id}:{cell.row}:{cell.col}')
                   for cell in block.table.cells] if block.type == BlockType.TABLE else (
            [(block, block.bbox, block.id)] if block.type in {
                BlockType.TEXT, BlockType.LIST_ITEM, BlockType.HEADING,
                BlockType.CAPTION, BlockType.FOOTNOTE} else [])
        for target, box, identifier in targets:
            if requests >= max_requests:
                break
            old = target.text or ''
            if len(old) > 2000:
                continue
            sx, sy = image.width / meta.width_pt, image.height / meta.height_pt
            bounds = (max(0, int(box.x0*sx)+2), max(0, int(box.y0*sy)+2),
                      min(image.width, int(box.x1*sx)-2), min(image.height, int(box.y1*sy)-2))
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                continue
            crop = image.crop(bounds)
            ink = np.asarray(crop.convert('L')) < 160
            # Exclude border remnants; a blank cell must not trigger a VLM request.
            ink = ink[2:-2, 2:-2]
            if ink.size == 0 or int(ink.sum()) < 12 or ink.mean() < .002:
                continue
            check = reader(crop, use_det=False, use_cls=False)
            witness = normalized(check.txts[0]) if check.txts else ''
            score = float(check.scores[0]) if check.txts else 0.
            mixed = any(re.search('[A-Za-z]', w) and re.search('[А-Яа-яЁё]', w) for w in old.split())
            uncertain = not old.strip() or mixed or score < .9 or normalized(old) != witness
            if not uncertain:
                continue
            # If even the independent reading cannot pass the acceptance guards,
            # a VLM request cannot produce an admissible correction in this mode.
            if not safe_candidate(old, witness, witness, score):
                continue
            requests += 1
            event = {'page': meta.index+1, 'target': identifier, 'original': old,
                     'witness': witness, 'witness_confidence': score, 'accepted': False}
            try:
                crop_meta = PageMeta(meta.index, crop.width/sx, crop.height/sy, 0, meta.dpi)
                regions = glm.analyze_page(crop, crop_meta)
                # A crop must yield text only. Never flatten a model-generated table.
                if any(r.type in {BlockType.TABLE, BlockType.FIGURE} for r in regions):
                    raise ModelError('GLM crop returned non-text structure')
                candidate = normalized(' '.join(r.text or '' for r in sorted(regions, key=lambda r: (r.bbox.y0, r.bbox.x0))))
                event['candidate'] = candidate
                event['accepted'] = safe_candidate(old, candidate, witness, score)
                if event['accepted'] and normalized(old) != candidate:
                    target.text = candidate
                    if target is block:
                        block.lines = None
                        block.confidence = None  # No calibrated GLM confidence is available.
                    elif block.table:
                        block.table.html = None  # Prevent stale HTML from shadowing cell text.
            except ModelError as exc:
                event['error'] = str(exc)
                if hasattr(exc, 'output_sample'):
                    event['output_sample'] = exc.output_sample
                events.append(event)
                log.warning('GLM review: %s', event)
                record(event)
                if isinstance(exc, GlmCropOutputError):
                    continue
                # Stop repeatedly calling an unavailable or incompatible server.
                return result, events
            events.append(event)
            record(event)
    return result, events
