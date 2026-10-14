"""Local focused audit; references are used only after recognition."""
import json
import os
from pathlib import Path
import logging

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/refinement_audit/focused'
OUT.mkdir(parents=True, exist_ok=True)
os.environ['DOCPIPE_OCR_DIAGNOSTICS'] = str(OUT / 'crops')
logging.basicConfig(filename=OUT / 'processing.log', level=logging.INFO)

from docpipe.engines.docling_engine import DoclingEngine
from docpipe.pipeline import parse
from docpipe.renderers.docx import render as render_docx
from docpipe.renderers.html import render as render_html
from docpipe.benchmark import reference_text
from docpipe.evaluation import _ir_text, compare_text

source = Path(r'C:\Users\andre\Desktop\тесты\tables\doc_tbl_008')
scan = next(source.glob('*_no_text.pdf'))
reference = next(p for p in source.glob('*.pdf') if p != scan)
engine = DoclingEngine()
engine.load()
doc = parse(scan, engine_instance=engine, out_dir=OUT)
(OUT / 'document.json').write_text(doc.model_dump_json(indent=2), encoding='utf-8')
render_docx(doc, OUT / 'document.docx', OUT)
render_html(doc, OUT / 'document.html')
gold = reference_text(reference)
baseline = ROOT / 'data/jobs/b5c2a18fff0a4bee8921d44fd9f4778e/output/document.json'
results = {}
for name, path in [('baseline', baseline), ('current', OUT / 'document.json')]:
    data = json.loads(path.read_text(encoding='utf-8'))
    results[name] = dict(metrics=compare_text(gold, _ir_text(data)),
                        pages=[dict(index=p['index'], status=p['status'], blocks=len(p['blocks']),
                                    warnings=p['warnings']) for p in data['pages']], timings=data['timings'])
from zipfile import ZipFile
from xml.etree import ElementTree as ET
with ZipFile(OUT / 'document.docx') as archive:
    xml = ET.fromstring(archive.read('word/document.xml'))
ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
docx_text = ' '.join(node.text or '' for node in xml.findall('.//w:t', ns))
html = (OUT / 'document.html').read_text(encoding='utf-8')
from html.parser import HTMLParser
class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
parser = TextParser()
parser.feed(html)
html_text = ' '.join(' '.join(parser.parts).split())
import html as html_module
missing_docx, missing_html = [], []
for page in doc.pages:
    for block in page.blocks:
        texts = [cell.text for cell in block.table.cells] if block.table else [block.text or '']
        for text in texts:
            if text and ' '.join(text.split()) not in ' '.join(docx_text.split()): missing_docx.append(text)
            if text and ' '.join(text.split()) not in html_text: missing_html.append(text)
results['export_check'] = dict(missing_docx=missing_docx, missing_html=missing_html,
                             native_docx_tables=len(xml.findall('.//w:tbl', ns)))
(OUT / 'comparison.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v['pages'] for k,v in results.items() if 'pages' in v}, ensure_ascii=True))
