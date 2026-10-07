# Отчёт Фазы 4 — PP-StructureV3 / PaddleOCR

## Сделано

- Реализован адаптер `PPStructureEngine` поверх публичного `PPStructureV3`.
- Все обращения к PaddleOCR изолированы в адаптере.
- Публичные сигнатуры `PPStructureV3` и `predict()` проверяются через `inspect.signature`.
- Для русского используется `lang="ru"` и явно задаётся модель `eslav_PP-OCRv5_mobile_rec`.
- Включено распознавание таблиц.
- Формулы преобразуются в IR.
- Layout labels преобразуются в `BlockType`.
- Reading order берётся из `parsing_res_list` и сохраняется в `order`.
- `overall_ocr_res` используется как fallback, если layout parsing не вернул блоков.
- Координаты из пикселей изображения переводятся в пункты IR.
- Таблицы преобразуются в `TableData`; HTML сохраняется из `pred_html`.
- Ячейки таблиц преобразуются в `TableCell`.
- GPU: `gpu=None` — автоопределение; `gpu=False` — CPU; `gpu=True` — обязательный GPU, при его отсутствии выдаётся `DependencyError`.
- Добавлен контрактный fake-тест без PaddleOCR.
- Зафиксированы `paddleocr==3.7.0` и `paddlepaddle==3.3.1`.

## Проверено запуском

Команда:

```text
PYTHONPATH=src python -m pytest -q tests
```

Результат:

```text
15 passed, 1 skipped
```

Команда:

```text
python scripts/check_licenses.py
```

Результат: запрещённых лицензий не обнаружено.

Контрактный fake-тест проверяет:

- создание pipeline;
- передачу `lang="ru"`;
- преобразование figure/text/table/caption;
- уникальность `order`;
- bbox внутри страницы;
- HTML и ячейки таблицы.

## Не проверено и почему

- Реальный PaddleOCR inference — пакет `paddleocr` отсутствует в текущем окружении.
- Реальные веса PP-StructureV3 и `eslav_PP-OCRv5_mobile_rec` — не скачивались.
- Реальные CER/WER/F1 PP-StructureV3 — нет inference.
- Реальный CPU/GPU latency — нет inference.
- Реальное сравнение Docling vs PP-Structure — Docling и PaddleOCR отсутствуют.

Установка из сети в текущем рабочем окружении невозможна, поэтому fake harness является единственным честно запускаемым контрактным тестом.

## Проверка публичного API

По официальной документации PP-StructureV3 публичный Python API имеет вид `PPStructureV3(...)` + `pipeline.predict(...)`. Result предоставляет публичный `json`, содержащий `parsing_res_list`, `overall_ocr_res`, `formula_res_list` и `table_res_list`. В `parsing_res_list` порядок элементов является reading order. Официальная документация также указывает `lang="ru"` среди поддерживаемых языков PP-OCRv5 и отдельно описывает `eslav_PP-OCRv5_mobile_rec` как модель для восточнославянских языков, английского и цифр. 

## Известные ограничения

- В IR нет отдельного типа `seal`, поэтому layout label `seal` временно маппится в `figure`; текст печати не должен попадать в `figure.text`.
- PP-StructureV3 возвращает изображения/markdown artifacts, но сохранение картинки в итоговый файл выполняется общим этапом figures, а не адаптером.
- Для таблиц `rowspan/colspan` не угадываются из приватных структур PaddleOCR: при наличии только `cell_box_list` строится детерминированная геометрическая row/col-разметка, а `pred_html` сохраняется как основной структурный результат.
- Текущий интерфейс `analyze_page(image, page_meta)` использует page-level numpy array; PDF-level streaming API PaddleOCR не используется внутри адаптера.

## Допущения

- `lang="ru"` используется как код языка, а `eslav_PP-OCRv5_mobile_rec` — как конкретная модель распознавания.
- `PP-DocLayout-L/default` в `EngineInfo` является логическим описанием layout-модуля; точное имя автоматически выбранного веса должно быть подтверждено при реальной загрузке модели.
- Без установленного PaddleOCR модельные SHA-256 не записываются и не объявляются известными.
