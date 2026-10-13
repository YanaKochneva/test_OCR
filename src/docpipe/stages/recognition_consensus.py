"""Bounded image-only retries; accept corroborated text, never a reference."""
from collections import defaultdict
from dataclasses import dataclass
import unicodedata
from PIL import ImageOps


@dataclass(frozen=True)
class RecognitionDecision:
    text: str
    confidence: float
    attempts: int
    agreed: bool


def recognize_with_consensus(image, reader, initial=None):
    def read(img):
        result=reader(img,use_det=False,use_cls=False)
        if not result.txts:return '',0.0
        return unicodedata.normalize('NFC',result.txts[0].strip()),float(result.scores[0])
    primary=initial if initial is not None else read(image)
    if primary[0] and primary[1]>=.96:
        return RecognitionDecision(*primary,1,True)
    # Preserve grayscale detail. Hard binarization may erase thin characters.
    contrasted=ImageOps.autocontrast(image.convert('RGB'),cutoff=0)
    variants=[contrasted,ImageOps.expand(contrasted,border=8,fill='white')]
    candidates=[primary,*[read(v) for v in variants]]
    groups=defaultdict(list)
    for text,score in candidates:
        if text:groups[text].append(score)
    agreed=[(text,sum(scores)/len(scores)) for text,scores in groups.items()
            if len(scores)>=2 and min(scores)>=.85]
    if agreed:
        text,score=max(agreed,key=lambda item:item[1])
        return RecognitionDecision(text,score,3,True)
    # Confidence scores alone do not establish which conflicting spelling is right.
    return RecognitionDecision(*primary,3,False)
