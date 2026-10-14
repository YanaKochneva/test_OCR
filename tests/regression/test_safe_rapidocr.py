from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from docpipe.engines.safe_rapidocr import RecognitionSafeReader, prepare_recognition_crop, split_recognition_crop


def test_normal_crop_unchanged():
    image = Image.new('RGB', (200, 40), 'white')
    assert prepare_recognition_crop(image, 2000) is image


def test_thin_crop_survives_backend_rounding():
    from rapidocr.utils.process_img import reduce_max_side
    import numpy as np
    for size in [(6000, 20), (20, 6000), (6000, 1)]:
        prepared = prepare_recognition_crop(Image.new('RGB', size), 2000)
        result, _, _ = reduce_max_side(np.asarray(prepared), 2000)
        assert min(result.shape[:2]) >= 32


def test_empty_rejected():
    with pytest.raises(ValueError):
        prepare_recognition_crop(Image.new('RGB', (0, 20)), 2000)


def test_cell_formula_signs_cannot_be_deleted():
    from docpipe.stages.cell_recovery import accepts_cell_candidate, preserves_cell_values
    original = 'ставка (30 % + взносы в %)/100'
    assert not preserves_cell_values(original, 'ставка (30 % взносы в %)/100')
    assert not accepts_cell_candidate(original, 'ставка (30 % + взносы в )/100')
    assert preserves_cell_values(original, 'новая ставка (30 % + взносы в %)/100')


def test_split_preserves_all_ink():
    import numpy as np
    image = Image.new('RGB', (6000, 30), 'white')
    draw = ImageDraw.Draw(image)
    for x in range(20, 5900, 100):
        draw.rectangle((x, 5, x + 40, 25), fill='black')
    pieces = split_recognition_crop(image, 2000)
    assert len(pieces) > 1
    assert all(p.width <= 2000 for p in pieces)
    assert np.array_equal(np.concatenate([np.asarray(p) for p in pieces], axis=1), np.asarray(image))


def test_detection_keeps_coordinates_and_errors():
    from rapidocr.utils.process_img import ResizeImgError
    image = Image.new('RGB', (6000, 20))
    class Backend:
        max_side_len = 2000
        def __call__(self, actual, **kwargs):
            assert actual is image
            raise ResizeImgError()
    with pytest.raises(ResizeImgError):
        RecognitionSafeReader(Backend())(image, use_det=True)


def test_refinement_failure_explicit_and_other_errors_propagate():
    from rapidocr.utils.process_img import ResizeImgError
    class Backend:
        max_side_len = 2000
        error = ResizeImgError()
        def __call__(self, actual, **kwargs):
            raise self.error
    backend = Backend()
    reader = RecognitionSafeReader(backend)
    assert not reader(Image.new('RGB', (50, 20)), use_det=False).txts
    assert reader.failures[0]['exception_type'] == 'ResizeImgError'
    backend.error = RuntimeError('unexpected')
    with pytest.raises(RuntimeError):
        reader(Image.new('RGB', (50, 20)), use_det=False)


def test_cell_projection_retains_numbers_and_multiline(monkeypatch):
    from docpipe.engines.docling_engine import DoclingEngine
    from docpipe.engines.base import PageMeta
    monkeypatch.setenv('DOCPIPE_OCR_CONSENSUS', '0')
    class Backend:
        max_side_len = 2000
        def __call__(self, image, **kwargs):
            return SimpleNamespace(txts=['12_'], scores=[1.0], boxes=None)
    engine = DoclingEngine()
    engine._refinement_reader = RecognitionSafeReader(Backend())
    engine._inside_table_cell = True
    image = Image.new('RGB', (100, 120), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 99, 7), fill='black')  # rule
    draw.rectangle((40, 25, 43, 40), fill='black')  # thin digit
    draw.rectangle((40, 44, 60, 45), fill='black')  # underscore
    draw.rectangle((40, 80, 43, 95), fill='black')
    result = engine._projected_lines(image, PageMeta(0,100,120,0,72))
    assert [text for text,_,_ in result] == ['12_', '12_']


def test_failed_line_does_not_discard_other_lines(monkeypatch):
    from rapidocr.utils.process_img import ResizeImgError
    from docpipe.engines.docling_engine import DoclingEngine
    from docpipe.engines.base import PageMeta
    monkeypatch.setenv('DOCPIPE_OCR_CONSENSUS', '0')
    class Backend:
        max_side_len = 2000
        count = 0
        def __call__(self, image, **kwargs):
            self.count += 1
            if self.count == 1: raise ResizeImgError()
            return SimpleNamespace(txts=['12'], scores=[1.0], boxes=None)
    engine = DoclingEngine()
    engine._refinement_reader = RecognitionSafeReader(Backend())
    engine._inside_table_cell = True
    image = Image.new('RGB', (100,120), 'white')
    draw = ImageDraw.Draw(image)
    for y in (20,80): draw.rectangle((40,y,43,y+15), fill='black')
    result = engine._projected_lines(image, PageMeta(0,100,120,0,72))
    assert len(result) == 1
    assert engine.refinement_incomplete
    assert 'Unread refinement region' in engine.warnings[0]


def test_failed_cell_reread_preserves_primary_table(monkeypatch):
    from rapidocr.utils.process_img import ResizeImgError
    from docpipe.engines.docling_engine import DoclingEngine
    from docpipe.engines.base import PageMeta
    from docpipe.ir import BBox, Block, BlockType, TableData, TableCell
    for option in ('CONSENSUS', 'VERIFIED_GRID', 'ADAPTIVE_CELL_CROPS', 'CELL_DETECTION_RECOVERY'):
        monkeypatch.setenv('DOCPIPE_OCR_' + option, '0')
    class Backend:
        max_side_len = 2000
        def __call__(self, image, **kwargs): raise ResizeImgError()
    engine = DoclingEngine()
    engine._refinement_reader = RecognitionSafeReader(Backend())
    box = BBox(x0=0,y0=0,x1=100,y1=120)
    block = Block(id='table',type=BlockType.TABLE,order=0,bbox=box,
        table=TableData(n_rows=1,n_cols=1,cells=[TableCell(row=0,col=0,text='123 + 45 %',bbox=box)]))
    image = Image.new('RGB', (100,120), 'white')
    ImageDraw.Draw(image).rectangle((40,20,43,35), fill='black')
    result = engine._refine_table_cells([block], image, PageMeta(0,100,120,0,72))
    assert result[0].table.cells[0].text == '123 + 45 %'
    assert result[0].table.cells[0].bbox == box
    assert engine.refinement_incomplete
