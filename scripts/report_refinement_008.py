"""Summarize the completed focused run and verify exported textual content."""
import json
from pathlib import Path
from html.parser import HTMLParser
from xml.etree import ElementTree as ET
from zipfile import ZipFile

root = Path(__file__).resolve().parents[1]
out = root / 'data/refinement_audit/focused'
report = json.loads((out/'comparison.json').read_text(encoding='utf-8'))
data = json.loads((out/'document.json').read_text(encoding='utf-8'))
class Parser(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]
    def handle_data(self, value): self.parts.append(value)
parser=Parser(); parser.feed((out/'document.html').read_text(encoding='utf-8'))
normalize=lambda text:' '.join(text.split())
html_text=normalize(' '.join(parser.parts))
with ZipFile(out/'document.docx') as archive:
    xml=ET.fromstring(archive.read('word/document.xml'))
ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
docx_text=normalize(' '.join(t.text or '' for t in xml.findall('.//w:t',ns)))
texts=[]
for page in data['pages']:
    for block in page['blocks']:
        texts.extend([c['text'] for c in block['table']['cells']] if block.get('table') else [block.get('text') or ''])
report['export_check']=dict(missing_docx=[t for t in texts if t and normalize(t) not in docx_text],
    missing_html=[t for t in texts if t and normalize(t) not in html_text],
    native_docx_tables=len(xml.findall('.//w:tbl',ns)))
(out/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
metrics=report['current']['metrics']
text=f'''# Проверка восстановления OCR — doc_tbl_008, 14.10.2026

Первопричина подтверждена на исходных пикселях: порог числа тёмных пикселей,
зависящий от ширины страницы, отделял верхние части букв от основного текста.
Восемь исходных кропов высотой 21–22 px при ширине 3099–4533 px уменьшались
RapidOCR до высоты 0. Пример: страница 1, (838,1190,4745,1212), 3907×22 → 1984×0.
Это ошибка сегментации текста; часть полос дополнительно содержит границы таблицы.
Кропы и координаты: ../original_candidates/candidates.json.

Изменения: порог проекции не зависит от ширины страницы; пропуски между штрихами
соединяются с учётом DPI; таблицы исключены из общего построчного прохода и читаются
в границах ячеек, включая существующую геометрию объединённых ячеек.
Длинные кропы разделяются по пустым колонкам без перекрытия и потери пикселей;
нераспиливаемые токены получают минимальные поля. Детекция использует исходное
изображение и координаты. ResizeImgError дополнительного прохода сохраняет
основной результат и явно переводит страницу в degraded; остальные исключения
не подавляются. Подтверждённые повторные чтения также обязаны сохранять числа
и знаки формул; обнаруженный обход этой проверки устранён в cell_recovery.
Диагностика содержит контекст, размеры, repr и traceback,
сохраняет до 100 записей на reader и до 64 MiB в каталоге, PNG до 4M пикселей.
Включение: DOCPIPE_OCR_DIAGNOSTICS=<каталог>.

В обычном прогоне обнаружена отдельная проблема памяти: Docling увеличивал
уже растеризованный вход ещё в 3 раза (9× пикселей). scale=1 сохраняет исходный
DPI 231 и модель; глобальный DPI не снижался. Первоначальные неудачные прогоны
сохранены в ../run и ../run_diagnostic. Пользовательские изменения не откатывались;
их исходный diff сохранён в ../preexisting.patch; safe_rapidocr — в ../safe_rapidocr.before.py.
AGENTS.md в проекте и родительских каталогах отсутствует.

Проверки: 29 адресных тестов подготовки, сегментации, восстановления и таблиц прошли.
Интеграционный набор ограничен doc_tbl_008. Результат: {[(p['index']+1,p['status'],p['blocks']) for p in report['current']['pages']]}.
CER без вставок: {metrics['reference_scoped_cer']:.4%}; WER без вставок: {metrics['reference_scoped_wer']:.4%}.
Обычные CER/WER: {metrics['cer']:.4%} / {metrics['wer']:.4%}.
Сохранённый результат 14.10: обе страницы failed, текста нет, все четыре метрики 100%.
Время OCR: {report['current']['timings']['parse_total']:.2f} с; сохранённый запуск: 93.01 с,
но завершался ошибками, поэтому время не сравнивает одинаковую выполненную работу.
DOCX: {report['export_check']['native_docx_tables']} редактируемые таблицы;
пропуски IR-текста при экспорте DOCX/HTML: {len(report['export_check']['missing_docx'])}/{len(report['export_check']['missing_html'])}.
Подробные метрики, операции alignment и предупреждения: comparison.json.

Ограничения: OCR не безошибочен; метрики показывают оставшиеся распознавания,
пропуски и вставки. Пустой результат базового запуска не позволяет доказать
отсутствие новых пропусков по сравнению с успешным результатом того запуска.
Проверка экспортов подтверждает сохранность текста IR и нативных таблиц, а не
пиксельное совпадение оформления. На документе нет иллюстраций для проверки;
код сохранения иллюстраций не изменён. Весь набор документов не запускался.
Неразрывные длинные токены без промежутков остаются на пути минимальных полей
и штатного уменьшения RapidOCR: сохранность деталей таких токенов отдельно не доказана.
'''
(out/'REPORT_RU.md').write_text(text,encoding='utf-8')
print(json.dumps(dict(metrics={k:metrics[k] for k in ('cer','wer','reference_scoped_cer','reference_scoped_wer')},
    export=report['export_check'],time=report['current']['timings']),ensure_ascii=True))
