"""Single-pass, full-page Nemotron inference, independent of the OCR pipeline."""
from __future__ import annotations

from pathlib import Path

PROMPT = "</s><s><predict_bbox><predict_classes><output_markdown><predict_text_in_pic>"


def page_images(source: Path, dpi: int):
    from PIL import Image, ImageSequence

    if source.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(source) as document:
            for index in range(len(document)):
                page = document[index]
                try:
                    bitmap = page.render(scale=dpi / 72)
                    try:
                        yield bitmap.to_pil().convert("RGB")
                    finally:
                        bitmap.close()
                finally:
                    page.close()
    else:
        with Image.open(source) as image:
            for frame in ImageSequence.Iterator(image):
                yield frame.convert("RGB")


def write_response_pdf(responses: list[str], destination: Path):
    """Print the exact decoded response as text, including model markup/tokens."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    font = "NemotronRawResponse"
    font_path = Path(__file__).parent / "assets/fonts/DejaVuSans.ttf"
    pdfmetrics.registerFont(TTFont(font, str(font_path)))
    canvas = Canvas(str(destination), pagesize=A4)
    width, height = A4
    margin, size, leading = 36, 9, 13
    for response in responses:
        y = height - margin
        canvas.setFont(font, size)
        # Wrap by actual glyph widths, even for unbroken model token streams.
        for line in response.split("\n"):
            chunk, used = "", 0.0
            for character in line.expandtabs(4):
                advance = pdfmetrics.stringWidth(character, font, size)
                if chunk and used + advance > width - 2 * margin:
                    canvas.drawString(margin, y, chunk)
                    y -= leading
                    if y < margin:
                        canvas.showPage()
                        canvas.setFont(font, size)
                        y = height - margin
                    chunk, used = "", 0.0
                chunk += character
                used += advance
            canvas.drawString(margin, y, chunk)
            y -= leading
            if y < margin:
                canvas.showPage()
                canvas.setFont(font, size)
                y = height - margin
        if y != height - margin:
            canvas.showPage()
    canvas.save()


def recognize(source: Path, output: Path, model_path: Path, *, device: str = "auto",
              dpi: int = 150, max_new_tokens: int = 8744) -> Path:
    """One generate call per source page; no crops, retries or postprocessing."""
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.suffix.lower() != ".pdf":
        raise ValueError("Output must have a .pdf extension")
    if dpi <= 0 or max_new_tokens <= 0:
        raise ValueError("dpi and max_new_tokens must be positive")
    if source.resolve() == output.resolve():
        raise ValueError("Output must differ from the source document")
    model_path = model_path.resolve()
    if not model_path.is_dir():
        raise FileNotFoundError(f"Local Nemotron model directory not found: {model_path}")

    import torch
    from transformers import AutoModel, AutoProcessor, GenerationConfig

    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    model = AutoModel.from_pretrained(
        model_path, trust_remote_code=True, local_files_only=True, dtype=dtype,
    ).to(device).eval()
    processor = AutoProcessor.from_pretrained(
        model_path, trust_remote_code=True, local_files_only=True,
    )
    generation = GenerationConfig.from_pretrained(model_path, local_files_only=True)
    generation.max_new_tokens = max_new_tokens
    generation.do_sample = False
    responses = []
    for index, image in enumerate(page_images(source, dpi), start=1):
        print(f"Nemotron: page {index}", flush=True)
        try:
            inputs = processor(images=[image], text=PROMPT, return_tensors="pt",
                               add_special_tokens=False).to(device)
            with torch.inference_mode():
                outputs = model.generate(**inputs, generation_config=generation)
            raw = processor.batch_decode(outputs, skip_special_tokens=False)[0]
            # Do not silently publish an incomplete document as a successful PDF.
            token_ids = outputs[0].tolist()
            eos = generation.eos_token_id
            eos_ids = eos if isinstance(eos, (list, tuple)) else [eos]
            if len(token_ids) >= max_new_tokens and token_ids[-1] not in eos_ids:
                raise RuntimeError(f"Page {index}: token limit reached; increase --max-new-tokens")
            if not raw.strip():
                raise RuntimeError(f"Page {index}: model returned an empty response")
            responses.append(raw)
        finally:
            image.close()
    if not responses:
        raise ValueError("Document has no pages")
    output.parent.mkdir(parents=True, exist_ok=True)
    write_response_pdf(responses, output)
    return output
