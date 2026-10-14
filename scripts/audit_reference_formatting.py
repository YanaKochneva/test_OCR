"""Read-only inventory of user supplied DOCX formatting, without source prose."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import sys
from zipfile import ZipFile
from lxml import etree

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{' + NS['w'] + '}'


def audit(path):
    with ZipFile(path) as z:
        root = etree.fromstring(z.read('word/document.xml'))
        styles = etree.fromstring(z.read('word/styles.xml'))
        def properties(node):
            return {etree.QName(c).localname: {etree.QName(k).localname: v for k, v in c.attrib.items()} for c in node}
        sections = [properties(s) for s in root.findall('.//w:sectPr', NS)]
        style_rows = []
        for s in styles.findall('w:style', NS):
            name = s.find('w:name', NS)
            style_rows.append({'id': s.get(W+'styleId'), 'type': s.get(W+'type'),
                'name': name.get(W+'val') if name is not None else None,
                'paragraph': [properties(x) for x in s.findall('w:pPr', NS)],
                'run': [properties(x) for x in s.findall('w:rPr', NS)]})
        fonts = Counter()
        paragraph_styles = Counter()
        for p in root.findall('.//w:p', NS):
            ps = p.find('w:pPr/w:pStyle', NS)
            paragraph_styles[ps.get(W+'val') if ps is not None else '(default)'] += 1
        for r in root.findall('.//w:r', NS):
            if not r.findall('w:t', NS):
                continue
            rp = r.find('w:rPr', NS)
            fonts[json.dumps(properties(rp) if rp is not None else {}, ensure_ascii=False, sort_keys=True)] += 1
        tables = []
        for t in root.findall('.//w:tbl', NS):
            tp = t.find('w:tblPr', NS)
            tables.append({'properties': properties(tp) if tp is not None else {},
                'grid': [c.get(W+'w') for c in t.findall('w:tblGrid/w:gridCol', NS)],
                'rows': len(t.findall('w:tr', NS)),
                'repeating_headers': len(t.findall('w:tr/w:trPr/w:tblHeader', NS))})
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'sections': sections, 'styles': style_rows,
            'paragraph_styles': dict(paragraph_styles), 'run_formats': fonts.most_common(12),
            'body_paragraphs': len(root.findall('w:body/w:p', NS)),
            'tables': tables, 'textboxes': len(root.findall('.//w:txbxContent', NS)),
            'numbered_paragraphs': len(root.findall('.//w:pPr/w:numPr', NS)),
            'drawing_count': len(root.findall('.//w:drawing', NS)),
            'header_footer_parts': [n for n in z.namelist() if n.startswith(('word/header', 'word/footer')) and n.endswith('.xml')]}


if __name__ == '__main__':
    source, out = map(Path, sys.argv[1:3])
    out.mkdir(parents=True, exist_ok=True)
    results = [audit(p) for p in sorted(source.rglob('*.docx')) if not p.name.startswith('~$')]
    (out/'reference-structure.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    for r in results:
        print(json.dumps({k:r[k] for k in ('path','body_paragraphs','textboxes','numbered_paragraphs','drawing_count')}, ensure_ascii=False))
        print(json.dumps({'sections': r['sections'], 'table_count':len(r['tables']), 'paragraph_styles':r['paragraph_styles']}, ensure_ascii=False))
