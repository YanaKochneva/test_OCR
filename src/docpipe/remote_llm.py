"""Opt-in AITunnel or local Ollama review. Credentials never enter reports."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def create_review_client(config, document=None):
    if config.provider == "ollama":
        return OllamaClient(config, document)
    return AITunnelClient(config)


def local_ollama(config):
    from urllib.parse import urlsplit

    url = urlsplit(config.ollama_url)
    return (
        config.provider == "ollama"
        and url.scheme in {"http", "https"}
        and not url.username
        and not url.password
        and url.hostname in {"localhost", "127.0.0.1", "::1"}
    )


class OllamaClient:
    """Experimental GLM-OCR reviewer using Ollama's native structured API."""

    def __init__(self, config, document=None):
        from urllib.parse import urlsplit

        url = urlsplit(config.ollama_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("Invalid Ollama URL")
        self.config = config
        self.document = document
        self.deadline = None
        self._images = {}

    def set_deadline(self, deadline):
        self.deadline = deadline

    def request(self, instruction, payload, response_type, image_url=None):
        if image_url is None and self.document is not None:
            from docpipe.review import units
            from docpipe.visual_review import _preview

            pages = {key: number for key, number, _ in units(self.document)}
            numbers = (
                {pages[u["unit_id"]] for u in payload["units"]}
                if "units" in payload
                else {payload["page"]}
            )
            image_url = []
            for number in sorted(numbers):
                page = next(p for p in self.document.pages if p.index + 1 == number)
                if number not in self._images:
                    rotation = page.rotation
                    if str(self.document.source.file).lower().endswith(".pdf"):
                        import pypdfium2 as pdfium

                        with pdfium.PdfDocument(self.document.source.file) as pdf:
                            original = pdf[page.index]
                            try:
                                rotation = (rotation - original.get_rotation()) % 360
                            finally:
                                original.close()
                    self._images[number] = _preview(
                        self.document.source.file, page.index, self.config.visual_max_side, rotation
                    )[0]
                image_url.append(self._images[number])
        urls = image_url if isinstance(image_url, list) else [image_url] if image_url else []
        if len(urls) > 2 or any(
            not isinstance(url, str)
            or not url.startswith(("data:image/jpeg;base64,", "data:image/png;base64,"))
            for url in urls
        ):
            raise ValueError("Ollama review accepts at most two embedded page images")
        if sum(len(url) for url in urls) > 12_000_000:
            raise ValueError("Visual review image payload too large")
        if len(urls) == 2:
            # GLM-OCR in Ollama supports one image per request. Keep both pages
            # in a single canvas so the existing visual-review contract works.
            import base64
            import io

            from PIL import Image

            with (
                Image.open(io.BytesIO(base64.b64decode(urls[0].split(",", 1)[1]))) as left,
                Image.open(io.BytesIO(base64.b64decode(urls[1].split(",", 1)[1]))) as right,
            ):
                canvas = Image.new(
                    "RGB", (left.width + right.width + 24, max(left.height, right.height)), "white"
                )
                try:
                    canvas.paste(left, (0, 0))
                    canvas.paste(right, (left.width + 24, 0))
                    stream = io.BytesIO()
                    canvas.save(stream, format="JPEG", quality=90)
                    urls = [
                        "data:image/jpeg;base64,"
                        + base64.b64encode(stream.getvalue()).decode("ascii")
                    ]
                finally:
                    canvas.close()
            instruction += (
                "\nThe two images are joined on one canvas: FIRST/source is LEFT; "
                "SECOND/reconstructed is RIGHT. Report boxes relative to the RIGHT page only."
            )
        body = {
            "model": self.config.api_model,
            "stream": False,
            "prompt": instruction
            + "\nReturn only JSON matching this schema: "
            + json.dumps(response_type.model_json_schema(), ensure_ascii=False)
            + "\nInput data:\n"
            + json.dumps(payload, ensure_ascii=False),
            "format": response_type.model_json_schema(),
            "options": {"temperature": 0, "num_predict": 4096, "num_ctx": 16384},
            "images": [url.split(",", 1)[1] for url in urls],
        }
        remaining = (
            self.deadline - time.monotonic()
            if self.deadline is not None
            else self.config.timeout_seconds
        )
        if remaining <= 0:
            raise TimeoutError("LLM stage time budget exceeded")
        request = Request(
            self.config.ollama_url.rstrip("/") + "/api/generate",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(_NoRedirect()).open(
                request, timeout=min(self.config.timeout_seconds, remaining)
            ) as response:
                data = response.read(2_000_001)
        except HTTPError as exc:
            raise RuntimeError(f"Ollama HTTP {exc.code}") from None
        except URLError:
            raise ConnectionError("Ollama connection failed") from None
        if len(data) > 2_000_000:
            raise ValueError("API response exceeds size limit")
        result = json.loads(data)
        if not result.get("done") or result.get("done_reason") != "stop":
            raise ValueError("API response incomplete")
        return response_type.model_validate_json(result["response"])


class AITunnelClient:
    def __init__(self, config):
        self.config = config
        self.key = os.environ.get("AITUNNEL_API_KEY", "").strip()
        if not self.key:
            raise ValueError("AITUNNEL_API_KEY is not configured")
        self.deadline = None

    def set_deadline(self, deadline):
        self.deadline = deadline

    def request(self, instruction, payload, response_type, image_url=None):
        content = json.dumps(payload, ensure_ascii=False)
        if image_url:
            urls = image_url if isinstance(image_url, list) else [image_url]
            if len(urls) > 2 or any(
                not isinstance(url, str)
                or not url.startswith(("data:image/jpeg;base64,", "data:image/png;base64,"))
                for url in urls
            ):
                raise ValueError("Visual review accepts at most two embedded page images")
            if sum(len(url) for url in urls) > 12_000_000:
                raise ValueError("Visual review image payload too large")
            content = [{"type": "text", "text": content}] + [
                {"type": "image_url", "image_url": {"url": url, "detail": "high"}} for url in urls
            ]
        body = {
            "model": self.config.api_model,
            "messages": [
                {
                    "role": "system",
                    "content": instruction
                    + "\nReturn only JSON matching this schema: "
                    + json.dumps(response_type.model_json_schema(), ensure_ascii=False),
                },
                {"role": "user", "content": content},
            ],
            "temperature": 0,
            "max_tokens": 4096,
            "response_format": {"type": "json_object"},
        }
        if image_url:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": response_type.model_json_schema(),
                },
            }
        request = Request(
            "https://api.aitunnel.ru/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            method="POST",
        )
        remaining = (
            (self.deadline - time.monotonic())
            if self.deadline is not None
            else self.config.timeout_seconds
        )
        if remaining <= 0:
            raise TimeoutError("LLM stage time budget exceeded")
        try:
            with build_opener(_NoRedirect()).open(
                request, timeout=min(self.config.timeout_seconds, remaining)
            ) as response:
                data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    raise ValueError("API response exceeds size limit")
                result = json.loads(data)
        except HTTPError as exc:
            # Do not include provider body, key or document text in diagnostics.
            raise RuntimeError(f"AITunnel HTTP {exc.code}") from None
        except URLError:
            raise ConnectionError("AITunnel connection failed") from None
        choice = result["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("API response incomplete")
        return response_type.model_validate_json(choice["message"]["content"])
