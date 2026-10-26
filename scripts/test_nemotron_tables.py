"""Check cell structure, spans and text retention without model inference."""
from io import BytesIO
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docpipe.nemotron_table_pdf import draw_table
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
import pypdfium2 as pdfium


class TableTests(unittest.TestCase):
    def test_cells_and_spans_keep_text(self):
        fonts = Path(__file__).resolve().parents[1] / "src/docpipe/assets/fonts"
        pdfmetrics.registerFont(TTFont("Nemotron", str(fonts / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("NemotronBold", str(fonts / "DejaVuSans-Bold.ttf")))
        buffer = BytesIO()
        canvas = Canvas(buffer, pagesize=(300, 300))
        result = draw_table(canvas, '<table><tr><th colspan="2">Заголовок</th></tr>'
                            '<tr><td rowspan="2">Объект</td><td>42</td></tr>'
                            '<tr><td>Текст</td></tr></table>', 20, 280, 260, 200)
        canvas.save()
        self.assertEqual(result["rows"], 3)
        self.assertEqual(result["columns"], 2)
        with pdfium.PdfDocument(buffer.getvalue()) as pdf:
            page = pdf[0]
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            for value in ["Заголовок", "Объект", "42", "Текст"]:
                self.assertIn(value, text)
            textpage.close()
            page.close()


if __name__ == "__main__":
    unittest.main()
