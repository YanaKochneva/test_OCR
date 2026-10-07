from docpipe.config import AppConfig


def test_default_config():
    config = AppConfig()
    assert config.default_dpi == 300
    assert config.force_ocr == "auto"
    assert config.render.headers_footers == "drop"
    assert config.render.positional is False
