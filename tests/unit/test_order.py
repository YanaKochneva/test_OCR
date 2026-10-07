from docpipe.ir import Block,BBox,BlockType
from docpipe.stages.order import assign_order

def b(i,x,y): return Block(id=i,type=BlockType.TEXT,bbox=BBox(x0=x,y0=y,x1=x+50,y1=y+10),order=0,text=i)
def test_two_columns():
 out=assign_order([b('r2',100,30),b('l2',0,30),b('r1',100,0),b('l1',0,0)],200)
 assert [x.id for x in out]==['l1','l2','r1','r2']
 assert [x.order for x in out]==[0,1,2,3]
