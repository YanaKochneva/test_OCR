"""Render every reconstructed PDF page beside its scan and inspect export objects."""
import json
import sys
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pikepdf
import pypdfium2 as pdfium
from PIL import Image, ImageDraw


def qa(report_path):
    report_path = Path(report_path)
    report = json.loads(report_path.read_text(encoding='utf-8'))
    output = report_path.parent / 'visual-qa'
    output.mkdir(exist_ok=True)
    checks = []
    for case in report['cases']:
        if 'output' not in case or not (Path(case['output'])/'document.docx').is_file():
            continue
        folder = Path(case['output'])
        row = {'case':case['case'],'status':case['status'],'pdfs':{}}
        for name in ('document-positional.pdf','docx-preview/document.pdf'):
            path = folder/name
            if not path.exists():
                continue
            target = output/case['case']/Path(name).stem
            target.mkdir(parents=True,exist_ok=True)
            with pdfium.PdfDocument(str(case['scan'])) as source, pdfium.PdfDocument(str(path)) as actual:
                row['pdfs'][name]={'source_pages':len(source),'output_pages':len(actual)}
                for index in range(len(actual)):
                    page=actual[index]
                    result=page.render(scale=1).to_pil().convert('RGB')
                    if index<len(source):
                        original=source[index].render(scale=1).to_pil().convert('RGB')
                        contact=Image.new('RGB',(original.width+result.width,max(original.height,result.height)+25),'#eeeeee')
                        contact.paste(original,(0,25));contact.paste(result,(original.width,25))
                        ImageDraw.Draw(contact).text((8,5),f'{case["case"]} page {index+1}: SCAN | RECONSTRUCTED',fill='black')
                        contact.save(target/f'page-{index+1:02d}.png')
                        original.close();contact.close()
                    result.close();page.close()
            with pikepdf.Pdf.open(path) as pdf:
                row['pdfs'][name]['invisible_text_pages']=[]
                row['pdfs'][name]['embedded_images']=0
                for index,page in enumerate(pdf.pages):
                    if any(str(item.operator)=='Tr' and int(item.operands[0])==3
                           for item in pikepdf.parse_content_stream(page)):
                        row['pdfs'][name]['invisible_text_pages'].append(index+1)
                    resources=page.obj.get('/Resources',{}).get('/XObject',{})
                    row['pdfs'][name]['embedded_images']+=sum(obj.get('/Subtype')=='/Image' for _,obj in resources.items())
        with ZipFile(folder/'document.docx') as archive:
            root=ET.fromstring(archive.read('word/document.xml'))
            ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            row['docx_native_tables']=len(root.findall('.//w:tbl',ns))
        checks.append(row)
    path=output/'checks.json'
    path.write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(checks,ensure_ascii=False,indent=2))


if __name__=='__main__':
    qa(sys.argv[1])
