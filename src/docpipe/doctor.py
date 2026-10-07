from __future__ import annotations

import importlib.util
import platform
import sys
from importlib.metadata import PackageNotFoundError, version

from docpipe.config import AppConfig


def _pkg(name: str) -> dict:
    module = name.replace("-", "_")
    available = importlib.util.find_spec(module) is not None
    try:
        ver = version(name)
    except PackageNotFoundError:
        ver = None
    return {"name": name, "available": available, "version": ver}


def run_doctor(config: AppConfig) -> dict:
    packages = [_pkg(x) for x in ("pydantic", "pydantic-settings", "PyYAML", "pypdfium2", "Pillow", "numpy", "fastapi", "uvicorn")]
    return {
        "ok": sys.version_info >= (3, 10) and all(x["available"] for x in packages),
        "python": platform.python_version(),
        "offline": config.offline,
        "gpu": config.gpu,
        "engine": config.api.default_engine,
        "workers": config.api.workers,
        "packages": packages,
    }
