# Отчёт Фазы 5

## Сделано

- PDF из потока через WeasyPrint с запасным ReportLab Platypus.
- Позиционный PDF, выключенный по умолчанию и явно отмеченный как режим с возможной потерей нераспознанной графики.
- DOCX через python-docx.
- Searchable-PDF через pikepdf overlay: исходные страницы не пересобираются; текст создаётся отдельным невидимым слоем.
- Учитываются CropBox и /Rotate на уровне исходной страницы; координаты IR переводятся из верхнего левого начала в PDF bottom-left внутри overlay.
- Markdown/HTML figure caption выводится как подпись, привязанная к figure.
- Добавлены e2e-тесты PDF-flow, positional и DOCX.

## Проверено запуском

Команда:

```text
PYTHONPATH=src python -m pytest -q
```

Результат: `21 passed, 2 skipped`.

Также выполнены `compileall` и `scripts/check_licenses.py`.

## Не проверено и почему

- Реальный pikepdf searchable-PDF overlay — пакет `pikepdf==10.16.0` отсутствует в текущем офлайн-окружении.
- Проверка нулевой разницы рендера и image-stream hashes для searchable-PDF будет выполнена сразу после появления pikepdf.
- Реальный Docling inference по-прежнему не запускался: пакет/модели отсутствуют.

## Известные ограничения

- ReportLab fallback не повторяет CSS pagination WeasyPrint полностью.
- Позиционный рендерер не восстанавливает произвольные печати/подписи, если они не представлены figure-блоками.
- Для searchable-PDF Unicode-шрифт DejaVu Sans используется как свободный fallback.

## Допущения

- PDF-flow использует WeasyPrint при наличии; ReportLab — fallback.
- Searchable-PDF не меняет исходный визуальный слой, а добавляет только overlay.
- DOCX остаётся низкоприоритетным выходом, как указано в ТЗ.
