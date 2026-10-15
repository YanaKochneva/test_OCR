"""Read source crops locally; reference contents are not used."""
import json
import sys
from pathlib import Path
import numpy as np
import pypdfium2 as pdfium
from docpipe.engines.docling_engine import DoclingEngine
from docpipe.stages.word_refinement import recognize_separated_words


def main():
    report=Path(sys.argv[1]);index=int(sys.argv[2]);out=Path(sys.argv[3]);out.mkdir(parents=True,exist_ok=True)
    case=json.loads(report.read_text(encoding='utf-8'))['cases'][index]
    ir=json.loads((Path(case['output'])/'document.json').read_text(encoding='utf-8'))
    reader=DoclingEngine()._get_refinement_reader()
    results=[]
    with pdfium.PdfDocument(case['scan']) as pdf:
        for page in ir['pages']:
            blocks=[b for b in page['blocks'] if b['type']=='footnote']
            if not blocks:continue
            rendered=pdf[page['index']].render(scale=300/72).to_pil().convert('RGB')
            for k,b in enumerate(blocks):
                box=b['bbox'];crop=rendered.crop((box['x0']*300/72,box['y0']*300/72,box['x1']*300/72,box['y1']*300/72))
                crop.save(out/f'p{page["index"]+1}-{k}.png')
                row={'page':page['index']+1,'id':b['id'],'original':b.get('text')}
                regular=reader(crop,use_det=False,use_cls=False)
                from docpipe.stages.recognition_consensus import recognize_with_consensus
                decision=recognize_with_consensus(crop,reader)
                row['consensus']={'text':decision.text,'score':decision.confidence,'agreed':decision.agreed}
                row['regular']={'text':regular.txts,'scores':list(map(float,regular.scores))}
                row['words']=recognize_separated_words(crop,reader)
                mask=np.asarray(crop.convert('L'))<170
                ys,xs=np.where(mask)
                if len(xs):
                    tight=crop.crop((max(0,int(xs.min())-2),max(0,int(ys.min())-2),min(crop.width,int(xs.max())+3),min(crop.height,int(ys.max())+3)))
                    reread=reader(tight,use_det=False,use_cls=False)
                    row['tight']={'text':reread.txts,'scores':list(map(float,reread.scores))};tight.close()
                results.append(row);crop.close()
            rendered.close()
    (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
