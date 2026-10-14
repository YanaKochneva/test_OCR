import pytest
from PIL import Image,ImageDraw,ImageFont

from docpipe.engines.base import PageMeta
from docpipe.ir import BBox,Line
from docpipe.renderers.fonts import font_path
from docpipe.stages.font_matching import fit_font


@pytest.mark.parametrize('family,bold,italic',[
    ('Arial',False,False),('Arial',True,False),('Arial',False,True),
    ('Times New Roman',True,False),('Calibri',False,False)])
def test_matching_recovers_font_and_variant_from_visible_glyphs(family,bold,italic):
    path=font_path(family,bold,italic)
    if path is None:
        pytest.skip('Font is not installed')
    text='Ресурсы модели 123.45'
    font=ImageFont.truetype(str(path),48)
    image=Image.new('RGB',(800,120),'white')
    draw=ImageDraw.Draw(image)
    draw.text((20,80),text,font=font,fill='black',anchor='ls')
    box=draw.textbbox((20,80),text,font=font,anchor='ls')
    line=Line(text=text,bbox=BBox(x0=box[0]/2,y0=box[1]/2,x1=box[2]/2,y1=box[3]/2))
    result=fit_font(line,image,PageMeta(0,400,60,0,144))
    assert result.font_family==family
    assert result.bold==bold
    assert result.italic==italic
    assert abs(result.font_size_pt-24)<1.5
    assert result.text==text
    assert result.bbox==line.bbox


def test_inline_emphasis_is_recovered_without_changing_words():
    from docpipe.stages.font_matching import _fit_word_runs
    image=Image.new('RGB',(1000,120),'white')
    draw=ImageDraw.Draw(image)
    x=20
    boxes=[]
    for word,bold,italic in [('обычный',False,False),('жирный',True,False),('курсив',False,True)]:
        font=ImageFont.truetype(str(font_path('Arial',bold,italic)),48)
        draw.text((x,80),word,font=font,fill='black',anchor='ls')
        boxes.append(draw.textbbox((x,80),word,font=font,anchor='ls'))
        x+=font.getlength(word)+25
    line=Line(text='обычный жирный курсив',font_family='Arial',font_size_pt=24,
        bbox=BBox(x0=boxes[0][0]/2,y0=min(b[1] for b in boxes)/2,
                  x1=boxes[-1][2]/2,y1=max(b[3] for b in boxes)/2))
    result=_fit_word_runs(line,image,PageMeta(0,500,60,0,144))
    assert result.runs
    assert [(r.bold,r.italic) for r in result.runs]==[(False,False),(True,False),(False,True)]
    assert ' '.join(r.text for r in result.runs)==line.text


def test_visual_ocr_line_alignment_keeps_original_text():
    from docpipe.stages.table_visual_layout import split_using_visual_ocr
    original='the original words contain one typo'
    readings=['the original words','contain one type']
    result=split_using_visual_ocr(original,readings)
    assert result==['the original words','contain one typo']
    assert ' '.join(result)==original


def test_matching_keeps_foreground_color():
    text='Цветной текст'
    font=ImageFont.truetype(str(font_path('Arial')),48)
    image=Image.new('RGB',(800,120),'white')
    draw=ImageDraw.Draw(image)
    draw.text((20,80),text,font=font,fill='#003cb4',anchor='ls')
    box=draw.textbbox((20,80),text,font=font,anchor='ls')
    line=Line(text=text,bbox=BBox(x0=box[0]/2,y0=box[1]/2,x1=box[2]/2,y1=box[3]/2))
    result=fit_font(line,image,PageMeta(0,400,60,0,144))
    assert result.color=='#003cb4'


def test_graphic_width_does_not_stretch_letters():
    from docpipe.stages.font_matching import guard_line_width
    line=Line(text='Short text',font_family='Arial',font_size_pt=11,
              bbox=BBox(x0=10,y0=100,x1=400,y1=113))
    result=guard_line_width(line)
    assert result.horizontal_scale_pct==100
    assert result.review_required
    assert result.text==line.text
