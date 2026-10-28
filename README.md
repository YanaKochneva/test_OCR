# DocPipe

Распознавание PDF и изображений: Docling + RapidOCR с русской моделью
PP-OCRv6. Результат содержит текст, таблицы, рисунки и координаты блоков.
Локальный Nemotron или внешний AITunnel могут повторно читать и исправлять текст.

## Notebook

Python 3.12+, зависимости в окружении ядра:

```sh
python -m pip install ".[nemotron-review]"
```

Все зависимости заданы в `pyproject.toml`. Для OCR/API без локального Nemotron
достаточно `python -m pip install .`; дополнительный набор `nemotron-review`
нужен только для повторного чтения этой моделью.

Откройте `process_documents.ipynb`, задайте `PROJECT_ROOT`, `INPUTS` и пути
моделей. Выполните три ячейки: настройки → подготовка → распознавание.
Notebook использует Python текущего ядра; пакеты и модели сам не скачивает.
Результаты сохраняются в `reports/notebook-runs/`.

Модели для локального запуска:

- Docling: артефакты layout/table в `DOCLING_MODELS` либо локальном кэше Docling.
- RapidOCR: `ch_PP-OCRv5_det_mobile.onnx`, `ch_ppocr_mobile_v2.0_cls_mobile.onnx`,
  `eslav_PP-OCRv5_rec_mobile.onnx`, `latin_PP-OCRv5_rec_mobile.onnx`.
- Русская модель: `model.onnx` и `rec_keys.txt` в `models/ppocrv6-medium-ru/`.
- Nemotron: веса, конфигурации и код модели в `models/nemotron-3.5-parse/`.
  Повторное чтение включается настройкой `NEMOTRON_ENABLED` в notebook;
  устройство задаётся через `NEMOTRON_DEVICE` (`cpu`, `cuda`, `auto`).

Используются уже скачанные локальные модели.

## Docker API

```sh
cp .env.example .env
docker compose --env-file .env -f deploy/docker-compose.yml up --build -d
```

В PowerShell используйте `Copy-Item .env.example .env`.
Сборка копирует русскую модель из `models/ppocrv6-medium-ru/`;
эта папка с `model.onnx` и `rec_keys.txt` должна быть на машине сборки.
Зависимости и остальные модели Docling/RapidOCR скачиваются при сборке.
После запуска OCR работает локально
на CPU. Порт по умолчанию: `127.0.0.1:8000`.

Прямая обработка одного или нескольких файлов:

```sh
curl --fail-with-body --max-time 660 http://localhost:8000/v1/recognize \
  -F "files=@scan.pdf" -F "files=@photo.png" -o recognized.zip
```

В PowerShell используйте `curl.exe`. ZIP содержит `manifest.json` и каталоги
`document_001/`, `document_002/` с результатами. Проверяйте статус каждого файла
в манифесте: частичный успех возвращает HTTP 200 и `status: partial`.
Если ни один документ не обработан, API возвращает HTTP 422.

Асинхронная обработка:

| Запрос | Назначение |
|---|---|
| `POST /v1/jobs` | Загрузить файл в multipart-поле `file`; ответ HTTP 202 с `job_id` |
| `GET /v1/jobs/{job_id}` | Статус и прогресс; опрашивать раз в 2 секунды |
| `GET /v1/jobs/{job_id}/document` | Текст и полная структура документа в JSON |
| `GET /v1/jobs/{job_id}/result` | ZIP с результатами |
| `GET /v1/jobs/{job_id}/files/{path}` | Отдельный файл результата |
| `DELETE /v1/jobs/{job_id}` | Удалить результат или ожидающее задание |

Состояния: `queued`, `running`, `completed`, `failed`, `cancelled`.
Необязательный `request_id` передаётся вместе с файлом и сохраняется в ответах.
Поле `review_required` вычисляется по предупреждениям и уверенности распознавания;
предупреждения и статусы страниц также находятся в `document.json`.
Повторная отправка создаёт новое задание. Ошибка обработки находится в поле `error`.

Проверки доступности: `/healthz`, `/readyz`. Схема API: `/openapi.json`, `/docs`.
Один процесс Uvicorn на том заданий; метаданные и результаты сохраняются в
`data/jobs` (Docker volume). Прерванные задания после рестарта получают `failed`.
По умолчанию завершённые результаты хранятся один час, лимит запроса — 16 файлов
и 200 МБ. Веб-формы и отдельного сервиса метрик нет.

## Форматы и настройки

Каждый результат содержит `document.json`, `document.md`, `document.html`,
`document-flow.pdf`, `document-positional.pdf` и извлечённые изображения, если они есть.
DOCX включается через `EXPORT_DOCX` в notebook или `DOCPIPE_DOCX_ENABLED=true`
в `.env` Compose. Поисковый PDF включается через `EXPORT_SEARCHABLE_PDF` в notebook
или `options={"outputs":["pdf-searchable"]}` в multipart-запросе API.

Изображения по умолчанию распознаются прямым RapidOCR; таблицы в этом режиме
не восстанавливаются. Для сканов таблиц задайте `DOCPIPE_IMAGE_OCR=docling`.
PDF обрабатываются Docling. Неуверенные области могут сохраняться фрагментами
исходного изображения; это регулируется `render.unreadable_as_image`.

Внешняя перепроверка по умолчанию выключена. Для неё в `.env` Compose задайте
`DOCPIPE_OFFLINE=false`, `DOCPIPE_REVIEW_ENABLED=true` и `AITUNNEL_API_KEY`.
Текст и, для фотографий, изображения отправляются в AITunnel. Модель задаётся
через `DOCPIPE_REVIEW_MODEL` и должна поддерживать изображения для фотографий.
Проверка цифр и согласования двух чтений сохраняется.

Проверку API-ключа можно включить через `DOCPIPE_REQUIRE_API_KEY=true` и
`DOCPIPE_API_KEY` длиной от 32 символов; клиент передаёт заголовок `X-API-Key`.
При прямом запуске Uvicorn используются переменные `DOCPIPE_API__REQUIRE_API_KEY`
и `DOCPIPE_API__API_KEY`.

Настройки описаны в `src/docpipe/config.py`. Для CLI используется YAML через
`--config`; переменные `DOCPIPE_` с разделителем `__` имеют приоритет над YAML.
Минимальный запуск CLI:

```sh
python -m docpipe parse scan.pdf --out output/scan
```

Исходники находятся в `src/docpipe/`:

- `input.py` — входные файлы, изображения страниц и координаты поворота;
- `pipeline.py` — распознавание и запуск экспорта;
- `engines/` — Docling, RapidOCR и фотографии;
- `stages/layout.py`, `text.py`, `tables.py`, `graphics.py`, `recognition.py` —
  порядок блоков, текст, таблицы, рисунки и согласование повторных чтений;
- `review.py`, `nemotron.py` — исправления распознанного текста;
- `renderers/` — PDF, HTML, Markdown и DOCX с общей подготовкой оформления;
- `api/` — HTTP-запросы и очередь обработки.

Модели, окружение и результаты исключены из Git.
Лицензия встроенных шрифтов: `src/docpipe/assets/fonts/LICENSE-DejaVu.txt`.
