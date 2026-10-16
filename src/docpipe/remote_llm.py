"""Explicit opt-in text review through AITunnel. Credentials never enter reports."""
import json
import os
from urllib.request import Request, build_opener, HTTPRedirectHandler


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class AITunnelClient:
    def __init__(self, config):
        self.config = config
        self.key = os.environ.get('AITUNNEL_API_KEY', '').strip()
        if not self.key:
            raise ValueError('AITUNNEL_API_KEY is not configured')

    def request(self, instruction, payload, response_type, image_url=None):
        if image_url:
            raise ValueError('Only text review is enabled')
        body = {
            'model': self.config.api_model,
            'messages': [
                {'role': 'system', 'content': instruction +
                 '\nReturn only JSON matching this schema: ' +
                 json.dumps(response_type.model_json_schema(), ensure_ascii=False)},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)},
            ],
            'temperature': 0,
            'max_tokens': 4096,
            'response_format': {'type': 'json_object'},
        }
        request = Request(
            'https://api.aitunnel.ru/v1/chat/completions',
            data=json.dumps(body).encode('utf-8'),
            headers={'Authorization': 'Bearer ' + self.key,
                     'Content-Type': 'application/json'}, method='POST')
        with build_opener(_NoRedirect()).open(request, timeout=self.config.timeout_seconds) as response:
            result = json.loads(response.read(2_000_000))
        choice = result['choices'][0]
        if choice.get('finish_reason') != 'stop':
            raise ValueError('API response incomplete')
        return response_type.model_validate_json(choice['message']['content'])
