import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from docpipe.config import AppConfig, ReviewConfig, load_config
from docpipe.ir import Document, EngineInfo, SourceInfo, Page, Block, BBox
from docpipe.pipeline import _memory_failure, _process_page
from docpipe.review import (_apply, valid_correction, review_before_render,
                            review_after_render, Corrections, Findings)
from docpipe.renderers import render_docx, render_pdf_positional
from docpipe.verify.checks import text_match


def document(text='Документ проверен', count=1):
    return Document(source=SourceInfo(file='scan.pdf',sha256='0'*64,type='pdf'),
        engine=EngineInfo(name='fake',version='test'),pages=[
            Page(index=i,width_pt=595.28,height_pt=841.89,raster_dpi=300,
                 size_px=(2480,3508),status='ok',text_layer='absent',blocks=[
                     Block(id='text',type='text',order=0,text=text,
                           bbox=BBox(x0=30,y0=30,x1=450,y1=65))]) for i in range(count)])


class RegressionTests(unittest.TestCase):
    def test_wrapped_numpy_allocation_error_is_recoverable(self):
        self.assertTrue(_memory_failure(RuntimeError('Unable to allocate 24.9 MiB')))
        self.assertFalse(_memory_failure(ValueError('invalid input')))
        wrapper=RuntimeError('wrapped');wrapper.__cause__=MemoryError()
        self.assertTrue(_memory_failure(wrapper))

    def test_memory_retry_resets_engine_and_keeps_first_retry_dpi(self):
        class Engine:
            name='fake';warnings=[];refinement_incomplete=False
            def __init__(self):self.calls=0;self.resets=0
            def analyze_page(self,*args):
                self.calls+=1
                if self.calls==1:raise RuntimeError('Unable to allocate 32 MiB')
                return []
            def reset_after_memory_error(self):self.resets+=1
        engine=Engine();dpis=[]
        def raster(page,dpi,limits):
            dpis.append(dpi)
            return SimpleNamespace(image=Image.new('RGB',(100,100),'white'),
                width_pt=100,height_pt=100,rotation=0,dpi=dpi)
        page=SimpleNamespace(get_size=lambda:(100,100),get_rotation=lambda:0)
        with patch('docpipe.pipeline.rasterize_page',side_effect=raster), self.assertLogs('docpipe.pipeline'):
            result=_process_page(page,0,'absent',engine,AppConfig(),None)
        self.assertEqual(result.status,'ok')
        self.assertEqual(dpis,[300,300]);self.assertEqual(engine.resets,1)

    def test_yaml_preserves_fields_but_environment_overrides_nested_setting(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.yaml'
            path.write_text('review:\n  llm_enabled: true\n  batch_chars: 800\n  api_model: custom\n')
            with patch.dict(os.environ,{'DOCPIPE_REVIEW__BATCH_CHARS':'10000'},clear=True):
                cfg=load_config(path)
            self.assertEqual(cfg.review.batch_chars,10000)
            self.assertEqual(cfg.review.api_model,'custom');self.assertTrue(cfg.review.llm_enabled)

    def test_grammar_negation_numbers_and_filenames_cannot_be_rewritten(self):
        for a,b in [('формата','формату'),('допускается','не допускается'),
                    ('допускается','недопускается'),('неправильный','правильный'),
                    ('2025','2026'),('file_name.dat','file.dat')]:
            with self.subTest(a=a):self.assertFalse(valid_correction(a,b))
        self.assertTrue(valid_correction('МТУРЕ','MTYPE'))
        self.assertTrue(valid_correction('докумеит','документ'))

    def test_replacements_are_atomic_and_respect_token_boundaries(self):
        doc=document('тест тестовый alpha beta')
        changed=_apply(doc,{'p1:text':{'тест':'факт','alpha':'beta','beta':'gamma'}})
        self.assertEqual(changed.pages[0].blocks[0].text,'факт тестовый beta gamma')
        self.assertEqual(changed.pages[0].blocks[0].bbox,doc.pages[0].blocks[0].bbox)

    def test_known_company_host_joined_to_folder_is_normalized_without_rewriting_folder(self):
        text='роlуиs.сотПолюс.Диск file_name.dat'
        result,report=review_before_render(document(text),AppConfig())
        self.assertEqual(result.pages[0].blocks[0].text,'polyus.comПолюс.Диск file_name.dat')
        self.assertEqual(len(report['corrections']),1)
        unknown,_=review_before_render(document('example.сотПолюс.Диск'),AppConfig())
        self.assertEqual(unknown.pages[0].blocks[0].text,'example.сотПолюс.Диск')

    def test_duplicate_word_loss_is_visible_to_verifier(self):
        report=text_match(document('слово слово'),'слово')
        self.assertEqual(report['recall'],.5);self.assertEqual(report['missing'],['слово'])

    def test_noop_suggestion_is_not_an_error_and_snapshot_is_refreshed(self):
        class Client:
            def request(self,*args):
                return Corrections(corrections=[dict(unit_id='p1:text',before='Документ',after='Документ',reason='ok',confidence=1)])
        cfg=AppConfig(review=ReviewConfig(llm_enabled=True))
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'new'
            _,report=review_before_render(document(),cfg,out,Client())
            self.assertEqual(report['status'],'completed')
            review_before_render(document('Новый документ'),AppConfig(),out)
            self.assertIn('Новый документ',(out/'document-ocr.json').read_text(encoding='utf-8'))

    def exports(self,out,doc):
        render_docx(doc,out/'document.docx',out)
        render_pdf_positional(doc,out/'document-positional.pdf',out)

    def test_export_checks_run_when_llm_disabled(self):
        doc=document('слово слово')
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);self.exports(out,document('слово'))
            report=review_after_render(doc,AppConfig(),out)
        self.assertEqual(report['pages_checked'],[1])
        self.assertTrue(report['review_required'])
        self.assertIn('pdf_text_missing',[i['kind'] for i in report['issues']])

    def test_spatial_composite_ocr_echo_is_not_reported_as_export_loss(self):
        doc=document('УТВЕРЖДЕНЫ')
        page=doc.pages[0]
        page.blocks[0]=page.blocks[0].model_copy(update={'bbox':BBox(x0=30,y0=30,x1=120,y1=45)})
        page.blocks.append(Block(id='second',type='text',order=1,text='Приложение',bbox=BBox(x0=140,y0=30,x1=220,y1=45)))
        page.blocks.append(Block(id='echo',type='text',order=2,text='УТВЕРЖДЕНЫ Приложение',bbox=BBox(x0=30,y0=30,x1=220,y1=45)))
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);self.exports(out,document('УТВЕРЖДЕНЫ Приложение'))
            report=review_after_render(doc,AppConfig(),out)
        self.assertEqual(report['issues'],[])
        self.assertEqual(report['overlapping_ocr_echoes_excluded'][0]['block_id'],'echo')

    def test_call_budget_never_skips_deterministic_page_checks(self):
        class Client:
            def request(self,*args):return Findings(issues=[])
        doc=document(count=3)
        cfg=AppConfig(review=ReviewConfig(llm_enabled=True,max_calls=2))
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);self.exports(out,doc)
            report=review_after_render(doc,cfg,out,Client())
        self.assertEqual(report['pages_checked'],[1,2,3])
        self.assertEqual(report['pages_llm_unchecked'],[2,3])
        self.assertEqual(report['status'],'partial')

    def test_api_failure_keeps_all_export_checks(self):
        class Client:
            def request(self,*args):raise TimeoutError('private text must not enter report')
        cfg=AppConfig(review=ReviewConfig(llm_enabled=True))
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);self.exports(out,document(count=3))
            report=review_after_render(document(count=3),cfg,out,Client())
        self.assertEqual(report['pages_checked'],[1,2,3])
        self.assertEqual(report['pages_llm_unchecked'],[1,2,3])
        self.assertNotIn('private text',json.dumps(report))

    def test_unquoted_llm_claims_are_not_verified_export_failures(self):
        class Client:
            def request(self,*args):return Findings(issues=[dict(page=1,kind='text_loss',detail='missing whole page')])
        cfg=AppConfig(review=ReviewConfig(llm_enabled=True))
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);self.exports(out,document())
            report=review_after_render(document(),cfg,out,Client())
        self.assertEqual(report['issues'],[])
        self.assertEqual(len(report['unverified_suggestions']),1)
        self.assertEqual(report['status'],'completed')

    def test_unreadable_docx_does_not_skip_pdf_checks(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);render_pdf_positional(document(count=2),out/'document-positional.pdf',out)
            report=review_after_render(document(count=2),AppConfig(render={'docx_enabled':True}),out)
        self.assertEqual(report['pages_checked'],[1,2])
        self.assertIn('docx_read_error',[i['kind'] for i in report['issues']])

class AdditionalRegressions(unittest.TestCase):
    def test_progress_does_not_show_complete_while_render_is_pending(self):
        from docpipe.api.jobs import JobManager
        job=SimpleNamespace(progress=0,pages_total=0)
        JobManager._progress(job,15,15)
        self.assertEqual(job.progress,90)

    def test_external_http_error_does_not_leak_provider_response(self):
        from urllib.error import HTTPError
        from docpipe.remote_llm import AITunnelClient
        with patch.dict(os.environ,{'AITUNNEL_API_KEY':'test-secret'}):
            client=AITunnelClient(ReviewConfig())
        opener=SimpleNamespace(open=lambda *args,**kwargs:(_ for _ in ()).throw(
            HTTPError('https://api.aitunnel.ru',429,'private provider text',{},None)))
        with patch('docpipe.remote_llm.build_opener',return_value=opener):
            with self.assertRaisesRegex(RuntimeError,'AITunnel HTTP 429') as error:
                client.request('Review',{},Corrections)
        self.assertNotIn('private',str(error.exception))
        self.assertNotIn('test-secret',str(error.exception))

    def test_expired_stage_deadline_cannot_start_another_api_request(self):
        from docpipe.remote_llm import AITunnelClient
        with patch.dict(os.environ,{'AITUNNEL_API_KEY':'test-secret'}):
            client=AITunnelClient(ReviewConfig())
        client.set_deadline(0)
        with patch('docpipe.remote_llm.build_opener') as opener:
            with self.assertRaises(TimeoutError):client.request('Review',{},Corrections)
            opener.assert_not_called()

    def test_review_flags_reach_verification(self):
        from docpipe.verify.checks import verify_document
        from docpipe.renderers import render_markdown,render_html,render_pdf_flow
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);doc=document()
            (out/'document.json').write_text(doc.model_dump_json(),encoding='utf-8')
            render_docx(doc,out/'document.docx',out)
            render_pdf_positional(doc,out/'document-positional.pdf',out)
            render_markdown(doc,out/'document.md');render_html(doc,out/'document.html')
            render_pdf_flow(doc,out/'document-flow.pdf',out)
            (out/'review-after.json').write_text('{"status":"partial","review_required":true}')
            report=verify_document(out)
        review=next(c for c in report['checks'] if c['check']=='document_review')
        self.assertEqual(review['status'],'WARN')
        self.assertTrue(report['ok'])
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);doc=document()
            (out/'document.json').write_text(doc.model_dump_json(),encoding='utf-8')
            (out/'review-after.json').write_text('{"issues":[{"kind":"pdf_text_missing"}],"review_required":true}')
            report=verify_document(out)
            self.assertEqual(next(c for c in report['checks'] if c['check']=='export_text_integrity')['status'],'FAIL')

if __name__=='__main__':unittest.main()
