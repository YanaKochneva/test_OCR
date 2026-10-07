#!/usr/bin/env python3
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docpipe.ir import Document

out = Path(__file__).resolve().parents[1] / "docs" / "ir.schema.json"
out.write_text(json.dumps(Document.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(out)
