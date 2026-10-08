import json
from pathlib import Path
from docpipe.config import AppConfig
from docpipe.pipeline import parse
from docpipe.ir import BBox,BlockType,Block,FigureData
from docpipe.renderers.markdown import render as render_md
from docpipe.renderers.html import render as render_html

def test_fake_pipeline(tmp_path):
 from reportlab.pdfgen import canvas
 pdf=tmp_path/'in.pdf'; c=canvas.Canvas(str(pdf),pagesize=(200,200)); c.drawString(20,170,'Пример текста'); c.showPage(); c.save()
 fix=tmp_path/'fixtures'; fix.mkdir()
 text=Block(id='t1',type=BlockType.TEXT,bbox=BBox(x0=20,y0=20,x1=180,y1=50),order=0,text='Пример текста')
 fig=Block(id='f1',type=BlockType.FIGURE,bbox=BBox(x0=20,y0=70,x1=100,y1=130),order=1,figure=FigureData(file='placeholder.png',format='png',width_px=80,height_px=60))
 (fix/'page_000.json').write_text(json.dumps([text.model_dump(mode='json'),fig.model_dump(mode='json')],ensure_ascii=False),encoding='utf-8')
 doc=parse(pdf,AppConfig(default_dpi=72),engine='fake',fixtures=fix,out_dir=tmp_path/'out')
 assert doc.pages[0].status.value=='ok'
 assert len(doc.pages[0].blocks)==2
 assert doc.pages[0].blocks[1].figure.file=='images/p001_fig01.png'
 assert (tmp_path/'out/images/p001_fig01.png').exists()
 render_md(doc,tmp_path/'out/document.md'); render_html(doc,tmp_path/'out/document.html')
 assert 'images/p001_fig01.png' in (tmp_path/'out/document.md').read_text(encoding='utf-8')
 assert '<figure class="figure-position"' in (tmp_path/'out/document.html').read_text(encoding='utf-8')
