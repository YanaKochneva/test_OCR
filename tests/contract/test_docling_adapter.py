from __future__ import annotations

import pytest

from docpipe.engines.docling_engine import DoclingEngine
from docpipe.errors import DependencyError


def test_docling_dependency_error_is_actionable_when_missing() -> None:
    try:
        import docling  # noqa: F401
    except ImportError:
        with pytest.raises(DependencyError, match=r"docpipe\[docling\]"):
            DoclingEngine().load()
    else:
        pytest.skip("Docling установлен; реальный контракт проверяется engine-тестом")


@pytest.mark.engine_docling
def test_docling_contract_requires_real_installation() -> None:
    try:
        import docling  # noqa: F401
    except ImportError:
        pytest.skip("Docling не установлен: pip install 'docpipe[docling]'")
    engine = DoclingEngine()
    engine.load()
    assert engine.version != "unknown"
