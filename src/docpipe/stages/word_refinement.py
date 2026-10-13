"""Read visually separated words when line OCR mixes writing systems."""
import re
import numpy as np
from rapidfuzz.distance import Levenshtein


def is_mixed_word(value):
    return bool(re.search('[A-Za-z]', value) and re.search('[А-Яа-яЁё]', value))


def select_word_result(primary, alternative):
    """Allow a high-confidence Latin reading to resolve mixed-script OCR."""
    if (primary.txts and alternative.txts and is_mixed_word(primary.txts[0])
            and float(alternative.scores[0]) >= .98):
        before, after = primary.txts[0].strip(), alternative.txts[0].strip()
        if (re.fullmatch(r'[A-Za-z]{4,}[.,;:!?]?', after)
                and abs(len(before)-len(after)) <= 2
                and Levenshtein.normalized_similarity(before.casefold(),after.casefold()) >= .4):
            return alternative
    return alternative if alternative.txts and (not primary.txts or alternative.scores[0]>primary.scores[0]) else primary


def recognize_separated_words(image, reader):
    """Independent fallback for low-confidence, widely spaced text lines."""
    mask=np.asarray(image.convert('L'))<170
    mask[:,mask.sum(axis=0)>image.height*.9]=False
    rows=np.flatnonzero(mask.sum(axis=1)>2)
    if not len(rows):return None
    height=int(rows[-1]-rows[0]+1)
    spans=[]
    for x in np.flatnonzero(mask.sum(axis=0)>1):
        if spans and x-spans[-1][1]<=max(4,height*.22):spans[-1][1]=int(x)
        else:spans.append([int(x),int(x)])
    if not 3<=len(spans)<=60:return None
    words=[];scores=[]
    for left,right in spans:
        result=reader(image.crop((max(0,left-3),0,min(image.width,right+4),image.height)),use_det=False,use_cls=False)
        if not result.txts or float(result.scores[0])<.9:return None
        text=result.txts[0].strip()
        if not text:return None
        words.append(text);scores.append(float(result.scores[0]))
    return ' '.join(words),sum(scores)/len(scores)


def refine_mixed_words(image, text, reader):
    words=text.split()
    suspicious=lambda word:bool(
        (re.search('[A-Za-z]',word) and re.search('[А-Яа-яЁё]',word))
    )
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
