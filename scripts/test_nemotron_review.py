"""Offline integration checks: review routing, crop cache, and guarded corrections."""
import sys
import json
import os
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
    def test_local_provider_and_removed_transport(self):
        from pydantic import ValidationError

        self.assertEqual(ReviewConfig().provider, "nemotron")
        with self.assertRaises(ValidationError):
            ReviewConfig(provider="ollama")
        with self.assertRaises(ValidationError):
            ReviewConfig(ollama_url="http://localhost:11434")

    def test_explicit_local_ocr_models(self):
        from docpipe.engines.russian_recognition import recognition_params
        from docpipe.errors import ModelError

        root = Path(__file__).resolve().parents[1] / "reports" / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder:
            folder = Path(folder)
            for name in ("model.onnx", "rec_keys.txt", "ch_PP-OCRv5_det_mobile.onnx",
                         "ch_ppocr_mobile_v2.0_cls_mobile.onnx"):
                (folder / name).touch()
            with patch.dict(os.environ, {"DOCPIPE_RU_REC_MODEL_DIR": str(folder),
                                         "DOCPIPE_RAPIDOCR_MODEL_DIR": str(folder)}):
                params = recognition_params()
                self.assertEqual(params["Global.model_root_dir"], str(folder.resolve()))
                self.assertEqual(params["Det.model_path"], str((folder / "ch_PP-OCRv5_det_mobile.onnx").resolve()))
                from docling.datamodel.pipeline_options import RapidOcrOptions
                from docpipe.engines.docling_engine import DoclingEngine
                from docpipe.engines.safe_rapidocr import RapidOCR

                options = DoclingEngine()._make_rapidocr_options(RapidOcrOptions)
                self.assertEqual(str(options.det_model_path), params["Det.model_path"])
                self.assertEqual(str(options.cls_model_path), params["Cls.model_path"])
                with patch("rapidocr.RapidOCR") as backend:
                    RapidOCR(params={})
                    self.assertEqual(backend.call_args.kwargs["params"]["Global.model_root_dir"],
                                     str(folder.resolve()))
                (folder / "ch_PP-OCRv5_det_mobile.onnx").unlink()
                with self.assertRaises(ModelError):
                    recognition_params()

    def test_notebook_uses_kernel_and_local_models(self):
        from types import ModuleType

        root = Path(__file__).resolve().parents[1]
        notebook = json.loads((root / "process_documents.ipynb").read_text(encoding="utf-8"))
        code = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
        display = ModuleType("IPython.display")
        for name in ("display", "HTML", "Markdown", "FileLink"):
            setattr(display, name, lambda *args: None)
        temporary = root / "reports" / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary) as folder, patch.dict(sys.modules, {
            "IPython": ModuleType("IPython"), "IPython.display": display,
        }), patch("pathlib.Path.cwd", return_value=root):
            state = {}
            exec(compile(code[0], "notebook-setup", "exec"), state)
            self.assertEqual(state["PYTHON"], Path(sys.executable))
            settings = code[1].replace('INPUTS = [PROJECT_ROOT / "data" / "input"]',
                                       'INPUTS = [PROJECT_ROOT / "README.md"]')
            exec(compile(settings, "notebook-settings", "exec"), state)
            folder = Path(folder)
            for name in ("MODELS_DIR", "DOCLING_MODELS", "RAPIDOCR_MODELS", "RUSSIAN_MODEL", "NEMOTRON_MODEL"):
                state[name] = folder
            for name in ("model.onnx", "rec_keys.txt", "model.safetensors", "config.json",
                         "hf_nemotron_parse_modeling.py", "hf_nemotron_parse_processor.py",
                         "ch_PP-OCRv5_det_mobile.onnx", "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                         "eslav_PP-OCRv5_rec_mobile.onnx", "latin_PP-OCRv5_rec_mobile.onnx"):
                (folder / name).touch()
            (folder / "nemotron_parse").mkdir()
            (folder / "nemotron_parse" / "__init__.py").touch()
            with patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stdout='{"checks":[]}')) as run:
                with patch.dict(os.environ, {"DOCPIPE_REVIEW__OLLAMA_URL": "http://obsolete"}):
                    exec(compile(code[2], "notebook-preflight", "exec"), state)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(state["env"]["DOCPIPE_REVIEW__PROVIDER"], "nemotron")
                self.assertEqual(state["env"]["DOCPIPE_REVIEW__NEMOTRON_MODEL"], str(folder))
                self.assertNotIn("DOCPIPE_REVIEW__OLLAMA_URL", state["env"])
                (folder / "model.safetensors").unlink()
                run.reset_mock()
                with self.assertRaises(FileNotFoundError):
                    exec(compile(code[2], "notebook-missing-model", "exec"), state)
                run.assert_not_called()

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
            "docpipe.nemotron_refinement._page_image", side_effect=lambda *args: Image.new("RGB", (72, 72))
        ), patch("docpipe.nemotron_refinement.russian_dictionary", return_value=SimpleNamespace(word_is_known=lambda word: True)):
            result, report = review_before_render(document, config, Path(folder), client=reader)
            self.assertEqual(result.pages[0].blocks[0].text, text)
            self.assertEqual(report["calls"], 1)
            self.assertEqual(report["status"], "completed")
            self.assertTrue((Path(folder) / "review-nemotron-reread.json").exists())
            _, cached = review_before_render(document, config, Path(folder), client=reader)
            self.assertEqual(cached["calls"], 0)
            self.assertEqual(len(calls), 1)
            with patch("docpipe.review._client", side_effect=AssertionError("Unexpected chat transport")):
                exported = review_after_render(document, config, Path(folder))
            self.assertEqual(exported["mode"], "deterministic_export_check_after_nemotron_reread")
            self.assertEqual(exported["calls"], 0)

    def test_disagreement_cannot_replace_whole_text(self):
        from docpipe.nemotron_refinement import proposals
        edits, rejection = proposals("Проверенный текст исходного документа", "Другой ответ", known_word=lambda word: True)
        self.assertEqual(edits, [])
        self.assertIsNotNone(rejection)


if __name__ == "__main__":
    unittest.main()
