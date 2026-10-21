"""Local Nemotron inference using NVIDIA's processor and postprocessing helpers."""
from __future__ import annotations

import sys


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
