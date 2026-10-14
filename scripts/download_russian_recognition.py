"""Explicit, pinned download; inference never contacts Hugging Face."""
import hashlib
import json
import sys
from pathlib import Path
from urllib.request import urlopen
from docpipe.engines.russian_recognition import MODEL_REPO, MODEL_REVISION


def main():
    target=Path(sys.argv[1]) if len(sys.argv)>1 else Path('models/ppocrv6-medium-ru')
    target.mkdir(parents=True,exist_ok=True)
    hashes={}
    for filename in ('model.onnx','rec_keys.txt','inference.yml'):
        temp=target/(filename+'.part')
        with urlopen(f'https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{filename}',timeout=120) as src,temp.open('wb') as dst:
            while chunk:=src.read(1024*1024):
                dst.write(chunk)
        temp.replace(target/filename)
        hashes[filename]=hashlib.sha256((target/filename).read_bytes()).hexdigest()
    (target/'manifest.json').write_text(json.dumps(dict(repo=MODEL_REPO,revision=MODEL_REVISION,sha256=hashes),indent=2),encoding='utf-8')
    print(target.resolve())


if __name__=='__main__':
    main()
