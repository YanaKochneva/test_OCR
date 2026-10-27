# Перепроверка фрагментов через Nemotron 3.5 Parse

Основной OCR остаётся Docling + RapidOCR. Nemotron локально выполняет
повторное чтение кропов текстовых блоков и ячеек. Эталон модели не передаётся.
Сохраняются ограничения словарных исправлений из `nemotron_refinement.py`.

В JupyterLab используйте `process_documents.ipynb`: Python берётся из текущего
ядра, зависимости уже должны быть установлены. В настройках задайте корень
проекта и пути `DOCLING_MODELS`, `RAPIDOCR_MODELS`, `RUSSIAN_MODEL`,
`NEMOTRON_MODEL`. Перепроверка Nemotron включена. По умолчанию используется CPU.
Notebook проверяет локальные файлы и не устанавливает пакеты и не скачивает модели.

Пример структуры папок (имена каталогов можно изменить в notebook):

```text
models/
  docling/                  # артефакты layout/table с исходной структурой Docling
  rapidocr/                 # ONNX-файлы, перечисленные ниже
  ppocrv6-medium-ru/         # model.onnx и rec_keys.txt
  nemotron-3.5-parse/        # веса, конфигурации и код NVIDIA
```

В `rapidocr/` нужны `ch_PP-OCRv5_det_mobile.onnx`,
`ch_ppocr_mobile_v2.0_cls_mobile.onnx`, `eslav_PP-OCRv5_rec_mobile.onnx`
и `latin_PP-OCRv5_rec_mobile.onnx`. При запуске CLI соответствующий каталог
можно задать через `DOCPIPE_RAPIDOCR_MODEL_DIR`. Пути распознавания RU и
артефактов Docling задаются через `DOCPIPE_RU_REC_MODEL_DIR` и
`DOCLING_ARTIFACTS_PATH`. Локальные пути не зависят от окружения ядра.

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
