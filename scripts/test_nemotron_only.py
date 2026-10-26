"""Offline contract checks: python scripts/test_nemotron_only.py."""
from pathlib import Path
import sys
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pypdfium2 as pdfium
import torch
from PIL import Image

from docpipe.nemotron_only import page_images, recognize, write_response_pdf

TEST_TMP = Path(__file__).resolve().parents[1] / "tmp"
TEST_TMP.mkdir(exist_ok=True)
tempfile.tempdir = str(TEST_TMP)


class DirectNemotronTests(unittest.TestCase):
    def test_raw_pdf_preserves_cyrillic_and_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "response.pdf"
            write_response_pdf(["<s>Привет, мир</s>\n<table>42</table>", "Вторая страница"], output)
            with pdfium.PdfDocument(output) as pdf:
                self.assertEqual(len(pdf), 2)
                page = pdf[0]
                textpage = page.get_textpage()
                text = textpage.get_text_range()
                self.assertIn("<s>Привет, мир</s>", text)
                self.assertIn("<table>42</table>", text)
                textpage.close()
                page.close()
            images = list(page_images(output, 72))
            self.assertEqual(len(images), 2)
            for image in images:
                image.close()

    def test_one_call_per_full_page_without_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.tiff"
            with Image.new("RGB", (64, 96), "white") as image:
                image.save(source, save_all=True, append_images=[image])
            from unittest.mock import MagicMock
            model = MagicMock()
            model.to.return_value.eval.return_value = model
            model.generate.return_value = torch.tensor([[5, 2]])
            processor = MagicMock()
            processor.return_value.to.return_value = {}
            processor.batch_decode.return_value = ["<s>Текст</s>"]
            generation = SimpleNamespace(eos_token_id=2)
            with patch("transformers.AutoModel.from_pretrained", return_value=model), \
                 patch("transformers.AutoProcessor.from_pretrained", return_value=processor), \
                 patch("transformers.GenerationConfig.from_pretrained", return_value=generation):
                output = recognize(source, root / "out.pdf", root, device="cpu")
                self.assertEqual(model.generate.call_count, 2)
                self.assertEqual(processor.call_args.kwargs["images"][0].size, (64, 96))
                self.assertTrue(output.is_file())
                saved = json.loads(output.with_suffix(".raw.json").read_text(encoding="utf-8"))
                self.assertTrue(saved["complete"])
                self.assertEqual(saved["pages"], ["<s>Текст</s>", "<s>Текст</s>"])
                model.generate.return_value = torch.tensor([[5, 5, 5]])
                with self.assertRaisesRegex(RuntimeError, "token limit"):
                    recognize(source, root / "truncated.pdf", root, device="cpu", max_new_tokens=3)
                self.assertFalse((root / "truncated.pdf").exists())

    def test_reject_source_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdf"
            source.touch()
            with self.assertRaisesRegex(ValueError, "differ"):
                recognize(source, source, Path(directory))


if __name__ == "__main__":
    unittest.main()
