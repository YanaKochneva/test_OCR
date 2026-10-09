"""Render current stages using the supplied vertical diagram's visual style."""
from pathlib import Path
import ast
import xml.etree.ElementTree as ET
from html import escape
from PIL import Image, ImageDraw, ImageFont

OUT=Path(__file__).parent
tree=ast.parse((OUT/'build_ocr_diagram.py').read_text(encoding='utf-8'))
nodes=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='nodes' for t in n.targets))
W,H=1540,2780
root=ET.Element('mxfile',host='app.diagrams.net')
di=ET.SubElement(root,'diagram',id='ocr-current-reference',name='Текущий процесс OCR')
model=ET.SubElement(di,'mxGraphModel',grid='1',gridSize='10',page='1',pageWidth=str(W),pageHeight=str(H))
r=ET.SubElement(model,'root')
ET.SubElement(r,'mxCell',id='0'); ET.SubElement(r,'mxCell',id='1',parent='0')
im=Image.new('RGB',(W,H),'white'); d=ImageDraw.Draw(im)
def font(size,bold=False):
    return ImageFont.truetype('C:/Windows/Fonts/'+('arialbd.ttf' if bold else 'arial.ttf'),size)
def wrap(text,f,width):
    lines=[]
    for p in text.split('\n'):
        line=''
        for word in p.split():
            candidate=(line+' '+word).strip()
            if line and d.textlength(candidate,font=f)>width: lines.append(line); line=word
            else: line=candidate
        lines.append(line)
    return lines
def text(text,x,y,width,f,color):
    for line in wrap(text,f,width):
        d.text((x+(width-d.textlength(line,font=f))/2,y),line,font=f,fill=color)
        y+=f.size+6
    return y
def box(cid,title,logic,stack,x,y,w,h,kind='blue'):
    stroke,fill={'blue':('#1991FF','#DDEEFF'),'green':('#5BC47A','#E8F8ED'),'purple':('#A77BDF','#F0E9FB'),'orange':('#F0A33A','#FFF1D9'),'red':('#D9534F','#FDE8E7'),'gray':('#777777','#F0F1F3')}[kind]
    value=f'<b>{escape(title)}</b><br>{escape(logic).replace(chr(10),"<br>")}<br><font color="#243B53"><b>Стек:</b> {escape(stack)}</font>'
    c=ET.SubElement(r,'mxCell',id=cid,value=value,style=f'rounded=1;whiteSpace=wrap;html=1;strokeColor={stroke};fillColor={fill};fontColor=#17304E;strokeWidth=1.5;arcSize=12;fontSize=17;spacing=14;',vertex='1',parent='1')
    ET.SubElement(c,'mxGeometry',x=str(x),y=str(y),width=str(w),height=str(h),attrib={'as':'geometry'})
    d.rounded_rectangle((x,y,x+w,y+h),radius=18,fill=fill,outline=stroke,width=2)
    cy=text(title,x+16,y+14,w-32,font(24 if w>500 else 20,True),'#17304E')
    text(logic,x+20,cy+6,w-40,font(18),'#17304E')
    text('Стек: '+stack,x+20,y+h-32,w-40,font(17,True),'#243B53')
def edge(a,b,y0,y1):
    c=ET.SubElement(r,'mxCell',id='edge'+a,edge='1',parent='1',source=a,target=b,style='edgeStyle=orthogonalEdgeStyle;html=1;endArrow=block;strokeColor=#718096;')
    ET.SubElement(c,'mxGeometry',relative='1',attrib={'as':'geometry'})
    d.line((770,y0,770,y1-6),fill='#718096',width=2)
    d.polygon([(764,y1-12),(776,y1-12),(770,y1-3)],fill='#718096')
text('Актуальный процесс OCR в DocPipe',220,20,1100,font(32,True),'#14233B')
c=ET.SubElement(r,'mxCell',id='title',value='Актуальный процесс OCR в DocPipe',vertex='1',parent='1',style='text;html=1;fontSize=28;fontStyle=1;fontColor=#14233B;align=center;')
ET.SubElement(c,'mxGeometry',x='220',y='20',width='1100',height='50',attrib={'as':'geometry'})
box('source','Входной документ','PDF или изображение; основной путь обработки — Docling.','PDF · PNG · JPG · TIFF и другие изображения',390,85,760,125,'green')
prev='source'; bottom=210
for i,(title,logic,stack) in enumerate(nodes):
    y=235+i*195
    kind='purple' if i in (5,9,10) else 'orange' if i==11 else 'blue'
    box(f'n{i}',f'{i+1}. {title}',logic,stack,360,y,820,170,kind)
    edge(prev,f'n{i}',bottom,y)
    prev=f'n{i}';bottom=y+170
box('outcome','Результат обработки','Собранные документы, извлечённые изображения\nи отчёт автоматических проверок.','Рендереры DocPipe · verification.json',360,2595,820,140,'green')
edge(prev,'outcome',bottom,2595)
box('repeat','Для каждой страницы','Этапы 4–9 повторяются. После обработки страниц собирается общий документ.','Page · Document IR',20,650,310,210,'gray')
box('error','Ошибка страницы','Статус FAILED и предупреждение сохраняются. Остальные страницы могут продолжить обработку.','PageStatus · Python',1210,1000,310,245,'red')
box('adapters','Другие адаптеры','PPStructure и PaddleOCR-VL доступны в коде. PaddleOCR-VL требует отдельного окружения; в текущих тестах использован Docling.','PaddleOCR · PaddlePaddle',20,1070,310,280,'gray')
box('qa','Границы контроля','Успешные проверки не гарантируют полного визуального совпадения. Для оценки нужны эталон и сравнение вёрстки.','verify · benchmark',1210,2240,310,265,'orange')
ET.indent(root)
ET.ElementTree(root).write(OUT/'ocr-process-current-reference-style.drawio',encoding='utf-8',xml_declaration=True)
im.save(OUT/'ocr-process-current-reference-style.png')
