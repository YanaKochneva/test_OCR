"""Export saved OCR with an explicit external standard, never reference content."""
import json
import shutil
import sys
from pathlib import Path

from docpipe.ir import Document
from docpipe.evaluation import _ir_text
from docpipe.renderers import render_docx, render_pdf_positional
from docpipe.stages.format_standard import extract_standard, apply_standard
from docpipe.style_review import write_style_review


def main():
    report_path, reference, target = map(Path, sys.argv[1:4])
    if target.exists():
        raise ValueError('Use a new output directory')
    profile=extract_standard(reference)
    target.mkdir(parents=True)
    (target/'formatting-profile.json').write_text(json.dumps(profile,ensure_ascii=False,indent=2),encoding='utf-8')
    report=json.loads(report_path.read_text(encoding='utf-8'))
    for i,case in enumerate(report['cases']):
        old=Path(case['output'])
        out=target/f'case-{i+1:04d}'
        out.mkdir()
        doc=Document.model_validate_json((old/'document.json').read_text(encoding='utf-8'))
        before=doc.model_dump()
        updated=doc.model_copy(update={'pages':[p.model_copy(update={'blocks':apply_standard(p.blocks,profile)}) for p in doc.pages]})
        assert _ir_text(before)==_ir_text(updated.model_dump())
        for p,q in zip(doc.pages,updated.pages):
            assert (p.width_pt,p.height_pt,p.index)==(q.width_pt,q.height_pt,q.index)
            for b,c in zip(p.blocks,q.blocks):
                assert b.bbox==c.bbox
                if b.table:
                    assert [(x.text,x.bbox) for x in b.table.cells]==[(x.text,x.bbox) for x in c.table.cells]
        if (old/'images').exists():
            shutil.copytree(old/'images',out/'images')
        (out/'document.json').write_text(updated.model_dump_json(indent=2),encoding='utf-8')
        render_docx(updated,out/'document.docx',out)
        render_pdf_positional(updated,out/'document-positional.pdf',out)
        review=write_style_review(updated,out)
        print(case['case'],len(review['fragments']),flush=True)
        case.update(output=str(out.resolve()),recognition_changed=False)
    report['formatting_reference']=str(reference.resolve())
    (target/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
