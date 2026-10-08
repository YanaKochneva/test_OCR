from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from importlib.metadata import PackageNotFoundError, version

from docpipe.config import AppConfig

# Имя пакета в metadata и имя importable-модуля часто различаются
# (PyYAML -> yaml, Pillow -> PIL); проверяем именно модуль.
_MODULE_FOR_PACKAGE = {
    "PyYAML": "yaml",
    "Pillow": "PIL",
    "pydantic-settings": "pydantic_settings",
    "python-multipart": "multipart",
    "python-docx": "docx",
}


def _pkg(name: str) -> dict:
    module = _MODULE_FOR_PACKAGE.get(name, name.replace("-", "_"))
    available = importlib.util.find_spec(module) is not None
    try:
        ver = version(name)
    except PackageNotFoundError:
        ver = None
    return {"name": name, "available": available, "version": ver}


def run_doctor(config: AppConfig) -> dict:
    packages = [_pkg(x) for x in ("pydantic", "pydantic-settings", "PyYAML", "pypdfium2", "Pillow", "numpy", "fastapi", "uvicorn")]
    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    # Python
    add("python", "PASS" if sys.version_info >= (3, 10) else "FAIL", platform.python_version())
    # Зависимости
    for pkg in packages:
        add(
            f"package:{pkg['name']}",
            "PASS" if pkg["available"] else "FAIL",
            f"version={pkg['version']}",
        )
    # PDF backend
    add(
        "pdf_backend:pypdfium2",
        "PASS" if packages[3]["available"] else "FAIL",
        "rasterization backend",
    )
    # OCR engines (optional): проверяем только наличие модуля, без загрузки моделей.
    for module, engine in (("docling", "docling"), ("paddleocr", "ppstructure")):
        present = importlib.util.find_spec(module) is not None
        add(f"ocr_backend:{engine}", "PASS" if present else "WARN",
            "installed" if present else "optional dependency not installed")
    # Шрифты с кириллицей
    from docpipe.renderers.fonts import find_font
    font_path = find_font()
    add("font:cyrillic", "PASS" if font_path else "FAIL", str(font_path) if font_path else "DejaVu Sans не найден")
    # Директории
    try:
        config.api.work_dir.mkdir(parents=True, exist_ok=True)
        add("work_dir", "PASS", str(config.api.work_dir))
    except OSError as exc:
        add("work_dir", "FAIL", str(exc))
    # GPU (informatively)
    add("gpu", "WARN" if config.gpu is None else "PASS", f"config.gpu={config.gpu}")
    # Offline mode
    add("offline", "PASS" if config.offline else "WARN", f"offline={config.offline}")
    add(
        "api_auth",
        "PASS" if config.api.api_key else "WARN",
        "API key configured" if config.api.api_key else "API key is unset; API endpoints accept unauthenticated requests",
    )

    failed = any(c["status"] == "FAIL" for c in checks)
    return {
        "ok": not failed,
        "python": platform.python_version(),
        "offline": config.offline,
        "gpu": config.gpu,
        "engine": config.api.default_engine,
        "workers": config.api.workers,
        "checks": checks,
        "packages": packages,
    }
