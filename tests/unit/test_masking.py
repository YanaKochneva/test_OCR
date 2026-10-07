from PIL import Image
from docpipe.stages.figures import PixelBox
from docpipe.stages.masking import mask_figures

def test_mask_does_not_change_outside():
 im=Image.new('RGB',(20,20),(1,2,3)); out=mask_figures(im,[PixelBox(5,5,10,10)])
 assert out.getpixel((0,0))==(1,2,3)
 assert out.getpixel((7,7))==(255,255,255)
