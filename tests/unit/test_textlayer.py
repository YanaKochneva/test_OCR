from docpipe.config import TextLayerConfig
from docpipe.io.textlayer import classify_text
from docpipe.ir import TextLayerStatus

def test_scripts_language_independent():
 c=TextLayerConfig(min_text_length=5)
 assert classify_text('This is an English document.',c)==TextLayerStatus.VALID
 assert classify_text('Это русский документ.',c)==TextLayerStatus.VALID
 assert classify_text('\x01\x02\ufffd\ufffd',c)!=TextLayerStatus.VALID
