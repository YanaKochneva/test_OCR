"""Explain benchmark errors without passing the reference to recognition."""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree
from typing import Any

from rapidfuzz.distance import Levenshtein

from docpipe.evaluation import _tokens


def analyze_errors(reference: str, ir: dict[str, Any], limit: int = 30) -> dict[str, Any]:
    """Attribute alignment spans to OCR objects and report structural risks.

    For deletions the location is the neighboring OCR object, not a proven
    location of the missing source text. No page-level CER is inferred from
    a DOCX reference without trustworthy page annotations.
    """
    expected = _tokens(reference)
    actual: list[str] = []
    locations: list[dict[str, Any]] = []
    marginal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    table_summary = []
    mixed = []
    for page in ir.get('pages', []):
        page_number = page['index'] + 1
        for block in sorted(page['blocks'], key=lambda item: item['order']):
            table = block.get('table')
            cells = sorted((table or {}).get('cells', []), key=lambda c: (c['row'], c['col']))
            if table:
                occupied = {
                    (row, col) for cell in cells
                    for row in range(cell['row'], cell['row'] + cell['rowspan'])
                    for col in range(cell['col'], cell['col'] + cell['colspan'])
                }
                missing = [(row, col) for row in range(table['n_rows'])
                           for col in range(table['n_cols']) if (row, col) not in occupied]
                table_summary.append({
                    'page': page_number, 'block': block['id'],
                    'rows': table['n_rows'], 'cols': table['n_cols'],
                    'cells': len(cells), 'empty_cells': sum(not c['text'].strip() for c in cells),
                    'unrepresented_slots': missing,
                })
            for entry in cells or [{'text': block.get('text') or '', 'bbox': block['bbox']}]:
                text = entry['text']
                words = _tokens(text)
                location = {
                    'page': page_number, 'block': block['id'], 'type': block['type'],
                    'row': entry.get('row'), 'col': entry.get('col'),
                    'bbox': entry['bbox'],
                }
                actual.extend(words)
                locations.extend([location] * len(words))
                for word in words:
                    if any('a' <= c <= 'z' for c in word) and any('а' <= c <= 'я' or c == 'ё' for c in word):
                        mixed.append({**location, 'word': word})
                box = block['bbox']
                if not table and len(words) >= 3 and (
                    box['y1'] < page['height_pt'] * .12
                    or box['y0'] > page['height_pt'] * .88
                ):
                    marginal[' '.join(words)].append({**location, 'text': text})

    spans = []
    attributed = Counter()
    for op in Levenshtein.opcodes(expected, actual):
        if op.tag == 'equal':
            continue
        location = locations[min(op.dest_start, len(locations) - 1)] if locations else {}
        size = max(op.src_end - op.src_start, op.dest_end - op.dest_start)
        attributed[location.get('page')] += size
        spans.append({
            'operation': op.tag, 'size_tokens': size, **location,
            'reference': ' '.join(expected[op.src_start:op.src_end]),
            'recognized': ' '.join(actual[op.dest_start:op.dest_end]),
            'before': ' '.join(actual[max(0, op.dest_start - 10):op.dest_start]),
            'after': ' '.join(actual[op.dest_end:op.dest_end + 10]),
            'location_is_neighbor': op.tag == 'delete',
        })
    repeated = [items for items in marginal.values() if len({item['page'] for item in items}) >= 3]
    return {
        'location_policy': 'Global word alignment. Deletions assigned to neighboring OCR object; not page CER.',
        'largest_spans': sorted(spans, key=lambda item: item['size_tokens'], reverse=True)[:limit],
        'attributed_error_tokens_by_page': [
            {'page': page, 'error_tokens': count} for page, count in attributed.most_common()
        ],
        'table_cells': table_summary,
        'repeated_marginal_text': repeated,
        'mixed_script_words': mixed[:200],
        'mixed_script_word_count': len(mixed),
        'note': 'Empty cells and mixed scripts are risks, not automatically proven OCR errors. '
                'Repeated marginal text is retained; reference body extraction omits Word headers/footers.',
    }


def analyze_report(report_path: Path, limit: int = 30) -> Path:
    """Analyze saved runs; do not start OCR or change benchmark metrics."""
    from docpipe.benchmark import reference_text

    report = json.loads(report_path.read_text(encoding='utf-8'))
    output = []
    for case in report['cases']:
        if 'text' not in case:
            continue
        ir_path = Path(case['output']) / 'document.json'
        ir = json.loads(ir_path.read_text(encoding='utf-8'))
        reference = Path(case['reference'])
        audit = reference_scope_audit(reference) if reference.suffix.lower() == '.docx' else {}
        output.append({
            'case': case['case'],
            'reference_scope': audit,
            **analyze_errors(reference_text(reference), ir, limit),
        })
    path = report_path.parent / 'error-analysis.json'
    path.write_text(json.dumps({'cases': output}, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


def reference_scope_audit(reference: Path) -> dict[str, Any]:
    """Expose text categories absent from the benchmark's DOCX body reader."""
    ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    with ZipFile(reference) as archive:
        names = archive.namelist()
        margins = [name for name in names if name.startswith(('word/header', 'word/footer')) and name.endswith('.xml')]
        notes = [name for name in ('word/footnotes.xml', 'word/endnotes.xml') if name in names]
        body = ElementTree.fromstring(archive.read('word/document.xml'))
        textboxes = body.findall('.//w:txbxContent', ns)
        def chars(parts):
            return sum(len(node.text or '') for name in parts
                       for node in ElementTree.fromstring(archive.read(name)).findall('.//w:t', ns))
        return {
            'scope': 'DOCX body paragraphs and table cells; not a page-aligned transcript of all visible source objects.',
            'header_footer_parts': len(margins), 'header_footer_xml_chars': chars(margins),
            'footnote_endnote_parts': len(notes), 'footnote_endnote_xml_chars': chars(notes),
            'textbox_count': len(textboxes),
            'image_parts': sum(name.startswith('word/media/') for name in names),
            'note': 'Image counts do not identify which pictures contain text. '
                    'Header parts are not page occurrences. Main CER/WER are unchanged.',
        }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Explain a saved OCR benchmark without rerunning recognition.')
    parser.add_argument('report', type=Path)
    parser.add_argument('--limit', type=int, default=30)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error('--limit must be positive')
    print(analyze_report(args.report, args.limit))
