import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from reportlab.pdfgen import canvas
from docpipe.config import AppConfig,ReviewConfig
from docpipe.ir import SourceInfo
from docpipe.visual_review import review_formatting,VisualFindings,VisualIssue
from docpipe.remote_llm import AITunnelClient
from test_regressions import document


def pdf_file(path,count=1):
    pdf=canvas.Canvas(str(path),pagesize=(595.28,841.89))
    for index in range(count):
        pdf.drawString(30,750,'Document page '+str(index+1));pdf.showPage()
    pdf.save()


class VisualReviewTests(unittest.TestCase):
    def config(self,**kwargs):
        return AppConfig(offline=False,review=ReviewConfig(llm_enabled=True,visual_enabled=True,**kwargs))

    def test_actual_pair_payload_contains_two_embedded_images(self):
        captured={}
        response={'choices':[{'finish_reason':'stop','message':{'content':'{"readability":"adequate","issues":[]}'}}]}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps(response).encode()
        class Opener:
            def open(self,request,**kwargs):captured['body']=json.loads(request.data);return Response()
        with patch.dict('os.environ',{'AITUNNEL_API_KEY':'test-key'}):
            client=AITunnelClient(ReviewConfig())
        with patch('docpipe.remote_llm.build_opener',return_value=Opener()):
            client.request('Compare',{'page':1},VisualFindings,image_url=['data:image/jpeg;base64,eA==']*2)
        content=captured['body']['messages'][1]['content']
        self.assertEqual([p['type'] for p in content],['text','image_url','image_url'])
        self.assertEqual(captured['body']['response_format']['type'],'json_schema')
        self.assertTrue(captured['body']['response_format']['json_schema']['strict'])
        self.assertNotIn('test-key',json.dumps(captured['body']))

    def test_invalid_or_external_images_are_rejected(self):
        with patch.dict('os.environ',{'AITUNNEL_API_KEY':'test-key'}):
            client=AITunnelClient(ReviewConfig())
        with self.assertRaises(ValueError):client.request('Compare',{},VisualFindings,image_url=['https://example.com/scan.jpg'])
        with self.assertRaises(ValueError):VisualIssue(category='table',severity='major',detail='bad',bbox=[.5,0,.2,1],confidence=.9)

    def test_only_pdf_is_sent_for_visual_review(self):
        calls=[]
        class Client:
            def request(self,instruction,payload,response_type,image_url=None):
                calls.append(payload['format'])
                self_test.assertEqual(len(image_url),2)
                return VisualFindings(readability='adequate',issues=[])
        self_test=self
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);source=out/'source.pdf';target=out/'document-positional.pdf'
            pdf_file(source);pdf_file(target)
            doc=document().model_copy(update={'source':SourceInfo(file=str(source),sha256='0'*64,type='pdf')})
            report=review_formatting(doc,self.config(),out,Client())
        self.assertEqual(calls,['pdf']);self.assertFalse(report['review_required'])
        self.assertEqual(report['docx']['status'],'not_requested')

    def test_docx_does_not_cause_skipped_review_or_extra_requests(self):
        class Client:
            def request(self,*args,**kwargs):return VisualFindings(readability='adequate',issues=[])
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);source=out/'source.pdf';pdf_file(source);pdf_file(out/'document-positional.pdf')
            doc=document().model_copy(update={'source':SourceInfo(file=str(source),sha256='0'*64,type='pdf')})
            report=review_formatting(doc,self.config(),out,Client())
        self.assertEqual(report['pdf']['checked'],[1]);self.assertEqual(report['docx']['checked'],[])
        self.assertEqual(report['docx']['status'],'not_requested');self.assertFalse(report['review_required'])

    def test_budget_and_findings_are_auditable_and_do_not_modify_document(self):
        class Client:
            def request(self,*args,**kwargs):
                return VisualFindings(readability='adequate',issues=[dict(category='clipping',severity='major',
                    detail='Обрезан заголовок',bbox=[.1,.1,.5,.2],confidence=.95)])
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);source=out/'source.pdf';pdf_file(source,2);pdf_file(out/'document-positional.pdf',2)
            doc=document(count=2).model_copy(update={'source':SourceInfo(file=str(source),sha256='0'*64,type='pdf')})
            original=doc.model_dump_json()
            report=review_formatting(doc,self.config(visual_max_calls=1),out,Client())
            self.assertEqual(doc.model_dump_json(),original)
        self.assertEqual(report['pdf']['unchecked'],[2]);self.assertEqual(report['docx']['unchecked'],[])
        self.assertEqual(report['issues'][0]['page'],1);self.assertEqual(report['status'],'partial')

    def test_missing_source_is_explicit_and_never_calls_api(self):
        with tempfile.TemporaryDirectory() as folder:
            report=review_formatting(document(),self.config(),Path(folder),object())
        self.assertEqual(report['calls'],0);self.assertEqual(report['status'],'partial')
        self.assertTrue(report['review_required'])

    def test_main_review_propagates_visual_coverage_and_missing_docx(self):
        from docpipe.review import review_after_render,Findings
        from docpipe.renderers import render_docx,render_pdf_positional
        class Client:
            def request(self,*args,**kwargs):return Findings(issues=[])
        visual={'status':'partial','review_required':True,
                'pdf':{'status':'completed','checked':[1]},'docx':{'status':'skipped','checked':[]}}
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);doc=document()
            render_docx(doc,out/'document.docx',out)
            render_pdf_positional(doc,out/'document-positional.pdf',out)
            with patch('docpipe.visual_review.review_formatting',return_value=visual) as check:
                report=review_after_render(doc,self.config(),out,Client())
        check.assert_called_once()
        self.assertTrue(report['pdf_visual_review']);self.assertFalse(report['docx_visual_review'])
        self.assertTrue(report['review_required']);self.assertEqual(report['status'],'partial')

    def test_original_is_rotated_before_comparison(self):
        shapes=[]
        class Client:
            def request(self,*args,**kwargs):
                data=base64.b64decode(kwargs['image_url'][0].split(',',1)[1])
                with Image.open(io.BytesIO(data)) as image:shapes.append(image.size)
                return VisualFindings(readability='adequate',issues=[])
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);source=out/'source.pdf';pdf_file(source);pdf_file(out/'document-positional.pdf')
            doc=document().model_copy(update={'source':SourceInfo(file=str(source),sha256='0'*64,type='pdf')})
            doc.pages[0].rotation=90
            review_formatting(doc,self.config(),out,Client())
        self.assertGreater(shapes[0][0],shapes[0][1])

if __name__=='__main__':unittest.main()
