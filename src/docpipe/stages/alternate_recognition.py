"""Visual corroboration of low-confidence table lines by another language head."""
import re
from PIL import ImageOps
from rapidfuzz.distance import Levenshtein


def recover_uncertain_line(image, primary, reader):
    text,confidence=primary
    if confidence>=.85 or len(text)>500:return None
    def read(raster):
        result=reader(raster,use_det=False,use_cls=False)
        if not result.txts:return '',0.
        return result.txts[0].strip(),float(result.scores[0])
    candidate,score=read(image)
    if not candidate:return None
    # Existing numeric evidence must not be silently replaced.
    numbers=lambda value:re.findall(r'\d+(?:[.,]\d+)*',value)
    if numbers(text)!=numbers(candidate):return None
    normal=lambda value:' '.join(value.casefold().split())
    if (confidence>=.65 and score>=.65 and normal(text)==normal(candidate)
            and len(text.split())>3 and sum(c.isalpha() for c in text)>=12):
        # Corroboration may recover a line without manufacturing confidence.
        return text,confidence
    if score<.92:return None
    if confidence>=.6 and Levenshtein.normalized_similarity(normal(text),normal(candidate))>=.9:
        return candidate,score
    other,other_score=read(ImageOps.autocontrast(ImageOps.expand(image,border=8,fill='white')))
    if candidate==other and other_score>=.92:
        return candidate,min(score,other_score)
    return None
