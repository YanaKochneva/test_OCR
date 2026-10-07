from PIL import Image
from docpipe.stages.figures import PixelBox,filter_boxes
from docpipe.config import FigureConfig

def test_filter():
 cfg=FigureConfig(min_width_px=10,min_height_px=10,min_area_ratio=.01)
 assert len(filter_boxes([PixelBox(0,0,2,2),PixelBox(10,10,60,60)],(100,100),cfg,3))==1
