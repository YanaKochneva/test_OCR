"""Recognize path components separated by measured diagonal ink strokes."""
import numpy as np
import cv2


def recognize_path_components(image, reader):
    mask=(np.asarray(image.convert('L'))<170).astype('uint8')
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask,8)
    separators=[]
    for label in range(1,count):
        x,y,width,height,area=stats[label]
        if height<16 or width<5 or width>height*.7:continue
        yy,xx=np.where(labels==label)
        slope,intercept=np.polyfit(yy,xx,1)
        residual=np.sqrt(np.mean((xx-(slope*yy+intercept))**2))
        if .1<slope<.65 and residual<1.8:separators.append((int(x),int(x+width)))
    if len(separators)<2:return None
    output=[];scores=[];left=0
    for right,end in sorted(separators)+[(image.width,image.width)]:
        region=mask[:,left:right]
        if right-left>8 and region.sum()>20:
            result=reader(image.crop((left,0,right,image.height)),use_det=False,use_cls=False)
            if not result.txts:return None
            text=result.txts[0].strip();score=float(result.scores[0])
            if not text or score<.9:return None
            output.append(text);scores.append(score)
        if end!=image.width:output.append('\\')
        left=end
    text=''.join(output)
    if not scores or len(text)<8:return None
    return text,min(scores)
