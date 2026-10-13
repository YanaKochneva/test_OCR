"""Transcribe an already localized crop through local Ollama, without layout detection."""
import base64
import io
import json
import os
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from docpipe.errors import ModelError
from docpipe.ir import BBox, Block, BlockType


class GlmCropOutputError(ModelError):
    """One unusable model response; the server remains available."""


class GlmCropReader:
    def __init__(self):
        self.url = os.getenv('DOCPIPE_GLM_REVIEW_URL', 'http://127.0.0.1:11434/api/generate')
        self.model = os.getenv('DOCPIPE_GLM_REVIEW_MODEL', 'glm-ocr:latest')
        self.timeout = 120

    def load(self):
        parts = urlsplit(self.url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username or parts.password:
            raise ModelError('Invalid DOCPIPE_GLM_REVIEW_URL')

    def analyze_page(self, image, meta):
        text=self._generate(image,2048)
        if not text:return []
        return [Block(id='glm-review', type=BlockType.TEXT, order=0, text=text,
                      bbox=BBox(x0=0,y0=0,x1=meta.width_pt,y1=meta.height_pt))]

    def read_single_line(self,image):
        """Only for an image crop already segmented into one physical line."""
        return self._generate(image,256,stop=['\n'])

    def _generate(self,image,limit,stop=None):
        buffer = io.BytesIO()
        image.convert('RGB').save(buffer, format='PNG')
        payload = {'model': self.model, 'prompt': 'Text Recognition:',
                   'images': [base64.b64encode(buffer.getvalue()).decode('ascii')],
                   'stream': False, 'options': {'temperature': 0, 'top_k': 1, 'num_predict': limit}}
        if stop is not None:payload['options']['stop']=stop
        request = Request(self.url, data=json.dumps(payload).encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(4*1024*1024+1)
            if len(raw) > 4*1024*1024:
                raise ModelError('GLM crop response too large')
            result = json.loads(raw)
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ModelError(f'GLM crop request failed: {exc}') from exc
        if not isinstance(result, dict) or result.get('error') or result.get('done') is not True:
            raise ModelError('GLM crop request incomplete or failed')
        text = result.get('response')
        if not isinstance(text, str):
            raise ModelError('GLM crop response lacks text')
        if result.get('done_reason') == 'length':
            error = GlmCropOutputError('GLM crop output truncated')
            error.output_sample = text[:400]
            raise error
        return text.strip()
