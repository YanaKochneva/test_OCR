"""Read visually separated words when line OCR mixes writing systems."""
import re
import numpy as np
from rapidfuzz.distance import Levenshtein


def refine_mixed_words(image, text, reader):
    words=text.split()
    suspicious=lambda word:bool(re.search('[A-Za-z]',word) and re.search('[А-Яа-яЁё]',word))
    if '\\' in text or not any(suspicious(w) for w in words):return text
    mask=np.asarray(image.convert('L'))<170
    mask[:,mask.sum(axis=0)>image.height*.9]=False
    rows=np.flatnonzero(mask.sum(axis=1)>2)
    if not len(rows):return text
    height=int(rows[-1]-rows[0]+1)
    active=np.flatnonzero(mask.sum(axis=0)>1)
    spans=[]
    for x in active:
        if spans and x-spans[-1][1]<=max(4,height*.22):spans[-1][1]=int(x)
        else:spans.append([int(x),int(x)])
    if not spans:return text
    for index,(left,right) in enumerate(spans):
        if index>=len(words):break
        if not suspicious(words[index]):continue
        if len(spans)!=len(words) and index!=0:continue
        result=reader(image.crop((max(0,left-3),0,min(image.width,right+4),image.height)),use_det=False,use_cls=False)
        if not result.txts or float(result.scores[0])<.98:continue
        candidate=result.txts[0].strip()
        if candidate and ' ' not in candidate and abs(len(candidate)-len(words[index]))<=2 and Levenshtein.normalized_similarity(words[index].casefold(),candidate.casefold())>=.2:
            words[index]=candidate
    return ' '.join(words)
