# DocPipe

Постраничное распознавание PDF и изображений с восстановлением текста, таблиц и рисунков.
Основной маршрут: Docling + PP-OCRv5 через RapidOCR.

## Локальный запуск на этом компьютере

```powershell
Set-Location 'C:\Users\andre\Desktop\test_OCR'
$env:PYTHONPATH = "$PWD\src"
& 'D:\AI\venvs\test_OCR\Scripts\python.exe' -m uvicorn docpipe.api.app:app --host 127.0.0.1 --port 8000
```

Откройте `http://localhost:8000/`. В обычном локальном режиме ключ не требуется.
Настройки могут переопределяться переменными окружения и YAML.

## Установка в новое окружение

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c constraints.txt -e '.[docling]'
```

OCR требует заранее загруженных моделей. Перенос исходного кода без зависимостей
и моделей не обеспечивает автономную работу.

## Документация

- [Объяснение и обоснование компонентов](docs/PROJECT_STRUCTURE_RU.md).
- [Интерфейс](docs/USER_INTERFACE.md).
- [Последние подтверждённые метрики](docs/QUALITY_BASELINE_RU.md).
- [Текущая схема](docs/ocr-architecture-best-styled.drawio).
- [Ограничения готовности](docs/PRODUCTION_READINESS.md).
- [Оценка качества на новых документах](docs/BENCHMARK.md).
- [Журнал очистки](docs/cleanup-20261013.json).

Вход: PDF, PNG, JPG/JPEG, TIFF, BMP, WebP. DOCX используется как эталон оценки.
Результаты создаются в `data/jobs/` или выбранной пользователем папке.
Тестовые наборы, временные файлы и старые выходные документы удалены по запросу.
Оценщик CER/WER сохранён как функция приложения.
