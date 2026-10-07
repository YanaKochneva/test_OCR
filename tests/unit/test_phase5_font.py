from pathlib import Path


def test_free_font_available():
    candidates = [
	Path("D:/AI/fonts/dejavu/dejavu-fonts-ttf-2.37/ttf/DejaVuSans.ttf"),
        Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'),
        Path('/usr/share/fonts/dejavu/DejaVuSans.ttf'),
        Path('C:/Windows/Fonts/DejaVuSans.ttf'),
    ]
    assert any(p.exists() for p in candidates), 'Нужен свободный DejaVu Sans с кириллицей'
