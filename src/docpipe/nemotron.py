from __future__ import annotations

import re
import sys
import time
from difflib import SequenceMatcher
from docpipe.input import load_page_image as _page_image
from functools import lru_cache
from rapidfuzz.distance import Levenshtein


PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"


class NemotronReader:
    name = "nemotron"
    version = "3.5"

    def __init__(self, config):
        self.config = config
        self.model = None
        self.ready = False

    def load(self):
        import torch
        from transformers import AutoModel, AutoProcessor, GenerationConfig

        self.path = self.config.nemotron_model.resolve()
        if not (self.path / "model.safetensors").is_file():
            raise RuntimeError(f"Download nvidia/Nemotron-3.5-Parse to {self.path} first")
        sys.path.insert(0, str(self.path))
        from nemotron_parse import postprocess_document
        from nemotron_parse.postprocessing import convert_mmd_to_plain_text_ours

        self.postprocess = postprocess_document
        self.plain_text = convert_mmd_to_plain_text_ours
        self.device = self.config.nemotron_device
        if self.device == "auto":
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.bfloat16 if self.device.startswith("cuda") else torch.float32
        self.model = AutoModel.from_pretrained(
            self.path, trust_remote_code=True, local_files_only=True, dtype=self.dtype,
        ).to(self.device).eval()
        self.processor = AutoProcessor.from_pretrained(self.path, trust_remote_code=True, local_files_only=True)
        self.generation = GenerationConfig.from_pretrained(self.path, local_files_only=True)
        self.generation.max_new_tokens = 8744
        self.ready = True

    def recognize(self, image, timeout):
        import torch
        import time
        from bs4 import BeautifulSoup
        from transformers import StoppingCriteria, StoppingCriteriaList

        deadline = time.monotonic() + timeout
        if not self.ready:
            self.load()
        if time.monotonic() >= deadline:
            raise TimeoutError("Nemotron loading exceeded crop time budget")

        class Deadline(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return time.monotonic() >= deadline

        inputs = self.processor(images=[image.convert("RGB")], text=PROMPT,
                                return_tensors="pt", add_special_tokens=False).to(self.device)
        with torch.inference_mode():
            outputs = self.model.generate(**inputs, generation_config=self.generation,
                                          stopping_criteria=StoppingCriteriaList([Deadline()]))
        if time.monotonic() >= deadline:
            raise TimeoutError("Nemotron crop exceeded time budget")
        if outputs.shape[-1] >= self.generation.max_new_tokens:
            raise RuntimeError("Nemotron output reached token limit; refusing truncated page")
        raw = self.processor.batch_decode(outputs, skip_special_tokens=False)[0]
        prediction = self.postprocess(raw, image_size=image.size)
        text = "\n".join(self.plain_text(BeautifulSoup(block["content"], "html.parser").get_text(" ", strip=True))
                         for block in prediction["blocks"])
        if not text.strip():
            raise ValueError("Nemotron returned empty crop transcription")
        return text.strip()


def candidates(document, min_chars):
    for page in document.pages:
        for block in sorted(page.blocks, key=lambda b: b.order):
            prefix = f"p{page.index + 1}:{block.id}"
            if block.table:
                for cell in block.table.cells:
                    if len(cell.text.strip()) >= min_chars:
                        yield page, f"{prefix}:c{cell.row},{cell.col}", cell.text, cell.bbox
            elif (
                block.text
                and len(block.text.strip()) >= min_chars
                and block.type.value not in {"header", "footer", "page_number"}
            ):
                yield page, prefix, block.text, block.bbox


@lru_cache(maxsize=1)
def russian_dictionary():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


def proposals(original, recognized, known_word=None):
    """Require close transcription and local word alignment; never delete content."""
    from docpipe.review import valid_correction

    known_word = known_word or russian_dictionary().word_is_known

    def normalize(value):
        return re.sub(r"\s+", " ", value).strip()

    original, recognized = normalize(original), normalize(recognized)
    if not original or not 0.65 <= len(recognized) / len(original) <= 1.45:
        return [], "length_mismatch"
    if Levenshtein.normalized_similarity(original.casefold(), recognized.casefold()) < 0.72:
        return [], "transcription_disagrees"
    left, right = original.split(), recognized.split()
    matcher = SequenceMatcher(
        None, [s.casefold() for s in left], [s.casefold() for s in right], autojunk=False
    )
    changes = []
    for tag, i0, i1, j0, j1 in matcher.get_opcodes():
        if tag != "replace" or i1 - i0 != j1 - j0:
            continue
        for before, after in zip(left[i0:i1], right[j0:j1]):
            before = before.strip('.,;:!?«»"()[]•–—')
            after = after.strip('.,;:!?«»"()[]•–—')
            if (
                before == after
                or original.count(before) != 1
                or not valid_correction(before, after)
                or not re.fullmatch("[А-Яа-яЁёA-Za-z-]+", before)
                or not re.fullmatch("[А-Яа-яЁёA-Za-z-]+", after)
            ):
                continue
            # Preserve dictionary words, abbreviations, and unknown domain terms
            # for Nemotron, even when a replacement looks plausible.
            # A correction must turn an unknown token into a known Russian word.
            if (
                known_word(before.casefold())
                or not known_word(after.casefold())
                or before.isupper()
                or after.isupper()
                or before.count("-") != after.count("-")
                or before.endswith("-") != after.endswith("-")
            ):
                continue
            if Levenshtein.distance(before.casefold(), after.casefold()) > max(2, len(before) // 5):
                continue
            changes.append((before, after))
    return changes, None


def refine_document(document, config, out=None, reader=None):
    from docpipe.review import _apply

    started = time.monotonic()
    backend = "nemotron"
    report = {
        "stage": f"{backend}_reread",
        "model": "nvidia/Nemotron-3.5-Parse",
        "mode": "image_only_text_recognition",
        "calls": 0,
        "corrections": [],
        "issues": [],
        "units": [],
        "review_required": False,
    }
    if reader is None:

        reader = NemotronReader(config.review)
    try:
        russian_dictionary()
    except ImportError:
        report.update(
            status="skipped",
            review_required=True,
            issues=[{"kind": "missing_dependency", "detail": "Install docpipe[nemotron-review]"}],
        )
        return document, report
    selected = list(candidates(document, config.review.reread_min_chars))
    report["units_total"] = len(selected)
    changes = {}
    image = None
    current_page = None
    try:
        for page, unit_id, text, bbox in selected:
            unit_started = time.monotonic()
            remaining = config.review.stage_timeout_seconds - (time.monotonic() - started)
            if remaining <= 0 or report["calls"] >= config.review.max_calls:
                report["issues"].append({"kind": "budget_exceeded"})
                break
            try:
                if current_page != page.index:
                    if image is not None:
                        image.close()
                    image = _page_image(
                        document.source.file, page, config.review.reread_dpi, config.limits
                    )
                    current_page = page.index
                sx, sy = image.width / page.width_pt, image.height / page.height_pt
                box = (
                    max(0, round(bbox.x0 * sx) - 8),
                    max(0, round(bbox.y0 * sy) - 8),
                    min(image.width, round(bbox.x1 * sx) + 8),
                    min(image.height, round(bbox.y1 * sy) + 8),
                )
                with image.crop(box) as crop:
                    report["calls"] += 1
                    recognized = reader.recognize(
                        crop, min(remaining, config.review.timeout_seconds)
                    )
                edits, rejected = proposals(text, recognized)
                item = {
                    "unit_id": unit_id,
                    "page": page.index + 1,
                    "before": text,
                    "recognized": recognized,
                    "rejected": rejected,
                    "proposed_edits": [list(edit) for edit in edits],
                    "elapsed_seconds": round(time.monotonic() - unit_started, 3),
                }
                report["units"].append(item)
                for before, after in edits:
                    changes.setdefault(unit_id, {})[before] = after
                    report["corrections"].append(
                        {
                            "unit_id": unit_id,
                            "page": page.index + 1,
                            "before": before,
                            "after": after,
                            "source": f"{backend}_ocr_reread",
                        }
                    )
            except Exception as exc:
                report["issues"].append(
                    {"unit_id": unit_id, "page": page.index + 1, "kind": type(exc).__name__,
                     "detail": str(exc)[:300]}
                )
                break
    finally:
        if image is not None:
            image.close()
    report["units_completed"] = len(report["units"])
    report["rejected_units"] = sum(bool(unit["rejected"]) for unit in report["units"])
    report["review_required"] = bool(
        report["corrections"] or report["issues"] or report["rejected_units"]
    )
    report["status"] = (
        "partial"
        if report["issues"] or len(report["units"]) < len(selected)
        else "completed_with_findings"
        if report["review_required"]
        else "completed"
    )
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["runtime_device"] = getattr(reader, "device", None)
    report["model_path"] = str(config.review.nemotron_model.resolve())
    document = _apply(document, changes, mark_review=True)
    return document, report
