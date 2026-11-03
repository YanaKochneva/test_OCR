# DocPipe: запуск и подключение

Распознавание PDF и изображений через Docling + RapidOCR с русской PP-OCRv6.
Локальный Nemotron используется для перепроверки текста. Доступны запуск
одного документа из терминала, HTTP API, Docker и notebook.

Инструкция рассчитана на уже настроенную среду сервера. Команд установки
и обновления пакетов здесь нет. Модели уже находятся на сервере.

## 1. Открыть терминал на сервере

Можно использовать терминал JupyterLab. Все команды ниже — для Bash/Linux.

Перейдите в папку проекта, которую проверяли при запуске:

```bash
cd /home/jupyter/rag-project/src/rag_project/notebooks/kochneva/project/test_OCR-codex-nemotron-3.5-parse
```

Если используете другую копию проекта, измените только этот путь.
В выбранной папке должны находиться `src/docpipe`, `configs` и `pyproject.toml`.

## 2. Подключить исходники и локальные модели

Выполните в том же терминале. После открытия нового терминала повторите команды.
Активация виртуального окружения не требуется: далее указан полный путь Python.

```bash
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
export DOCLING_ARTIFACTS_PATH=/home/jupyter/rag-project/src/rag_project/models/docling-models
export DOCPIPE_RAPIDOCR_MODEL_DIR=/home/jupyter/rag-project/src/rag_project/models/RapidOCR
export DOCPIPE_RU_REC_MODEL_DIR=/home/jupyter/rag-project/src/rag_project/models/PP-OCRv6_medium_rec_ru_onnx
export DOCPIPE_REVIEW__NEMOTRON_MODEL=/home/jupyter/rag-project/src/rag_project/models/Nemotron-3.5-Parse
export DOCPIPE_REVIEW__NEMOTRON_DEVICE=cpu
export DOCPIPE_REVIEW__PROVIDER=nemotron
export DOCPIPE_REVIEW__LLM_ENABLED=true
export DOCPIPE_REVIEW__TIMEOUT_SECONDS=120
export DOCPIPE_REVIEW__STAGE_TIMEOUT_SECONDS=900
export DOCPIPE_REVIEW__MAX_CALLS=300
export DOCPIPE_OFFLINE=true
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_MODULES_CACHE=/home/jupyter/rag-project/src/rag_project/models/hf-modules
export DOCPIPE_OCR_THREADS=6
export OMP_NUM_THREADS=6
export MKL_NUM_THREADS=6
```

Серверные пути моделей сохранены. Отдельный путь
`/home/jupyter/rag-project/src/rag_project/models/docling-layout-heron`
текущий адаптер не использует: артефакты Docling задаются через
`DOCLING_ARTIFACTS_PATH`.

Проверить Python, пакет PDF и выбранную копию исходников:

```bash
/home/jupyter/rag-project/.venv/bin/python -c 'import sys, pypdfium2, docpipe; print("Python:", sys.executable); print("PDF:", pypdfium2.__file__); print("Проект:", docpipe.__file__)'
```

Команда ничего не устанавливает. Если импорт завершается ошибкой, проверьте
путь Python и окружение именно этого терминала. Несовпадение версий с
`pyproject.toml` само по себе не требует переустановки работающей среды.
Реально отсутствующий обязательный модуль всё же остановит обработку.

## 3. Обработать документ из терминала

Подставьте имя своего файла. Пути с пробелами заключайте в кавычки.

```bash
/home/jupyter/rag-project/.venv/bin/python -m docpipe parse \
  "data/input/документ.pdf" \
  --config configs/nemotron-review.yaml \
  --out output/result
```

Входом может быть PDF или изображение. Один вызов CLI обрабатывает один файл.
Для следующего документа укажите другую папку `--out`, чтобы сохранить
результаты предыдущего запуска. Ход обработки выводится в терминал.

Обязательные результаты:

| Файл | Содержимое |
|---|---|
| `document.json` | Страницы, блоки, текст, таблицы и координаты |
| `document.md` | Текст в Markdown |
| `document.html` | HTML-документ |
| `document-flow.pdf` | PDF с последовательным размещением текста |
| `document-positional.pdf` | PDF с восстановленным расположением блоков |

Извлечённые изображения сохраняются рядом, если они есть в документе.
Для DOCX и дополнительного поискового PDF:

```bash
export DOCPIPE_RENDER__DOCX_ENABLED=true
/home/jupyter/rag-project/.venv/bin/python -m docpipe parse \
  "data/input/документ.pdf" \
  --config configs/nemotron-review.yaml \
  --out output/result-with-docx \
  --outputs md html docx pdf-flow pdf-positional pdf-searchable
```

Для запуска без перепроверки Nemotron:

```bash
export DOCPIPE_REVIEW__LLM_ENABLED=false
```

Вернуть перепроверку: `export DOCPIPE_REVIEW__LLM_ENABLED=true`.
Для распознавания таблиц на изображениях задайте `export DOCPIPE_IMAGE_OCR=docling`.
По умолчанию изображения обрабатываются прямым RapidOCR без восстановления таблиц;
PDF обрабатываются Docling.

## 4. Запустить HTTP API в готовой среде

Выполните разделы 1–2, затем запустите сервер:

```bash
/home/jupyter/rag-project/.venv/bin/python -m uvicorn docpipe.api.app:app \
  --host 127.0.0.1 --port 8000 --workers 1 --timeout-graceful-shutdown 310
```

API использует переменные окружения. YAML из команды CLI автоматически
не загружается. Настройки раздела 2 включают локальный Nemotron.
Остановка сервера в терминале: `Ctrl+C`.

Проверка из второго терминала сервера:

```bash
curl --fail http://127.0.0.1:8000/healthz
curl --fail http://127.0.0.1:8000/readyz
```

`/readyz` показывает готовность обработки после загрузки OCR-моделей.
Документация запросов: `http://127.0.0.1:8000/docs`.

Обработать файл и скачать ZIP результатов:

```bash
curl --fail-with-body --max-time 660 \
  http://127.0.0.1:8000/v1/recognize \
  -F 'files=@data/input/документ.pdf' \
  -o recognized.zip
```

Можно передать несколько полей `files`. ZIP содержит `manifest.json` и папки
документов. Проверяйте манифест: частичный успех возвращает HTTP 200 со статусом
`partial`; если не обработан ни один документ, возвращается HTTP 422.

Для долгой обработки используйте задания:

| Запрос | Назначение |
|---|---|
| `POST /v1/jobs` | Передать файл в поле `file`, получить `job_id` |
| `GET /v1/jobs/{job_id}` | Узнать статус и прогресс |
| `GET /v1/jobs/{job_id}/document` | Получить текст и структуру JSON |
| `GET /v1/jobs/{job_id}/result` | Скачать ZIP после завершения |
| `GET /v1/jobs/{job_id}/files/{path}` | Скачать отдельный результат |
| `DELETE /v1/jobs/{job_id}` | Удалить результат или ожидающее задание |

Пример создания задания:

```bash
curl --fail-with-body http://127.0.0.1:8000/v1/jobs \
  -F 'file=@data/input/документ.pdf'
```

Статусы: `queued`, `running`, `completed`, `failed`, `cancelled`.
При ошибке причина находится в поле `error`. При прямом запуске результаты
и метаданные по умолчанию хранятся в `data/jobs` относительно папки запуска.
Используйте один процесс API на каталог заданий.

### Подключение к API со своего компьютера

Запущенный на `127.0.0.1` сервис доступен с самого сервера. Для доступа
с компьютера откройте SSH-туннель, заменив пользователя и адрес сервера:

```bash
ssh -N -L 8000:127.0.0.1:8000 USER@SERVER
```

Пока туннель работает, открывайте `http://127.0.0.1:8000/docs` на своём компьютере
и отправляйте запросы на этот адрес. При отправке через локальный `curl` путь
после `@` должен указывать на файл на вашем компьютере.
Для SSH нужны адрес сервера и ваши обычные учётные данные.

При необходимости включите ключ доступа перед запуском API:

```bash
export DOCPIPE_API__REQUIRE_API_KEY=true
export DOCPIPE_API__API_KEY='YOUR_SECRET_AT_LEAST_32_CHARACTERS_LONG'
```

В запросах передавайте `-H 'X-API-Key: YOUR_SECRET_AT_LEAST_32_CHARACTERS_LONG'`.

## 5. Docker

Docker — отдельный способ запуска. Сборка устанавливает пакеты внутри образа,
скачивает модели Docling/RapidOCR и копирует русскую модель из
`models/ppocrv6-medium-ru/` в контексте сборки. Она не использует автоматически
серверные пути раздела 2. Локальный Nemotron в стандартном Compose не включён.

Если образ уже собран:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up -d --no-build
```

Для первоначальной сборки подготовьте `.env` на основе `.env.example`
и положите `model.onnx` и `rec_keys.txt` в `models/ppocrv6-medium-ru/`, затем:

```bash
docker compose --env-file .env -f deploy/docker-compose.yml up --build -d
```

Не заменяйте существующий `.env`, если он уже настроен.
Адрес API: `http://127.0.0.1:8000`; результаты хранятся в Docker volume.
В `.env` Compose используются `DOCPIPE_DOCX_ENABLED`, `DOCPIPE_REQUIRE_API_KEY`
и `DOCPIPE_API_KEY`; для прямого запуска Python используются вложенные имена
из предыдущих разделов. Compose по умолчанию работает без перепроверки;
его `DOCPIPE_REVIEW_ENABLED` включает внешний AITunnel, а не локальный Nemotron.

## Notebook и структура проекта

`process_documents.ipynb` использует то же готовое окружение. Выполните три ячейки:
настройки → проверка → обработка. Проверка пакетов только предупреждает,
не устанавливает ничего и не требует совпадения версий.
Результаты: `reports/notebook-runs/`.

Код распознавания: `src/docpipe/pipeline.py`, `engines/`, `stages/`.
Экспорт: `src/docpipe/renderers/`. HTTP-сервис: `src/docpipe/api/`.
Настройки: `src/docpipe/config.py`. Зависимости описаны только в `pyproject.toml`.
Для CLI переменные окружения `DOCPIPE_` имеют приоритет над YAML.
Модели, окружение и результаты исключены из Git.
Лицензия встроенных шрифтов: `src/docpipe/assets/fonts/LICENSE-DejaVu.txt`.


## Чеки

По умолчанию включён отдельный этап `receipts`. Он ищет области чеков на
отрисованной странице, включая PDF из множества фрагментов, и проверяет
ориентации 0/90/180/270°. Исходные блоки документа остаются сохранены.
Обнаружение эвристическое: явное название кассового/фискального чека либо
несколько фискальных реквизитов вместе с признаком оплаты, номер чека
с числовыми данными или признаки банковского слипа. Небольшой наклон
исправляется только при согласии нескольких обнаруженных линий. Это не гарантия
обнаружения выцветших, обрезанных чеков и чеков без русских реквизитов.

Для числовых строк и обозначений итоговой суммы используются два чтения:
основное чтение чека и одно чтение увеличенной контрастной строки (другой
локальной моделью, если она доступна). Выбирается непустой вариант с большей
уверенностью, даже при очень низкой уверенности. При равенстве остаётся
первое чтение. Если оба чтения пусты, текст не выдумывается.

Обозначения «ИТОГ», «ИТОГО», «ВСЕГО», «К ОПЛАТЕ», «СУММА К ОПЛАТЕ»
сохраняются, если найдены хотя бы в одном чтении. Строки чека не удаляются
и не заменяются картинкой из-за низкой уверенности. Выбранный текст
попадает непосредственно на исходную страницу чека. Исходный скан страницы
как подложка не используется. Расхождения и оценки сохраняются в JSON.

Проверяются явные выражения количество × цена = сумма; арифметика не
переписывает распознанные цифры. QR-коды читаются локально.
Результаты диагностики находятся в `receipts.json`, `pages[].receipts` и
кропах `receipts/`. Проверочные листы в PDF и DOCX не добавляются.
На страницу действуют ограничения: до 16 областей и целой страницы для
поиска, до 8 чеков, до 100 числовых строк на чек. Пропуск по лимиту отражается
в предупреждении или признаке ручной проверки. Поиск добавляет время OCR.

Отключение: `DOCPIPE_RECEIPTS__ENABLED=false`. Параметры YAML:

```yaml
receipts:
  enabled: true
  scout_max_side: 1800
  max_receipts_per_page: 8
  max_numeric_lines: 100
  min_numeric_confidence: 0.9
```


### Правило сборки PDF

PDF собирается из распознанных текстовых блоков, редактируемых таблиц и
отдельно выделенных изображений. Запрещено использовать исходный скан
страницы как фон или подложку и накладывать на него невидимый OCR-текст.
Это правило действует также для `pdf-searchable` и вспомогательных скриптов.
Изображения отдельных чеков в приложении и локальные фрагменты непрочитанных
областей сохраняются как самостоятельные иллюстрации, а не фон страницы.
