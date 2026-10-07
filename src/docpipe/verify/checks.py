from __future__ import annotations
import re
from pathlib import Path
from docpipe.ir import Document,BlockType

def words(text:str)->list[str]: return re.findall(r"\w+",text.lower().replace('ё','е'),flags=re.UNICODE)
def ir_words(doc:Document)->list[str]: return words(' '.join(b.text or '' for p in doc.pages for b in sorted(p.blocks,key=lambda x:x.order)))
def text_match(doc:Document, text:str)->dict:
    a=ir_words(doc); b=words(text); sa=set(a); sb=set(b)
    missing=[w for w in a if w not in sb]; extra=[w for w in b if w not in sa]
    return {'precision':len([w for w in b if w in sa])/max(1,len(b)),'recall':len([w for w in a if w in sb])/max(1,len(a)),'missing':missing[:50],'extra':extra[:50]}

def figure_in_flow(doc:Document, output_text:str)->dict:
    figures=[b for p in doc.pages for b in p.blocks if b.type==BlockType.FIGURE]
    counts=[]
    for f in figures:
        name=f.figure.file if f.figure else ''
        counts.append({'file':name,'count':output_text.count(name)})
    return {'ok':all(x['count']==1 for x in counts),'figures':counts}
