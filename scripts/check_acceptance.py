"""Acceptance gates for the user-selected control corpus; no OCR or gold editing."""
import json
import sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import pypdfium2 as pdfium
from docpipe.renderers.page_size import A4_WIDTH,A4_HEIGHT


def main():
    folder=Path(sys.argv[1]);limit=float(sys.argv[2]) if len(sys.argv)>2 else .015
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    results=[]
    for case in report['cases']:
        out=Path(case.get('output',''))
        row={'case':case['case'],'status':case['status'],'max_cer':limit,
             'cer':case.get('text',{}).get('reference_scoped_cer'),
             'ordinary_cer':case.get('text',{}).get('cer'),'wer':case.get('text',{}).get('reference_scoped_wer')}
        try:
            ir=json.loads((out/'document.json').read_text(encoding='utf-8'))
            row['failed_pages']=[p['index']+1 for p in ir['pages'] if p['status']=='failed']
            row['degraded_pages']=[p['index']+1 for p in ir['pages'] if p['status']=='degraded']
            with pdfium.PdfDocument(case['scan']) as original:
                row['source_pages']=len(original)
            row['ir_pages']=len(ir['pages'])
            with pdfium.PdfDocument(out/'document-positional.pdf') as pdf:
                row['pdf_pages']=len(pdf)
                row['pdf_a4']=all(abs(sorted(p.get_size())[0]-A4_WIDTH)<.2 and
                                  abs(sorted(p.get_size())[1]-A4_HEIGHT)<.2 for p in pdf)
            with ZipFile(out/'document.docx') as archive:
                root=ET.fromstring(archive.read('word/document.xml'))
                ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                sizes=[sorted([int(s.get('{'+ns['w']+'}w'))/20,int(s.get('{'+ns['w']+'}h'))/20])
                       for s in root.findall('.//w:sectPr/w:pgSz',ns)]
                row['docx_a4']=len(sizes)==row['ir_pages'] and all(abs(w-A4_WIDTH)<.2 and abs(h-A4_HEIGHT)<.2 for w,h in sizes)
            row['processing_ok']=(case['status']=='evaluated' and case.get('exit_code')==0
                and not row['failed_pages'] and not row['degraded_pages']
                and row['source_pages']==row['ir_pages']==row['pdf_pages']
                and bool(case.get('verification',{}).get('ok')))
            row['export_ok']=row['pdf_a4'] and row['docx_a4']
        except Exception as exc:
            row.update(processing_ok=False,export_ok=False,error=str(exc))
        row['quality_ok']=row['cer'] is not None and row['cer']<=limit
        row['accepted']=row['processing_ok'] and row['quality_ok'] and row['export_ok']
        results.append(row)
    failures=sum(not row['processing_ok'] for row in results)
    accepted=bool(results) and all(row['accepted'] for row in results)
    result={'accepted':accepted,'metric':'reference_scoped_cer; standard CER separately reported',
            'max_cer':limit,'processing_failure_rate':failures/max(1,len(results)),'cases':results}
    (folder/'acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# Приёмка контрольного набора','',
        '| Документ | CER согласованный | Обычный CER | Обработка | А4 PDF/DOCX | Приёмка |',
        '|---|---:|---:|---|---|---|']
    for row in results:
        pct=lambda v:f'{100*v:.3f}%' if v is not None else '—'
        lines.append('| '+' | '.join([row['case'],pct(row['cer']),pct(row['ordinary_cer']),
            'OK' if row['processing_ok'] else 'FAIL','OK' if row['export_ok'] else 'FAIL',
            'PASS' if row['accepted'] else 'FAIL'])+' |')
    lines.extend(['',f'Порог CER для каждого документа: ≤{100*limit:.3f}%. Провалы обработки: {100*result["processing_failure_rate"]:.1f}%.',
        '', 'Согласованный CER исключает вставки относительно эталона и графические пустые поля, записанные в эталоне сериями подчёркиваний. Подчёркивания в идентификаторах остаются текстом. Исключения действуют только при оценке; содержимое выходных файлов не удаляется. Обычный CER показан без этих исключений.',
        '', 'Успех обработки требует успешного процесса, проверки выходных файлов, отсутствия failed/degraded-страниц и совпадения числа страниц входа, IR и PDF. Проверка А4 выполняется по фактическим размерам PDF и разделов DOCX. Это техническая проверка, а не подтверждение совпадения каждого глифа со сканом.'])
    (folder/'ACCEPTANCE_RU.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True,indent=2))
    return 0 if accepted else 1


if __name__=='__main__':
    raise SystemExit(main())
