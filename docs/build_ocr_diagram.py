"""Generate the current file-processing diagram as editable draw.io and PNG."""
from pathlib import Path
from html import escape
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
W, H = 1400, 3020
nodes = [
    ('Приём и проверка файла', 'Проверяем наличие, размер и возможность открыть файл.', 'Python · pathlib · лимиты AppConfig'),
    ('Нормализация входа', 'PDF сохраняется; изображения и их кадры переводятся в PDF.', 'Pillow · img2pdf'),
    ('Анализ PDF и текстового слоя', 'Проверяем страницы и качество существующего текста.\nВ текущем пути Docling страницы всё равно проходят OCR.', 'pypdfium2 · docpipe.io.textlayer'),
    ('Растрирование страниц', 'Получаем изображение каждой страницы, обычно при 300 DPI.\nСохраняем размеры страницы для пересчёта координат.', 'pypdfium2 · Pillow'),
    ('Распознавание и анализ структуры', 'Находим текст, заголовки, оглавление, таблицы и иллюстрации.\nРаспознаём русский текст и структуру ячеек.', 'Docling · RapidOCR / PP-OCRv5 eslav · TableFormer'),
    ('Единое представление страницы', 'Переводим результат в блоки с текстом и координатами.\nСохраняем строки, столбцы и объединения ячеек.', 'Адаптер Docling · IR · Pydantic'),
    ('Восстановление пропущенного текста', 'Собираем строки оглавления; добавляем уверенные OCR-строки,\nне покрытые существующими блоками.', 'document_index · parsed OCR cells · ocr_recovery'),
    ('Извлечение иллюстраций', 'Объединяем и фильтруем области изображений.\nВырезаем иллюстрации и сохраняем файлы для экспорта.', 'Pillow · docpipe.stages.figures'),
    ('Порядок чтения и очистка', 'Упорядочиваем блоки по координатам, связываем подписи.\nОчищаем текст; обрабатываем колонтитулы по настройкам.', 'assign_order · attach_captions · postprocess'),
    ('Сборка документа и размещение', 'Объединяем обработанные страницы в IR документа.\nДля позиционных форматов рассчитываем переносы,\nвысоты таблиц и смещения при пересечениях текста.', 'Document IR · text_layout · table_layout'),
    ('Экспорт результата', 'Создаём DOCX, HTML, Markdown, JSON и варианты PDF.\nВ DOCX / HTML — текст, таблицы и встроенные изображения.', 'python-docx · HTML/CSS · ReportLab · pikepdf'),
    ('Проверка результата', 'Проверяем текст, оглавление, таблицы, изображения и файлы.\nОшибки проверки не засчитываются как успешный результат.', 'docpipe.verify · checks'),
]
root = ET.Element('mxfile', host='app.diagrams.net')
diagram = ET.SubElement(root, 'diagram', id='current-ocr', name='Обработка файла — текущая архитектура')
model = ET.SubElement(diagram, 'mxGraphModel', grid='1', gridSize='10', page='1', pageWidth=str(W), pageHeight=str(H))
r = ET.SubElement(model, 'root')
ET.SubElement(r, 'mxCell', id='0')
ET.SubElement(r, 'mxCell', id='1', parent='0')
im = Image.new('RGB', (W, H), '#F4F7FC')
d = ImageDraw.Draw(im)
fontdir = Path('C:/Windows/Fonts')
def font(size, bold=False):
    return ImageFont.truetype(str(fontdir / ('arialbd.ttf' if bold else 'arial.ttf')), size)
def centered(text, y, f, color):
    for line in text.split('\n'):
        d.text(((W-d.textlength(line,font=f))/2,y),line,font=f,fill=color)
        y += f.size + 7
def cell(cid, value, x, y, width, height, style):
    c=ET.SubElement(r,'mxCell',id=cid,value=value,style=style,vertex='1',parent='1')
    ET.SubElement(c,'mxGeometry',x=str(x),y=str(y),width=str(width),height=str(height),attrib={'as':'geometry'})
title='Как файл становится документом'
centered('OCR / ОБРАБОТКА ДОКУМЕНТОВ',30,font(18,True),'#52718F')
centered(title,65,font(40,True),'#14233B')
centered('Текущий путь Docling: от входного файла до проверенного результата',125,font(22),'#55708F')
cell('title',title,100,55,1200,55,'text;html=1;fontSize=32;fontStyle=1;fontColor=#14233B;align=center;')
cell('subtitle','Текущий путь Docling: от входного файла до проверенного результата',100,120,1200,40,'text;html=1;fontSize=18;fontColor=#55708F;align=center;')
groups=[(0,4,'01 / ПОДГОТОВКА ВХОДА','#397BD5','#EBF2FC'),(4,5,'02 / РАСПОЗНАВАНИЕ СТРАНИЦ','#138B7B','#E9F5F2'),(9,3,'03 / СБОРКА И КОНТРОЛЬ','#8463BA','#F1EDF8')]
ys={}
y=235
for start,count,label,color,bg in groups:
    height=65+count*200
    d.rounded_rectangle((85,y-25,1315,y+height-15),radius=28,fill=bg)
    d.text((130,y),label,font=font(20,True),fill=color)
    cell(f'group{start}','',85,y-25,1230,height+10,f'rounded=1;arcSize=6;fillColor={bg};strokeColor=none;')
    cell(f'label{start}',label,130,y,1000,35,f'text;html=1;align=left;fontSize=18;fontStyle=1;fontColor={color};')
    for j in range(count): ys[start+j]=(y+55+j*200,color)
    y+=height+50
for i,(name,logic,stack) in enumerate(nodes):
    y,color=ys[i]
    d.rounded_rectangle((145,y+5,1260,y+175),radius=20,fill='#DEE6EF')
    d.rounded_rectangle((140,y,1260,y+170),radius=20,fill='white',outline='#D3DEE9',width=1)
    d.rounded_rectangle((160,y+18,216,y+66),radius=12,fill=color)
    d.text((171,y+28),f'{i+1:02}',font=font(24,True),fill='white')
    d.text((235,y+21),name,font=font(27,True),fill='#17304E')
    for li,line in enumerate(logic.split('\n')): d.text((235,y+61+li*25),line,font=font(21),fill='#55708F')
    d.line((235,y+140,1225,y+140),fill='#E5EDF3',width=1)
    d.text((235,y+147),stack,font=font(17,True),fill=color)
    cell(f'n{i}','',140,y,1120,170,'rounded=1;arcSize=12;html=1;fillColor=#FFFFFF;strokeColor=#D3DEE9;shadow=1;')
    cell(f'num{i}',f'{i+1:02}',160,y+18,56,48,f'rounded=1;arcSize=24;html=1;fillColor={color};strokeColor=none;fontColor=#FFFFFF;fontSize=22;fontStyle=1;')
    cell(f'heading{i}',escape(name),235,y+16,980,38,'text;html=1;align=left;fontSize=24;fontStyle=1;fontColor=#17304E;')
    cell(f'logic{i}',escape(logic).replace('\n','<br>'),235,y+57,990,78,'text;html=1;align=left;verticalAlign=top;fontSize=18;fontColor=#55708F;')
    cell(f'stack{i}',escape(stack),235,y+142,990,25,f'text;html=1;align=left;fontSize=15;fontStyle=1;fontColor={color};')
    if i:
        e=ET.SubElement(r,'mxCell',id=f'e{i}',style='edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;strokeColor=#6C8FB3;strokeWidth=2;',edge='1',parent='1',source=f'n{i-1}',target=f'n{i}')
        ET.SubElement(e,'mxGeometry',relative='1',attrib={'as':'geometry'})
        # Section headings separate the three phases; arrows connect cards within phases.
        if i not in (4,9):
            d.line((700,y-25,700,y-8),fill='#91A8BD',width=2)
            d.polygon([(695,y-13),(705,y-13),(700,y-5)],fill='#91A8BD')
note='Этапы 4–9 повторяются для каждой страницы. Проверки не гарантируют полного визуального совпадения.\nDOCX-эталон используется только для оценки; исходная страница не служит фоном DOCX / HTML.'
centered(note,2910,font(18),'#55708F')
cell('note',escape(note).replace('\n','<br>'),80,2900,1240,90,'text;html=1;fontSize=14;fontColor=#55708F;align=center;')
ET.indent(root)
ET.ElementTree(root).write(OUT/'ocr-process-current.drawio',encoding='utf-8',xml_declaration=True)
im.save(OUT/'ocr-process-current.png')
print('Created ocr-process-current.drawio and ocr-process-current.png')
