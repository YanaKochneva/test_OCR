from docpipe.ir import BBox
from docpipe.stages.geometry import bottom_left_to_top_left,scale_bbox,rotate_bbox

def test_geometry():
 b=BBox(x0=10,y0=20,x1=30,y1=40)
 assert bottom_left_to_top_left(b,100)==BBox(x0=10,y0=60,x1=30,y1=80)
 assert scale_bbox(b,2,3)==BBox(x0=20,y0=60,x1=60,y1=120)
 assert rotate_bbox(b,100,100,180)==BBox(x0=70,y0=60,x1=90,y1=80)
