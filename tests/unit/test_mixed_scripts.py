from docpipe.stages.mixed_scripts import choose_path_candidate


def test_preserves_latin_names():
    primary='\\server\\Пonюc\\PGR_UpdatedBlockModels'
    assert choose_path_candidate(primary,'\\server\\Полюс\\PGR_UpdatedBlockModels')=='\\server\\Полюс\\PGR_UpdatedBlockModels'
    assert choose_path_candidate(primary,'\\server\\Полюс\\PGR_UpdatedBlokModels')==primary


def test_does_not_rewrite_plain_latin_or_unrelated_text():
    assert choose_path_candidate('file_name.DAT','файл_имя.DAT')=='file_name.DAT'
    assert choose_path_candidate('\\server\\Model','\\server\\Модель')=='\\server\\Model'
    assert choose_path_candidate('\\server\\Полюс','другой текст')=='\\server\\Полюс'
