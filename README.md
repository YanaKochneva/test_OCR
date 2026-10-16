# DocPipe

API-сервис распознавания PDF и сканов с восстановлением текста, таблиц и изображений. Основной движок — Docling + RapidOCR с русской моделью PP-OCRv6. GLM удалён.

## Запуск в Docker

Требуются Docker Engine и Docker Compose. Из корня репозитория:

```sh
cp .env.example .env
# Задайте DOCPIPE_API_KEY (от 32 символов) и AITUNNEL_API_KEY в .env.
docker compose --env-file .env -f deploy/docker-compose.yml up --build -d
```

В PowerShell для копирования используйте `Copy-Item .env.example .env`.
Первая сборка скачивает зависимости и модели; ей нужны интернет и несколько гигабайт свободного места. Модели включены в образ. Сервис использует CPU, работает от непривилегированного пользователя и слушает `127.0.0.1:8000`. Для внешнего доступа настройте reverse proxy с HTTPS.

Документация API: [Swagger UI](http://localhost:8000/docs), [OpenAPI](http://localhost:8000/openapi.json).

## Прямой вызов из внешней системы

Основной endpoint — **`POST /v1/recognize`**. Передайте один или несколько документов в multipart-поле `files`. Сервис выполнит распознавание и в том же HTTP-ответе вернёт ZIP (`Content-Type: application/zip`). Опрос очереди для этого сценария не нужен.

Передавайте секрет в заголовке `X-API-Key`. В примерах переменная `DOCPIPE_API_KEY` должна содержать ключ из `.env`: Compose сам не экспортирует его в оболочку.

```sh
curl --fail-with-body --max-time 660 http://localhost:8000/v1/recognize \
  -H "X-API-Key: $DOCPIPE_API_KEY" \
  -F "files=@contract.pdf" \
  -F "files=@invoice.png" \
  -o recognized.zip
```

В PowerShell используйте `curl.exe` и `$env:DOCPIPE_API_KEY`. Для системы, работающей в той же Docker-сети, адрес сервиса — `http://docpipe:8000/v1/recognize`.

Пример структуры ответа:

```text
recognized.zip
├── manifest.json
├── document_001/
│   ├── document.json
│   ├── document.md
│   ├── document.html
│   ├── document.docx
│   ├── document-flow.pdf
│   ├── document-positional.pdf
│   ├── images/                 # если документ содержит изображения
│   └── ...                    # отчёты качества и проверки
└── document_002/
    └── ...
```

Папки нумеруются в порядке входных файлов; одинаковые имена исходников не конфликтуют. `document.json` содержит распознанную структуру, текст, таблицы и координаты для последующей машинной обработки. Остальные форматы предназначены для просмотра и редактирования.

```json
{
  "batch_id": "...",
  "status": "completed",
  "documents": [
    {
      "source_filename": "contract.pdf",
      "index": 1,
      "status": "completed",
      "job_id": "...",
      "review_required": false,
      "directory": "document_001"
    }
  ]
}
```

**Всегда проверяйте `manifest.json`.** Если часть документов не распознана, ответ также содержит ZIP и HTTP 200, но `status` равен `partial`. У неудачного документа будут `status: failed`, `http_status` и `error`; папки результатов для него не будет. Заголовок `X-Batch-Status` также содержит `completed` или `partial`. Если не удалось распознать ни одного документа, сервис возвращает HTTP 422 с JSON-ошибкой.

Готовый Python-клиент: `examples/recognize.py`. Зависимость `httpx` нужна только на стороне вызывающей системы:

```sh
pip install httpx
python examples/recognize.py contract.pdf invoice.png --output recognized.zip
```

Клиент берёт ключ из `DOCPIPE_API_KEY`, сохраняет ZIP, проверяет manifest и завершает работу с кодом 2 при частичном результате.

Входные форматы: PDF, PNG, JPEG, TIFF, BMP, WebP, включая многостраничные документы. По умолчанию один запрос принимает до 16 файлов общим размером до 200 МБ, каждый документ — до 300 страниц. На обработку всего запроса отводится 600 секунд. Документы внутри комплекта обрабатываются последовательно. Таймаут клиента и reverse proxy задайте больше таймаута сервиса.

При таймауте возвращается HTTP 504 с JSON; уже запущенное задание продолжает обработку. В деталях ошибки указан `job_id`, по которому можно получить статус и скачать результат через `/v1/jobs/{id}` и `/v1/jobs/{id}/result`. Если комплект прервался из-за таймаута или полной очереди, JSON также содержит сведения об уже обработанных документах. Повторная отправка создаёт новые задания.

Для одного файла также доступен `POST /v1/parse` с полем `file`: в ответе — непосредственно комплект результатов одного документа без общей папки и batch-manifest. Заголовки `X-Job-ID` и `X-Review-Required` описывают результат.

Базовый комплект экспорта формируется всегда. Чтобы добавить PDF с поисковым слоем, передайте multipart-поле `options`:

```json
{"engine":"docling","outputs":["json","md","html","docx","pdf-flow","pdf-positional","pdf-searchable"]}
```

| Endpoint | Назначение |
|---|---|
| `POST /v1/recognize` | Документы → распознавание → общий ZIP в ответе |
| `POST /v1/parse` | Один документ → ZIP в ответе |
| `POST /v1/jobs` | Асинхронная отправка одного документа, поле `file`, ответ 202 |
| `GET /v1/jobs/{id}` | Статус и прогресс |
| `GET /v1/jobs/{id}/result` | Скачать ZIP завершённого задания |
| `GET /v1/jobs/{id}/files/{path}` | Скачать отдельный файл |
| `DELETE /v1/jobs/{id}` | Удалить результат или отменить задание в очереди |
| `GET /healthz` | Процесс отвечает |
| `GET /readyz` | Модели загружены, сервис готов |
| `GET /metrics` | Счётчики заданий, требуется ключ |

Ошибки возвращаются как JSON с полем `detail`: неверный ключ — 401, пустой файл или неверные options — 400, превышение лимитов — 413, неподдерживаемый формат — 415, ошибка распознавания — 422, полная очередь — 429, сервис не готов — 503, таймаут — 504.

## Настройка и эксплуатация

Настройки задаются переменными `DOCPIPE_`, вложенные поля разделяются `__`. Поля и значения по умолчанию описаны в `config.example.yaml` и `src/docpipe/config.py`. API читает переменные окружения; YAML используется CLI через `--config`.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `DOCPIPE_API__WORKERS` | 1 | Потоки обработки; один экземпляр OCR выполняется последовательно |
| `DOCPIPE_API__MAX_QUEUE` | 16 | Максимальная очередь ожидания |
| `DOCPIPE_API__MAX_JOBS` | 1000 | Максимум сохранённых заданий |
| `DOCPIPE_API__RESULT_TTL_SECONDS` | 3600 | Срок хранения после завершения |
| `DOCPIPE_LIMITS__MAX_FILE_MB` | 200 | Размер входного файла |
| `DOCPIPE_LIMITS__MAX_PAGES` | 300 | Максимум страниц |
| `DOCPIPE_API__MAX_BATCH_FILES` | 16 | Максимум документов в одном запросе |
| `DOCPIPE_API__MAX_BATCH_MB` | 200 | Общий размер входных файлов запроса |
| `DOCPIPE_API__SYNC_MAX_PAGES` | 300 | Максимум страниц одного документа в прямом API |
| `DOCPIPE_API__SYNC_TIMEOUT_SECONDS` | 600 | Таймаут ожидания синхронного API |

Compose включает обязательный API-ключ. При запуске вне Compose задайте `DOCPIPE_API__REQUIRE_API_KEY=true` и `DOCPIPE_API__API_KEY` самостоятельно. Проверки здоровья доступны без ключа.

Используйте **один процесс Uvicorn и одну реплику на том заданий**: очередь работает в памяти. Завершённые задания и результаты восстанавливаются после перезапуска из Docker volume; прерванные задания получают статус `failed` и требуют повторной отправки. TTL-очистка работает в фоне и при запуске. Удаление volume уничтожит результаты.

```sh
docker compose --env-file .env -f deploy/docker-compose.yml logs -f
docker compose --env-file .env -f deploy/docker-compose.yml down
```

В Docker Compose включена текстовая проверка OCR и экспортированных документов через AITunnel API. Задайте `AITUNNEL_API_KEY` в `.env`; модель выбирается через `DOCPIPE_REVIEW_MODEL`. Само OCR выполняется локально, а текст для проверки отправляется во внешний API. Локальная LLM не используется. Для запуска вне Compose задайте `DOCPIPE_OFFLINE=false`, `DOCPIPE_REVIEW__LLM_ENABLED=true` и `AITUNNEL_API_KEY`; пример CLI-конфигурации — `configs/aitunnel-review.yaml`. При недоступности проверки отчёт отмечает необходимость ручной проверки. Качество OCR зависит от исходного скана; результаты с `review_required=true` нужно проверить.

## Локальный запуск

Рекомендуется Python 3.11. Установите пакет `pip install ".[docling]"`, скачайте модели Docling и русскую модель командой `python scripts/download_russian_recognition.py`. Укажите `DOCLING_ARTIFACTS_PATH` и при необходимости `DOCPIPE_RU_REC_MODEL_DIR`, затем запустите `uvicorn docpipe.api.app:app --host 127.0.0.1 --port 8000 --workers 1`. Полная последовательность подготовки моделей зафиксирована в `Dockerfile`.

Исследовательские отчёты, наборы тестов и экспериментальные утилиты удалены из репозитория. Локальные `data/`, `models/`, `reports/` игнорируются Git и исключены из Docker-контекста. Лицензии зависимостей перечислены в `LICENSES.md`; шрифты поставляются со своими лицензиями.
