# Отчёт Фазы 0

## Сделано

- Создан пакет `docpipe` и базовая структура репозитория.
- Реализован IR на Pydantic v2: Document/Page/Block/Table/Figure и связанные типы.
- Добавлена валидация bbox, уникальности `order` и payload для figure/table.
- Сгенерирован `docs/ir.schema.json`.
- Добавлен конфиг Pydantic Settings + YAML.
- Добавлены `InputError`, `DependencyError`, `ModelError`, `PageError`.
- Добавлен `doctor`.
- Добавлен license gate и `LICENSES.md`.
- Добавлен CI-контур для ruff/mypy/pytest/license gate.

## Проверено запуском

### Прошло

Команда:

```text
cd /mnt/data/docpipe
PYTHONPATH=src pytest -q
```

Результат:

```text
7 passed in 0.07s
```

Команда:

```text
PYTHONPATH=src python -m docpipe.cli doctor
```

Результат: OK для Python, pydantic, pydantic-settings, PyYAML, pypdfium2, Pillow и NumPy.

### Запускалось и упало

Первый запуск `python -m docpipe.cli doctor` без установки пакета завершился `ModuleNotFoundError: docpipe`. Это проблема способа запуска из исходного checkout, а не тестов; повторно команда с `PYTHONPATH=src` прошла.

Первоначальная версия license gate также падала из-за формата metadata лицензий. Проверка была исправлена. Финальный запуск license gate завершился с кодом 0.

Попытка `pip install -e . --no-deps` запускалась и упала до установки: изолированный build env попытался скачать `setuptools` при отключённой сети. Это ограничение текущего окружения; pytest и license gate работают непосредственно из checkout.

### Не запускалось

- `ruff`: не установлен в текущем окружении.
- `mypy`: не установлен в текущем окружении.
- `pikepdf`: не установлен.
- `img2pdf`: не установлен.
- Docling/PaddleOCR/PaddleOCR-VL: не установлены; реальные движки Фазы 0 не требуются.
- CI GitHub Actions не запускался из этого окружения.
- Проверка работы при реально отключённой сети не выполнялась на сетевом уровне; архитектурно сетевые вызовы в Фазе 0 отсутствуют.

## Известные ограничения

Фаза 0 не выполняет OCR, rasterization, image extraction, masking, reading order, renderers или verify.

Установленный локально `pypdfium2` имеет версию 5.8.0, тогда как проект фиксирует 5.13.0. Это явно оставлено как предупреждение окружения; версия не объявляется проверенной.

## Допущения

- GPU опционален и будет определяться автоматически на следующих фазах.
- Целевая latency не объявляется до baseline.
- Основной потоковый вывод используется по умолчанию.
- Позиционная пересборка выключена.
- Качество и latency thresholds не придумываются до baseline.
