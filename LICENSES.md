# Лицензии зависимостей

Проверка лицензий выполняется по первоисточникам проекта/релиза. Этот файл фиксирует решение проекта; юридическая экспертиза не заменяется им.

| Пакет | Версия | Лицензия | Решение |
|---|---:|---|---|
| pydantic | 2.13.4 | MIT | разрешён |
| pydantic-settings | 2.14.1 | MIT | разрешён |
| PyYAML | 6.0.3 | MIT | разрешён |
| FastAPI | 0.128.2 | MIT | разрешён |
| Uvicorn | 0.48.0 | BSD-3-Clause | разрешён |
| pypdfium2 | 5.13.0 | Apache-2.0 / BSD-3-Clause; PDFium и bundled dependencies имеют отдельные лицензии | разрешён при сохранении notices |
| pikepdf | 10.16.0 | MPL-2.0; wheel содержит third-party components с отдельными лицензиями | разрешён |
| reportlab | 4.4.9 | BSD | разрешён |
| img2pdf | 0.6.3 | LGPL-3 | разрешён только как неизменяемая зависимость |
| Pillow | 12.3.0 | MIT-CMU | разрешён |
| NumPy | 2.3.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | разрешён |
| rapidfuzz | 3.14.3 | MIT | разрешён |
| WeasyPrint | 68.0 | BSD-3-Clause | разрешён |
| python-docx | 1.2.0 | MIT | разрешён |
| python-multipart | 0.0.20 | Apache-2.0 | разрешён; multipart-загрузки FastAPI |
| docling | 2.134.0 | MIT | разрешён; лицензии загружаемых OCR/ML-моделей проверяются отдельно |
| ruff | 0.14.2 | MIT | dev-зависимость, разрешён |
| mypy | 1.18.2 | MIT | dev-зависимость, разрешён |
| pytest | 9.0.2 | MIT | dev-зависимость, разрешён |

## Первичные источники, проверенные при Фазе 0

- Pydantic: официальный репозиторий, LICENSE — MIT.
- pydantic-settings: официальный репозиторий, metadata — MIT.
- FastAPI: официальный репозиторий — MIT.
- pypdfium2: официальный репозиторий/README — Apache-2.0 или BSD-3-Clause; bundled PDFium/dependencies имеют дополнительные notices.
- pikepdf: официальный репозиторий — MPL-2.0 и отдельная карта third-party licenses для wheel.
- img2pdf: PyPI/проект — LGPLv3.
- NumPy: PyPI, BSD License для релиза 2.3.5 — только permissive компоненты.
- Pillow: официальный репозиторий, LICENSE — MIT-CMU; релиз 12.3.0 зафиксирован отдельно.

Запрещённые лицензии: AGPL, GPL, SSPL, OpenRAIL-M и лицензии с порогами выручки/финансирования/числа пользователей либо запретом конкурирующих продуктов.

## Добавлено в Фазе 5

- `weasyprint==68.0` — BSD-3-Clause; проверять также лицензии системных runtime-зависимостей при сборке образа.
- `python-docx==1.2.0` — MIT.
- `reportlab==4.4.9` — BSD-3-Clause.
