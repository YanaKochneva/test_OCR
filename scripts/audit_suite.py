"""Read-only error attribution for a saved suite; never feed gold into OCR."""
import json
import sys
from pathlib import Path

from docpipe.benchmark import reference_text
from docpipe.error_analysis import analyze_errors
from docpipe.evaluation import _ir_text, compare_text


def main():
    suite_path, output = map(Path, sys.argv[1:3])
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    rows = []
    for case in suite["cases"]:
        if "output" not in case or "reference" not in case:
            continue
        path = Path(case["output"]) / "document.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        reference = reference_text(Path(case["reference"]))
        audit = analyze_errors(reference, data, limit=8)
        row = {"case": case["case"], "status": case["status"],
               "reference": case["reference"], "scan": case["scan"],
               "metrics": compare_text(reference, _ir_text(data)), **audit}
        rows.append(row)
        print(case["case"], round(row["metrics"]["cer"] * 100, 2), flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
