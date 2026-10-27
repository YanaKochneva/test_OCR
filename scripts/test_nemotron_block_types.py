"""Regression checks for tables mislabeled as images, independent of page IDs."""
from pathlib import Path
import sys
import unittest
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docpipe.nemotron_block_types import resolve_block_type, source_column_widths
from docpipe.nemotron_assets import collect_regions
from bs4 import BeautifulSoup


def block(text, cls="Picture"):
    return {"class": cls, "text": text, "bbox": [.2, .2, .8, .5]}


class BlockTypeTests(unittest.TestCase):
    def test_complete_tables_override_generic_image_labels(self):
        cases = [r"\begin{tabular}{cc}" + "\nA & B\\\\\n1 & 2\\\\\n" + r"\end{tabular}",
                 "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
                 "| A | B |\n| --- | --- |\n| 1 | 2 |"]
        for cls in ("Picture", "Figure", "Image", "Graphic", "Text"):
            for text in cases:
                with self.subTest(cls=cls, text=text):
                    result = resolve_block_type(block(text, cls))
                    self.assertEqual(result["class"], "Table")
                    self.assertEqual(result["original_class"], cls)

    def test_incomplete_column_preserves_every_fragment_in_anchored_column(self):
        raw = (r"\begin{tabular}{cc}" + "\nType & Requirements\\\\\n"
               r" & \\server\files\_model" + "\\\\\n"
               "– first requirement & \\\\\n"
               "– second requirement\\\\\n"
               "– final requirement")
        result = resolve_block_type(block(raw))
        self.assertEqual(result["class"], "Table")
        self.assertEqual(result["table_repairs"], ["column_continuation"])
        soup = BeautifulSoup(result["text"], "html.parser")
        rows = soup.find_all("tr")
        self.assertEqual(len(rows), 2)
        cells = rows[1].find_all("td")
        self.assertEqual(cells[0].get_text(), "")
        for text in [r"\\server\files\_model", "first requirement", "second requirement", "final requirement"]:
            self.assertIn(text, cells[1].get_text())
        # Resolved types are stable when passed through the asset collector.
        self.assertEqual(resolve_block_type(result), result)

    def test_regular_truncated_table_only_closes_environment(self):
        raw = r"\begin{tabular}{cc}" + "\nName & Value\\\\\nAlpha & 42"
        result = resolve_block_type(block(raw))
        self.assertEqual(result["table_repairs"], ["closed_table_environment"])
        self.assertTrue(result["text"].endswith(r"\end{tabular}"))

    def test_real_figures_charts_and_mentions_are_not_promoted(self):
        values = ["ПОЛЮС МАГАДАН СУХОЙ ЛОГ год Объект МЕСЯЦ",
                  "Flowchart: A → B → C", "Example: \\begin{tabular}{cc}\nA & B\\\\\n1 & 2",
                  r"\begin{tabular}{cc}A & B"]
        for value in values:
            self.assertEqual(resolve_block_type(block(value))["class"], "Picture")
        for cls in ["Chart", "Logo"]:
            value = block("<table><tr><td>A</td></tr><tr><td>1</td></tr></table>", cls)
            self.assertEqual(resolve_block_type(value), value)

    def test_native_raster_layer_cannot_mask_recovered_table(self):
        class NativeImage:
            type = 3
            def get_bounds(self):
                return (20, 50, 80, 80)
        class Page:
            def get_size(self):
                return (100, 100)
            def get_objects(self):
                return [NativeImage()]
        table = block("<table><tr><td>A</td></tr><tr><td>42</td></tr></table>")
        self.assertEqual(collect_regions(Page(), [table]), [])
        self.assertEqual(len(collect_regions(Page(), [block("Flowchart A → B")])), 1)

    def test_column_geometry_comes_from_source_rules(self):
        from reportlab.pdfgen.canvas import Canvas
        import pypdfium2 as pdfium
        buffer = BytesIO()
        canvas = Canvas(buffer, pagesize=(100, 150))
        for x in (10, 25, 90):
            canvas.line(x, 10, x, 140)
        canvas.save()
        with pdfium.PdfDocument(buffer.getvalue()) as pdf:
            page = pdf[0]
            widths = source_column_widths(page, [.1, 10/150, .9, 140/150], 2)
            self.assertAlmostEqual(widths[0], 15/80, delta=.01)
            self.assertAlmostEqual(sum(widths), 1)
            self.assertIsNone(source_column_widths(page, [.1, 10/150, .9, 140/150], 3))
            page.close()


if __name__ == "__main__":
    unittest.main()
