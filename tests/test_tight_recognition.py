from PIL import Image,ImageDraw
from docpipe.stages.recognition_consensus import tight_ink_image,corroborates_tight_reading


def test_tight_crop_retains_ink_and_margin():
    image=Image.new('RGB',(120,60),'white')
    ImageDraw.Draw(image).rectangle((10,20,100,40),fill='black')
    tight=tight_ink_image(image)
    assert tight.size==(95,25)
    assert tight.getpixel((2,2))==(0,0,0)
    assert image.size==(120,60)


def test_tight_reading_cannot_change_numbers_or_unrelated_content():
    assert corroborates_tight_reading(('Wordswithout spaces 12',.91),('Words without spaces 12',.99))
    assert not corroborates_tight_reading(('Wordswithout spaces 12',.91),('Words without spaces 13',.99))
    assert not corroborates_tight_reading(('Original sentence 12',.91),('Unrelated content 12',.99))
