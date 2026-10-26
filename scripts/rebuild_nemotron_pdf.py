"""Rebuild a coordinate PDF from an existing raw-response PDF; no inference."""
from pathlib import Path
import argparse
import html
import json
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from docpipe.nemotron_assets import IMAGE_CLASSES, collect_regions, covered_fraction, save_regions

import pypdfium2 as pdfium
import pikepdf
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph


def rebuild(raw_pdf: Path, source_pdf: Path, output: Path, image_regions=None, image_dpi=300):
    if output.resolve() in {raw_pdf.resolve(), source_pdf.resolve()}:
        raise ValueError("Output must differ from both input PDFs")
    explicit = json.loads(image_regions.read_text(encoding="utf-8")) if image_regions else []
    font_root = Path(__file__).resolve().parents[1] / "src/docpipe/assets/fonts"
    for name, filename in [("Nemotron", "DejaVuSans.ttf"), ("NemotronBold", "DejaVuSans-Bold.ttf")]:
        pdfmetrics.registerFont(TTFont(name, str(font_root / filename)))
    with pikepdf.Pdf.open(raw_pdf) as document:
        text = []
        for page in document.pages:
            maps = {}
            for name, font in page.Resources.Font.items():
                if "/ToUnicode" in font:
                    cmap = font.ToUnicode.read_bytes().decode("ascii")
                    entries = re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S)
                    maps[name] = {int(a, 16): bytes.fromhex(b).decode("utf-16-be")
                                  for a, b in re.findall(r"<([\dA-Fa-f]+)>\s*<([\dA-Fa-f]+)>", "".join(entries))}
            active = None
            for instruction in pikepdf.parse_content_stream(page):
                if str(instruction.operator) == "Tf":
                    active = str(instruction.operands[0])
                elif str(instruction.operator) == "Tj":
                    encoded = bytes(instruction.operands[0])
                    line = "".join(maps.get(active, {}).get(c, chr(c)) for c in encoded)
                    if line:
                        text.append((line, float(page.MediaBox[2]) - 72))
    recovered = ""
    for index, (line, available) in enumerate(text):
        recovered += line
        if index + 1 < len(text):
            next_character = text[index + 1][0][0]
            # Undo only the line wrapping inserted by write_response_pdf.
            occupied = pdfmetrics.stringWidth(line + next_character, "Nemotron", 9)
            recovered += "" if occupied > available else " "
    recovered = re.sub(r"<[^>]+>", lambda m: re.sub(r"\s+", "", m[0]), recovered)
    raw_pages = re.split(r"</s>\s*<s>", recovered)[1:]
    raw_sidecar = raw_pdf.with_suffix(".raw.json")
    if raw_sidecar.is_file():
        saved = json.loads(raw_sidecar.read_text(encoding="utf-8"))
        if not saved.get("complete"):
            raise ValueError("Nemotron response is incomplete; wait for inference to finish")
        if Path(saved["source"]).resolve() != source_pdf.resolve():
            raise ValueError("Nemotron response belongs to another source document")
        raw_pages = saved["pages"]
    pattern = re.compile(
        r"<x_([\d.]+)>\s*<y_([\d.]+)>(.*?)<x_([\d.]+)>\s*<y_([\d.]+)>\s*<class_([^>]+)>", re.S
    )
    pdfmetrics.registerFontFamily("Nemotron", normal="Nemotron", bold="NemotronBold")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(output))
    report = []
    image_dir = output.parent / f"{output.stem}-images"
    all_assets = []
    with pdfium.PdfDocument(source_pdf) as source:
        if len(raw_pages) != len(source):
            raise ValueError(f"Response/source page mismatch: {len(raw_pages)} / {len(source)}")
        if any(not 1 <= r["page"] <= len(source) for r in explicit):
            raise ValueError("Image region page is outside the source document")
        for i, raw in enumerate(raw_pages):
            page = source[i]
            width, height = page.get_size()
            canvas.setPageSize((width, height))
            blocks = []
            matches = list(pattern.finditer(raw))
            figure_blocks = [{"class": m[6], "bbox": [float(m[j]) for j in (1, 2, 4, 5)]}
                             for m in matches]
            try:
                regions = collect_regions(page, figure_blocks, [r for r in explicit if r["page"] == i + 1])
                assets = save_regions(page, regions, image_dir, i + 1, dpi=image_dpi)
            finally:
                page.close()
            all_assets.extend(assets)
            for match in matches:
                x1, y1, body, x2, y2, cls = match.groups()
                x1, y1, x2, y2 = map(float, (x1, y1, x2, y2))
                original_bbox = [x1, y1, x2, y2]
                if cls.lower() in IMAGE_CLASSES or any(
                    covered_fraction(original_bbox, asset["bbox"]) >= .65 for asset in assets
                ):
                    blocks.append({"class": cls, "bbox": original_bbox, "rendered_as": "image"})
                    continue
                # Misclassified prose headers must not be printed over a logo.
                for asset in assets:
                    ax0, ay0, ax1, ay1 = asset["bbox"]
                    if cls == "Page-header" and min(y2, ay1) > max(y1, ay0) and ax0 < x1 < ax1 < x2:
                        x1 = ax1 + .008
                bw, bh = (x2 - x1) * width, (y2 - y1) * height
                if bw <= 0 or bh <= 0:
                    raise ValueError(f"Invalid box on page {i+1}")
                if cls == "Table":
                    from docpipe.nemotron_table_pdf import draw_table
                    table_info = draw_table(canvas, body, x1 * width, height - y1 * height, bw, bh)
                    blocks.append({"class": cls, "bbox": original_bbox, "rendered_as": "table",
                                   "text": body, **table_info})
                    continue
                source_lines = [line.strip() for line in body.splitlines() if line.strip()]
                contents_lines = (source_lines if len(source_lines) >= 3 and all(
                    re.search(r"\.{2,}\s*\d+\s*$", line) for line in source_lines) else None)
                body = re.sub(r"\s+", " ", body).strip()
                body = body.replace("\x00", "•")
                bold = "**" in body or cls == "Section-header"
                body = body.replace("**", "")
                if body.startswith("_") and body.endswith("_"):
                    body = body[1:-1]
                body = re.sub(r"<[^>]+>", "", body)
                # Do not correct the recognized words or numbers.
                body = html.escape(body)
                if contents_lines:
                    body = "<br/>".join(html.escape(line.replace("**", "")) for line in contents_lines)
                center = (abs((x1 + x2) / 2 - .5) < .08 and (y1 > .4 and i == 0 or bw < width * .2))
                size = 13 if i == 0 else (9 if cls == "Page-header" else 11.5)
                rotated = bw < bh * .25 and len(body) > 25
                available_width, available_height = (bh, bw) if rotated else (bw, bh)
                while True:
                    style = ParagraphStyle("block", fontName="NemotronBold" if bold else "Nemotron",
                                           fontSize=size, leading=size * 1.15,
                                           alignment=1 if center else 0, splitLongWords=True)
                    paragraph = Paragraph(body, style)
                    _, used = paragraph.wrap(available_width, height)
                    if used <= available_height + .01 or size <= 4:
                        break
                    size -= .1
                if used > available_height + .01:
                    raise ValueError(f"Block does not fit on page {i+1}")
                if rotated:
                    canvas.saveState()
                    canvas.translate(x2 * width, height - y2 * height)
                    canvas.rotate(90)
                    paragraph.drawOn(canvas, 0, bw - used)
                    canvas.restoreState()
                else:
                    paragraph.drawOn(canvas, x1 * width, height - y1 * height - used)
                blocks.append({"class": cls, "bbox": [x1, y1, x2, y2],
                               "source_bbox": original_bbox, "text": html.unescape(body.replace("<br/>", "\n")),
                               "rotation": 90 if rotated else 0, "font_size": round(size, 2)})
            if not blocks:
                raise ValueError(f"No blocks recovered on page {i+1}")
            if len(blocks) != raw.count("<class_"):
                raise ValueError(f"Some block coordinates were not recovered on page {i+1}")
            for asset in assets:
                ax0, ay0, ax1, ay1 = asset["bbox"]
                canvas.drawImage(str(image_dir / asset["file"]), ax0 * width, (1 - ay1) * height,
                                 width=(ax1 - ax0) * width, height=(ay1 - ay0) * height, mask="auto")
            report.append({"page": i + 1, "size": [width, height], "blocks": blocks, "images": assets})
            canvas.showPage()
    canvas.save()
    output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if all_assets:
        (image_dir / "manifest.json").write_text(json.dumps({"coordinate_system": "normalized_top_left",
            "source": str(source_pdf.resolve()), "images": all_assets}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Created {output}; blocks per page: {[len(p['blocks']) for p in report]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_pdf", type=Path)
    parser.add_argument("source_pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--image-regions", type=Path, help="JSON list of {page: 1-based, bbox: [x0,y0,x1,y1]}")
    parser.add_argument("--image-dpi", type=int, default=300)
    args = parser.parse_args()
    rebuild(args.raw_pdf, args.source_pdf, args.output, args.image_regions, args.image_dpi)
