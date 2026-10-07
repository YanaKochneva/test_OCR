# Отчёт Фазы 6

## Сделано

- FastAPI jobs API.
- In-memory bounded queue.
- Worker pool и backpressure 429.
- Повторное использование загруженных engine в процессе.
- Прогресс по страницам.
- TTL-cleanup.
- ZIP результата и безопасная выдача отдельных файлов.
- `/healthz`, `/readyz`, `/metrics`.
- API key.
- CLI `parse`, `doctor`, `verify`, `models`, `eval`.
- CPU Dockerfile и CUDA-вариант.
- docker-compose.
- Нагрузочный скрипт.

## Проверено запуском

- `pytest -q` → 23 passed, 2 skipped.
- `python -m compileall -q src eval` → OK.
- `python scripts/check_licenses.py` → RC 0; текущий `pypdfium2` 5.8.0 отличается от зафиксированного 5.13.0, `pikepdf`/`img2pdf` отсутствуют и поэтому не выполняют runtime license check.
- `ruff`/`mypy` не запускались: исполняемые файлы отсутствуют в текущем окружении.
- Docker build не запускался: Docker daemon/CLI отсутствует в текущем окружении.
- Реальный Docling/PaddleOCR inference не запускался: пакеты/модели недоступны в офлайн-окружении.

## Не проверено и почему

Реальный inference Docling/PaddleOCR и CUDA-образ не запускаются в текущем офлайн-окружении без соответствующих моделей/рантайма.

## Известные ограничения

- Очередь по умолчанию только в памяти; при рестарте незавершённые jobs теряются.
- Для горизонтального масштабирования требуется внешний broker согласно архитектурному интерфейсу.
- Метрики реализованы минимальным Prometheus text endpoint без отдельной зависимости.

## Допущения

- По умолчанию engine API — `fake`, чтобы чистый контейнер был проверяемым без скачивания моделей.
- Результаты хранятся в `data/jobs` и удаляются по TTL.
