from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import hashlib
import requests
import time
import sys
from huggingface_hub import snapshot_download

revision = 'e7ea79242a87ea0ad3919b672bfedb429a683206'
model_dir = Path(__file__).resolve().parents[1] / 'models' / 'nemotron-3.5-parse'
snapshot_download('nvidia/Nemotron-3.5-Parse', revision=revision, local_dir=model_dir,
                  allow_patterns=['*.py', '*.json', 'nemotron_parse/**', 'logitsprocs/**',
                                  'LICENSE', 'THIRD_PARTY_NOTICES.md', 'TOKENIZER_ATTRIBUTION.md'])
url = f'https://huggingface.co/nvidia/Nemotron-3.5-Parse/resolve/{revision}/model.safetensors'
size = 2523266640
expected = '81dd7609c589f3694ce75520c25af5010953231358de6aec4d03e8c6aac787e9'
ready = model_dir / 'model.safetensors'
if ready.exists() and ready.stat().st_size == size:
    with ready.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() == expected:
            print('Weights already verified')
            sys.exit(0)
folder = model_dir / 'download-parts'
folder.mkdir(exist_ok=True)
chunk_size = 32 * 1024 * 1024
count = (size + chunk_size - 1) // chunk_size
with requests.get(url + '?download=true', stream=True, timeout=(15, 30)) as response:
    response.raise_for_status()
    resolved_url = response.url

def fetch(i):
    start, end = i * chunk_size, min(size, (i + 1) * chunk_size) - 1
    path = folder / str(i)
    if path.exists() and path.stat().st_size == end - start + 1:
        return i
    for attempt in range(8):
        try:
            offset = path.stat().st_size if path.exists() else 0
            while offset < end - start + 1:
                first = start + offset
                last = min(end, first + 4 * 1024 * 1024 - 1)
                with requests.get(resolved_url, headers={'Range': f'bytes={first}-{last}'}, stream=True, timeout=(15, 30)) as response:
                    response.raise_for_status()
                    assert response.status_code == 206
                    assert response.headers['Content-Range'].startswith(f'bytes {first}-{last}/')
                    with path.open('ab') as stream:
                        for part in response.iter_content(16 * 1024):
                            stream.write(part)
                offset = path.stat().st_size
            assert path.stat().st_size == end - start + 1
            return i
        except Exception as exc:
            print(f'Chunk {i} attempt {attempt}: {type(exc).__name__}', flush=True)
            if attempt == 7:
                raise

started = time.monotonic()
with ThreadPoolExecutor(max_workers=16) as pool:
    futures = [pool.submit(fetch, i) for i in range(count)]
    for n, future in enumerate(as_completed(futures), 1):
        future.result()
        print(f'{n}/{count} chunks, {time.monotonic() - started:.0f}s', flush=True)
target = folder.parent / 'model.safetensors.verified-part'
sha = hashlib.sha256()
with target.open('wb') as stream:
    for i in range(count):
        with (folder / str(i)).open('rb') as source:
            for part in iter(lambda: source.read(4 * 1024 * 1024), b''):
                stream.write(part)
                sha.update(part)
if sha.hexdigest() != expected or target.stat().st_size != size:
    raise ValueError('Weights checksum mismatch; download parts retained for inspection')
target.replace(folder.parent / 'model.safetensors')
print('Verified weights ready', flush=True)
