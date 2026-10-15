from types import SimpleNamespace
from docpipe.stages.word_refinement import select_word_result


def result(text,score):
    return SimpleNamespace(txts=[text],scores=[score])


def test_independent_latin_reader_can_correct_a_mixed_domain():
    primary=result('exaмple.cоm',.99)
    alternative=result('example.com',.995)
    assert select_word_result(primary,alternative) is alternative
    assert select_word_result(primary,result('other.net',.995)) is primary


def test_native_russian_identifier_is_not_transliterated():
    primary=result('МкСЗ_07_2024',.99)
    assert select_word_result(primary,result('MKC3_07_2024',.98)) is primary
