from __future__ import annotations
import json
from pathlib import Path

def write_verify(data:dict,out:Path)->Path:
 out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8'); return out
