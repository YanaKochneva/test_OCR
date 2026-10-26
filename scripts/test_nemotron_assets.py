"""Offline checks for original image extraction and coordinate placement."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pypdfium2 as pdfium
from PIL import Image
from reportlab.pdfgen.canvas import Canvas
from docpipe.nemotron_assets import collect_regions, covered_fraction, save_regions, validate_region


class AssetTests(unittest.TestCase):
    def test_native_image_and_manual_scan_region(self):
        tmp = Path(__file__).resolve().parents[1] / "tmp"
        tmp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp) as directory:
            root = Path(directory)
            with Image.new("RGB", (100, 50), "red") as image:
                image.save(root / "logo.png")
            canvas = Canvas(str(root / "source.pdf"), pagesize=(200, 300))
            canvas.drawImage(str(root / "logo.png"), 20, 240, width=60, height=30)
            canvas.showPage()
            canvas.drawImage(str(root / "logo.png"), 0, 0, width=200, height=300)
            canvas.save()
            with pdfium.PdfDocument(root / "source.pdf") as document:
                page = document[0]
                regions = collect_regions(page, [])
                self.assertEqual(len(regions), 1)
                for actual, expected in zip(regions[0]["bbox"], [.1, .1, .4, .2]):
                    self.assertAlmostEqual(actual, expected, places=5)
                assets = save_regions(page, regions, root / "images", 1, dpi=144)
                self.assertEqual(assets[0]["pixels"], [120, 60])
                with Image.open(root / "images" / assets[0]["file"]) as crop:
                    self.assertEqual(crop.getpixel((60, 30))[:3], (255, 0, 0))
                # PNG crop and placement agree to less than a source pixel.
                self.assertEqual(assets[0]["bbox_points_top_left"], [20, 30, 80, 60])
                self.assertEqual(len(collect_regions(page, [], [{"bbox": [.1, .1, .4, .2]}])), 1)
                page.close()
                page = document[1]
                self.assertEqual(collect_regions(page, []), [])
                manual = collect_regions(page, [], [{"bbox": [.1, .1, .4, .2]}])
                self.assertEqual(len(save_regions(page, manual, root / "images", 2)), 1)
                page.close()

    def test_bad_boxes_and_text_coverage(self):
        for box in ([0, 0, 2, 1], [0, 0, 0, 1], [0, 0, float("nan"), 1]):
            with self.assertRaises(ValueError):
                validate_region({"bbox": box})
        self.assertEqual(covered_fraction([.1, .1, .2, .2], [0, 0, .3, .3]), 1)
        self.assertEqual(covered_fraction([.5, .5, .8, .8], [0, 0, .3, .3]), 0)


if __name__ == "__main__":
    unittest.main()
