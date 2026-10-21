"""Opt-in AITunnel review. Credentials never enter reports."""

import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def create_review_client(config, document=None):
    if config.provider == "nemotron":
        raise ValueError("Nemotron is a local crop reader, not a chat review transport")
    return AITunnelClient(config)


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
