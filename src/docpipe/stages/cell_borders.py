"""Erase only long rule fragments at the perimeter of a proven full-cell crop."""
import cv2
import numpy as np
from PIL import Image


def clean_proven_cell_borders(image):
    gray=np.asarray(image.convert('L'));h,w=gray.shape
    if min(h,w)<16:return image
    ink=(cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY_INV,31,12)>0).astype('uint8')
    horizontal=cv2.morphologyEx(cv2.dilate(ink,np.ones((7,1),np.uint8)),cv2.MORPH_OPEN,np.ones((1,max(20,w//3)),np.uint8))
    vertical=cv2.morphologyEx(cv2.dilate(ink,np.ones((1,5),np.uint8)),cv2.MORPH_OPEN,np.ones((max(20,h//3),1),np.uint8))
    margin_y=min(8,h//5);margin_x=min(8,w//5)
    horizontal[margin_y:-margin_y,:]=0
    vertical[:,margin_x:-margin_x]=0
    remove=((horizontal|vertical)>0)&(ink>0)
    pixels=np.asarray(image.convert('RGB')).copy();pixels[remove]=255
    return Image.fromarray(pixels)
