"""Write a Russian score report without altering OCR artifacts or metric policy."""
import json
import sys
from pathlib import Path


def main():
    folder=Path(sys.argv[1])
    report=json.loads((folder/'report.json').read_text(encoding='utf-8'))
    lines=['# PP-OCRv6 Medium RU — контроль doc_img_003 и doc_img_004','',
        'Распознавание выполнялось локально. Эталонное содержимое не передавалось модели.', '',
        '| Документ | Статус | CER | WER | CER без вставок | WER без вставок |',
        '|---|---|---:|---:|---:|---:|']
    for case in report['cases']:
        score=case.get('text')
        values=[f'{100*score[key]:.3f}%' for key in ('cer','wer','reference_scoped_cer','reference_scoped_wer')] if score else ['—']*4
        lines.append('| '+ ' | '.join([case['case'],case['status'],*values])+' |')
    summary=report.get('summary',{})
    keys=('macro_cer','macro_wer','macro_reference_scoped_cer','macro_reference_scoped_wer')
    if all(summary.get(k) is not None for k in keys):
        lines.append('| Среднее по документам | '+str(summary['scored_cases'])+' оценены | '+' | '.join(f'{100*summary[k]:.3f}%' for k in keys)+' |')
    lines.extend(['','Обычные CER/WER учитывают вставки, замены и удаления. По согласованной оценке «без вставок» из расстояния исключаются только операции вставки относительно эталона; они остаются в выходных документах. Это оценка с использованием эталона, а не стандартный CER/WER.',
        '', 'Нормализация: регистр, ё/е и пробелы. Порядок текста учитывается. Точные формулы и счётчики ошибок находятся в report.json.',
        '', 'Оформление остаётся предварительным; этот прогон проверяет качество распознавания. Работа над корректировкой оформления приостановлена по указанию пользователя.'])
    manifest=Path('models/ppocrv6-medium-ru/manifest.json')
    if manifest.exists():
        (folder/'model-manifest.json').write_bytes(manifest.read_bytes())
        model=json.loads(manifest.read_text(encoding='utf-8'))
        lines.extend(['','Модель: '+model['repo']+'. Версия: `'+model['revision']+'`. Контрольные суммы: model-manifest.json.'])
    (folder/'RESULTS_RU.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines))


if __name__=='__main__':
    main()
