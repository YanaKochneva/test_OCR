from PIL import Image,ImageDraw
from types import SimpleNamespace
from docpipe.stages.path_recognition import recognize_path_components


def test_no_separators_keeps_original_recognition():
    image=Image.new('RGB',(200,60),'white')
    assert recognize_path_components(image,lambda *a,**k:None) is None


def test_low_confidence_component_is_rejected():
    image=Image.new('RGB',(200,60),'white');draw=ImageDraw.Draw(image)
    draw.line((10,10,20,45),fill='black',width=2)
    draw.line((80,10,90,45),fill='black',width=2)
    draw.rectangle((25,15,65,40),fill='black')
    reader=lambda *a,**k:SimpleNamespace(txts=['guess'],scores=[.5])
    assert recognize_path_components(image,reader) is None
