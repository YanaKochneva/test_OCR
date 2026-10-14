"""Re-export existing OCR with fixed styles; no recognition or reference access."""
import json
import shutil
import sys
from pathlib import Path
from docpipe.ir import Document
from docpipe.renderers import render_docx, render_pdf_positional, render_html
from docpipe.renderers.fixed_layout import stable_document
from docpipe.style_review import write_style_review


def main():
    source,target=map(Path,sys.argv[1:3])
    if target.exists():
        raise ValueError('Output directory must be new')
    target.mkdir(parents=True)
    report=json.loads(source.read_text(encoding='utf-8'))
    for i,case in enumerate(report['cases']):
        old=Path(case['output']);out=target/f'case-{i+1:04d}';out.mkdir()
        data=(old/'document.json').read_bytes()
        doc=Document.model_validate_json(data)
        shutil.copytree(old/'images',out/'images')
        (out/'document.json').write_bytes(data)
        render_docx(doc,out/'document.docx',out)
        render_pdf_positional(doc,out/'document-positional.pdf',out)
        render_html(doc,out/'document.html',base_dir=out)
        write_style_review(stable_document(doc),out)
        assert (out/'document.json').read_bytes()==data
        case['output']=str(out.resolve())
        case['recognition_changed']=False
        print(case['case'],'exported; OCR unchanged',flush=True)
    (target/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
