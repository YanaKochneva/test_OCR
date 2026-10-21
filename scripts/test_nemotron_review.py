"""Offline integration checks: review routing, crop cache, and guarded corrections."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from PIL import Image
from docpipe.config import AppConfig, ReviewConfig
from docpipe.ir import BBox, Block, Document, EngineInfo, Page, SourceInfo
from docpipe.review import review_after_render, review_before_render


class ReviewTests(unittest.TestCase):
    def test_nemotron_routing_and_cache(self):
        text = "Документ содержит достаточно текста для проверки распознавания."
        box = BBox(x0=0, y0=0, x1=72, y1=72)
        document = Document(source=SourceInfo(file="unused.pdf", sha256="test", type="pdf"),
                            engine=EngineInfo(name="docling", version="test"),
                            pages=[Page(index=0, width_pt=72, height_pt=72, rotation=0,
                                        raster_dpi=72, size_px=(72, 72), status="ok", text_layer="absent",
                                        blocks=[Block(id="b1", type="text", bbox=box, order=0, text=text)])])
        config = AppConfig(review=ReviewConfig(llm_enabled=True, provider="nemotron",
                                               api_model="nvidia/Nemotron-3.5-Parse"))
        calls = []
        reader = SimpleNamespace(recognize=lambda crop, timeout: calls.append(crop.size) or text)
        root = Path(__file__).resolve().parents[1] / "reports" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder, patch(
            "docpipe.glm_refinement._page_image", side_effect=lambda *args: Image.new("RGB", (72, 72))
        ), patch("docpipe.glm_refinement.russian_dictionary", return_value=SimpleNamespace(word_is_known=lambda word: True)):
            result, report = review_before_render(document, config, Path(folder), client=reader)
            self.assertEqual(result.pages[0].blocks[0].text, text)
            self.assertEqual(report["calls"], 1)
            self.assertEqual(report["status"], "completed")
            self.assertTrue((Path(folder) / "review-nemotron-reread.json").exists())
            self.assertFalse((Path(folder) / "review-glm-reread.json").exists())
            _, cached = review_before_render(document, config, Path(folder), client=reader)
            self.assertEqual(cached["calls"], 0)
            self.assertEqual(len(calls), 1)
            with patch("docpipe.review._client", side_effect=AssertionError("Unexpected chat transport")):
                exported = review_after_render(document, config, Path(folder))
            self.assertEqual(exported["mode"], "deterministic_export_check_after_nemotron_reread")
            self.assertEqual(exported["calls"], 0)

    def test_disagreement_cannot_replace_whole_text(self):
        from docpipe.glm_refinement import proposals
        edits, rejection = proposals("Проверенный текст исходного документа", "Другой ответ", known_word=lambda word: True)
        self.assertEqual(edits, [])
        self.assertIsNotNone(rejection)


if __name__ == "__main__":
    unittest.main()
