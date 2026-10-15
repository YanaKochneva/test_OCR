import pytest
from docpipe.evaluation import compare_text


def test_graphic_fields_are_scoped_only():
    score = compare_text('Имя _____ файл file_name.dat', 'Имя файл file_name.dat')
    assert score['reference_scoped_cer'] == 0
    assert score['cer'] > 0
    assert score['excluded_graphic_placeholder_chars_reference'] == 5


def test_identifiers_are_not_graphic_fields():
    score = compare_text('file_name.dat name___value', 'file name.dat namevalue')
    assert score['reference_scoped_cer'] > 0
    assert score['excluded_graphic_placeholder_chars_reference'] == 0


def test_graphics_only_reference_is_invalid():
    with pytest.raises(ValueError):
        compare_text('_____', '')


def test_blank_date_rule_next_to_year():
    score = compare_text('Дата « »________2025 г.', 'Дата « »2025 г.')
    assert score['reference_scoped_cer'] == 0
    assert score['excluded_graphic_placeholder_chars_reference'] == 8
