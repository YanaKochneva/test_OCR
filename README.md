# docpipe

Production-oriented конвейер «скан → документ с картинками на своих местах».

## Статус

Реализованы OCR-конвейер, API заданий и веб-интерфейс. Production API и CLI по умолчанию используют Docling; fake-движок остаётся только для явно настроенных тестов и разработки. Готовность к промышленной эксплуатации ограничена незавершённым тестированием контейнера, безопасности и нагрузок.

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
python -m pip install -c constraints.txt -e '.[docling]'
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

Веб-интерфейс запускается по адресу `http://localhost:8000/` вместе с API. Инструкция: `docs/USER_INTERFACE.md`. Для распознавания локально установите extra `docpipe[docling]`; Docker-образ устанавливает Docling и заранее загружает его модели.
