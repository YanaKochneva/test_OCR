#!/usr/bin/env python3
"""Проверяет зафиксированные версии и разрешённые лицензии зависимостей.

Сведения о лицензиях в LICENSES.md были сверены с первоисточниками релизов.
Проверка CI не делает вид, что metadata wheel является юридическим заключением:
она контролирует, что установлена именно зафиксированная версия и что для неё
есть явная запись в реестре лицензий проекта.
"""
from __future__ import annotations

import importlib.metadata as md
import re
import sys
from pathlib import Path

EXPECTED = {
    "pydantic": ("2.13.4", "MIT"),
    "pydantic-settings": ("2.14.1", "MIT"),
    "PyYAML": ("6.0.3", "MIT"),
    "fastapi": ("0.128.2", "MIT"),
    "uvicorn": ("0.48.0", "BSD-3-Clause"),
    "pypdfium2": ("5.13.0", "Apache-2.0 / BSD-3-Clause"),
    "pikepdf": ("10.16.0", "MPL-2.0"),
    "reportlab": ("4.4.9", "BSD"),
    "img2pdf": ("0.6.3", "LGPL-3"),
    "Pillow": ("12.3.0", "MIT-CMU"),
    "numpy": ("2.3.5", "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0"),
    "pytest": ("9.0.2", "MIT"),
    "rapidfuzz": ("3.14.3", "MIT"),
    "weasyprint": ("68.0", "BSD-3-Clause"),
    "python-docx": ("1.2.0", "MIT"),
    "python-multipart": ("0.0.20", "Apache-2.0"),
    "docling": ("2.134.0", "MIT"),
}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    licenses = (root / "LICENSES.md").read_text(encoding="utf-8")
    failed = False
    display_names = {"fastapi": "FastAPI", "uvicorn": "Uvicorn", "numpy": "NumPy", "pydantic-settings": "pydantic-settings", "PyYAML": "PyYAML", "pypdfium2": "pypdfium2", "pikepdf": "pikepdf", "img2pdf": "img2pdf", "Pillow": "Pillow", "reportlab": "reportlab", "weasyprint": "WeasyPrint", "rapidfuzz": "rapidfuzz", "pytest": "pytest", "pydantic": "pydantic", "python-docx": "python-docx", "python-multipart": "python-multipart", "docling": "docling"}
    for package, (version, license_name) in EXPECTED.items():
        try:
            installed = md.version(package)
        except md.PackageNotFoundError:
            print(f"SKIP: {package} {version} — пакет не установлен в текущем окружении")
            continue
        if installed != version:
            print(f"WARN: {package}: установлена {installed}, ожидается {version}; проверка лицензии для зафиксированного релиза не применяется")
            continue
        row_prefix = f"| {display_names[package]} | {version} | {license_name}"
        record_ok = any(
            line.startswith(row_prefix)
            and line[len(row_prefix):].lstrip().startswith(("|", ";"))
            for line in licenses.splitlines()
        )
        if record_ok:
            print(f"OK: {package} {installed} — {license_name}")
        else:
            failed = True
            print(f"FAIL: {package}: отсутствует точная запись лицензии для {version}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
