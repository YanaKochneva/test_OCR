# Перепроверка фрагментов через Nemotron 3.5 Parse

Основной OCR остаётся Docling + RapidOCR. Nemotron заменяет GLM на этапе
повторного чтения кропов текстовых блоков и ячеек. Эталон модели не передаётся.
Сохраняются ограничения словарных исправлений из `glm_refinement.py`.

Модель: https://huggingface.co/nvidia/Nemotron-3.5-Parse.
Локальные веса и официальный пакет `nemotron_parse` должны находиться вместе
в `models/nemotron-3.5-parse`. Вызовы модели используют `local_files_only=True`.
Необходимы PyTorch, совместимый torchvision, Transformers, timm, einops,
beautifulsoup4 и pymorphy3 с русским словарём. NVIDIA рекомендует Transformers
5.14.1, torchvision 0.26.0 и timm 1.0.22; CUDA-сборка PyTorch выбирается под GPU.

Загрузка:

```powershell
python -m pip install -e ".[nemotron-review]"
hf download nvidia/Nemotron-3.5-Parse --local-dir models/nemotron-3.5-parse
```

При обрывах больших загрузок вместо `hf download`:

```powershell
python scripts/download_nemotron.py
```

Этот загрузчик сохраняет части, докачивает небольшими диапазонами и проверяет
SHA-256 весов закреплённой ревизии перед использованием.

Запуск основного конвейера с новой перепроверкой:

```powershell
python -m docpipe parse input.pdf --config configs/nemotron-review.yaml --out reports/nemotron-run
```

Отчёт: `review-nemotron-reread.json`; кеш транскрипций: `nemotron-crops/`.
Настройки `review.nemotron_model` и `review.nemotron_device` задают папку модели
и устройство (`auto`, `cpu`, `cuda`). Таймаут проверяется между шагами генерации;
текущий GPU-шаг и загрузка весов не прерываются немедленно.

Сравнение до/после перепроверки на полном документе:

```powershell
python scripts/benchmark_review.py scan_no_text.pdf reference.pdf --out reports/nemotron-doc-tbl-006
```

Для повторной проверки с уже сохранённым OCR добавьте
`--baseline reports/nemotron-doc-tbl-006-score/baseline.json`. Для короткого
теста подключения добавьте `--max-calls 2`; отчёт будет помечен как частичный.
Повторный запуск с тем же `--out` использует кеш транскрипций и продолжает
перепроверку, если предыдущий запуск достиг лимита времени.

В CPU-тесте использовались `OMP_NUM_THREADS=6` и `MKL_NUM_THREADS=6`.

Скрипт сохраняет `baseline.json`, `reviewed.json`, отчёты и `metrics.json`.
CER — расстояние Левенштейна по полному тексту страниц в порядке чтения,
делённое на число символов эталона. Нормализация: Unicode NFC и схлопывание
пробелов; регистр, пунктуация и весь контент сохраняются. Перестановки текста
в таблицах и колонтитулах влияют на эту метрику. Текстовый PDF-эталон должен
иметь проверенный порядок извлечения текста. Частичная перепроверка явно
отмечается в `reviewer_status`, и её нельзя выдавать за полный прогон модели.

На `doc_tbl_006` (12 страниц) основной конвейер без модельной перепроверки дал
CER **0,7969%**: 201 правка Левенштейна на 25 224 символа эталона после
нормализации. Это результат Docling + RapidOCR, а не Nemotron.
Веса Nemotron загружены и проверены по SHA-256:
`81dd7609c589f3694ce75520c25af5010953231358de6aec4d03e8c6aac787e9`.
Полный CPU-прогон: перепроверено **144/144** выбранных кропов; CER снизился
с **0,7969% до 0,7889%** (201 → 199 правок Левенштейна).
Принято одно исправление смешанной латиницы и кириллицы в `веб-сайте`,
один слишком короткий ответ отклонён. Основной конвейер уже давал CER меньше 1%.
Сравнение с действующим GLM на этом файле не выполнялось: сервис был недоступен.
Итоговый отчёт: `reports/nemotron-doc-tbl-006-live/result.md`.
