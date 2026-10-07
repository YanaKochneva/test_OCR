# docpipe

Production-oriented конвейер «скан → документ с картинками на своих местах».

## Статус

Реализованы **Фазы 0–5**. Реальные Docling/PaddleOCR адаптеры имеют контрактные тесты; фактический inference требует установленных пакетов и предварительно загруженных моделей.

### Сделано в Фазе 0

- Pydantic v2 IR с детерминированной JSON-сериализацией.
- JSON Schema в `docs/ir.schema.json`.
- Конфигурация через `pydantic-settings` и YAML.
- Иерархия ошибок.
- Базовый `doctor`.
- Проверка лицензий установленного окружения.
- Зафиксированные версии в `constraints.txt`.
- Архитектурные решения в `docs/DECISIONS.md`.

## Установка

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -c constraints.txt -e .
```

Для CPU-only потокового PDF можно использовать `pip install -e .` и extra `pdf` для WeasyPrint. OCR extras: `docpipe[docling]`, `docpipe[paddle]`.

## Команды

```bash
python -m docpipe.cli doctor
python scripts/generate_ir_schema.py
python scripts/check_licenses.py
python -m pytest -q
```

## Допущения

- GPU: опционален, определяется автоматически на следующих фазах.
- Целевая latency: не объявляется до baseline.
- Языки: русский и английский.
- Основной вывод: потоковый.
- Позиционный рендерер по умолчанию выключен.
- Обработка выполняется offline; загрузка моделей — отдельный подготовительный шаг.
- Численные quality thresholds заказчик задаёт после baseline, они не подгоняются под результат.

## Важное ограничение текущей фазы

В среде исполнения сейчас нет `docling`, `paddleocr`, `pikepdf`, `img2pdf`; поэтому их API и реальные OCR-прогоны в Фазе 0 не объявляются проверенными.
