"""Shared service/CLI export with optional region raster recovery."""
import json
from pathlib import Path


def render_bundle(document,config,out,requested=(),source_path=None):
    from docpipe.renderers import (render_markdown,render_html,render_pdf_flow,
        render_pdf_positional,render_pdf_searchable,render_docx)
    from docpipe.stages.image_fallback import fallback_pdf_losses
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    policy={'docx_enabled':config.render.docx_enabled,
            'unreadable_as_image':config.render.unreadable_as_image}
    (out/'output-policy.json').write_text(json.dumps(policy,indent=2),encoding='utf-8')
    if not config.render.docx_enabled:(out/'document.docx').unlink(missing_ok=True)
    def render(doc):
        (out/'document.json').write_text(doc.model_dump_json(indent=2),encoding='utf-8')
        render_markdown(doc,out/'document.md');render_html(doc,out/'document.html')
        render_pdf_flow(doc,out/'document-flow.pdf',out)
        render_pdf_positional(doc,out/'document-positional.pdf',out)
        if 'pdf-searchable' in requested:
            render_pdf_searchable(doc,Path(source_path or doc.source.file),out/'document-searchable.pdf')
        if config.render.docx_enabled:render_docx(doc,out/'document.docx',out)
    render(document)
    for _ in range(2):
        recovered,count=fallback_pdf_losses(document,config,out)
        if not count:break
        document=recovered;render(document)
    return document
