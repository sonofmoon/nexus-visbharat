"""Browser regression checks use an isolated database and labelled provider fixtures."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server, WSGIRequestHandler
from visbharat import create_app
from visbharat.db import get_db


class QuietHandler(WSGIRequestHandler):
    def log_request(self,*args):pass


class TestAssistantBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='nvb-assistant-ui-')
        cls.app=create_app({'TESTING':True,'DATABASE_PATH':str(Path(cls.tmp.name)/'ui.db'),'DATABASE_URL':'','DISABLE_EXTERNAL_SERVICES':True,'JURY_REQUIRE_LIVE_MODELS':False})
        cls.server=make_server('127.0.0.1',0,cls.app,threaded=True,request_handler=QuietHandler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'
        cls.pw=sync_playwright().start();cls.browser=cls.pw.chromium.launch(headless=True)
        cls.output=Path('scratch/assistant-review');cls.output.mkdir(parents=True,exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop();cls.server.shutdown();cls.thread.join(timeout=5);cls.tmp.cleanup()

    def setUp(self):
        self.context=self.browser.new_context(viewport={'width':1440,'height':1000})
        self.context.route('https://**/*',lambda r:r.abort())
        self.context.route('**/api/tts/synthesize',lambda r:r.fulfill(status=503,json={'success':False}))
        self.context.add_init_script('window.speechSynthesis.speak = () => {};')
        self.page=self.context.new_page();self.errors=[];self.page.on('pageerror',lambda e:self.errors.append(str(e)))
        self.patches=[patch('visbharat.blueprints.api._run_translation',side_effect=lambda text,language:{'translated_text':text,'model':'test fixture'}),patch('visbharat.blueprints.api._run_classification',return_value={'category':'Water Supply','urgency':'Routine','sentiment':'Concerned','confidence':.9,'model':'test fixture'})]
        for p in self.patches:p.start()
        self.page.goto(self.url,wait_until='domcontentloaded')
        self.page.locator('#dfcxWidgetToggleBtn').click()
        expect(self.page.locator('#dfcxSend')).to_be_enabled()
        expect(self.page.locator('#dfcxMessageStream')).to_contain_text('Please describe')
        self.page.locator('#dfcxMute').click()

    def tearDown(self):
        self.context.close()
        for p in self.patches:p.stop()
        self.assertEqual(self.errors,[])

    def send(self,text):
        expect(self.page.locator('#dfcxSend')).to_be_enabled()
        self.page.locator('#dfcxInputText').fill(text);self.page.locator('#dfcxInputForm').evaluate('form => form.requestSubmit()')

    def review(self,lang='en'):
        if lang!='en':
            self.page.select_option('#dfcxLanguage',lang)
            expect(self.page.locator('#dfcxSend')).to_be_enabled()
        texts={'en':'The water supply stops every morning near our school.','ta':'எங்கள் தெருவில் குடிநீர் தினமும் ஒரு மணி நேரம் மட்டுமே வருகிறது.','te':'మా వీధిలో తాగునీరు రోజుకు ఒక గంట మాత్రమే వస్తోంది.'}
        self.send(texts[lang]);expect(self.page.locator('#dfcxLocationPanel')).to_be_visible()
        self.page.select_option('#dfcxDistrict','Tirupati' if lang=='te' else 'Vellore')
        self.page.locator('#dfcxWard').fill('Ward 12');self.page.locator('#dfcxUseLocation').click()
        expect(self.page.locator('#dfcxReviewPanel')).to_be_visible()

    def count(self):
        with self.app.app_context():return get_db().execute('SELECT COUNT(*) AS n FROM citizen_requests').fetchone()['n']

    def test_all_languages_complete_and_follow_same_ticket(self):
        for lang in ['en','ta','te']:
            with self.subTest(language=lang):
                if lang!='en':
                    self.page.locator('#dfcxNew').click();expect(self.page.locator('#dfcxSend')).to_be_enabled()
                self.review(lang);self.page.locator('#dfcxConsent').check();self.page.locator('#dfcxConfirm').click()
                expect(self.page.locator('#dfcxReceipt')).to_be_visible()
                rid=self.page.locator('#dfcxReceipt strong').inner_text()
                self.page.locator('#dfcxReceipt button').click();expect(self.page.locator('#dfcxMessageStream')).to_contain_text(rid)
                expect(self.page.locator('#dfcxSend')).to_be_enabled()
                target=self.page.locator('#dfcxReceipt a').get_attribute('href')
                self.assertIn(rid,target)
                self.assertIn(rid,self.context.request.get(self.url+target).text())

    def test_network_loss_retry_does_not_duplicate(self):
        self.review();self.page.locator('#dfcxConsent').check();before=self.count();attempts=[]
        def route(r):
            body=r.request.post_data_json
            if body.get('action')=='confirm':
                attempts.append(body['turn_id'])
                if len(attempts)==1:r.fetch();r.abort('failed');return
            r.continue_()
        self.page.route('**/api/dialogflow/session',route)
        self.page.locator('#dfcxConfirm').click();expect(self.page.locator('#dfcxRetry')).to_be_visible()
        self.page.locator('#dfcxRetry').click();expect(self.page.locator('#dfcxReceipt')).to_be_visible()
        self.assertEqual(attempts[0],attempts[1]);self.assertEqual(self.count(),before+1)

    def test_keyboard_layout_and_localized_review(self):
        self.review('ta')
        for width,height in [(1440,1000),(390,844),(320,640)]:
            self.page.set_viewport_size({'width':width,'height':height})
            report=self.page.evaluate('''() => {const win=document.querySelector('#dfcxAssistantWindow'), send=document.querySelector('#dfcxSend');return {width:innerWidth,scroll:document.documentElement.scrollWidth,windowBottom:win.getBoundingClientRect().bottom,sendBottom:send.getBoundingClientRect().bottom,height:innerHeight,overflow:win.scrollHeight-win.clientHeight};}''')
            self.assertLessEqual(report['scroll'],width+1,report);self.assertLessEqual(report['windowBottom'],height,report)
            self.assertLessEqual(report['sendBottom'],report['windowBottom'],report)
            self.page.screenshot(path=str(self.output/f'tamil-review-{width}.png'))
        self.page.locator('#dfcxInputText').focus();self.page.keyboard.press('Escape')
        expect(self.page.locator('#dfcxAssistantWindow')).to_be_hidden();expect(self.page.locator('#dfcxWidgetToggleBtn')).to_be_focused()

    def test_microphone_denial_keeps_text_available(self):
        self.page.evaluate("() => { navigator.mediaDevices.getUserMedia = async () => {throw new DOMException('denied','NotAllowedError')}; }")
        self.page.locator('#dfcxMicBtn').click();expect(self.page.locator('#dfcxStatus')).to_contain_text('Microphone unavailable')
        self.send('There is a water supply problem.');expect(self.page.locator('#dfcxLocationPanel')).to_be_visible()

    def test_recording_transcript_review_and_close_stop(self):
        self.page.evaluate('''() => {
          window.stoppedTracks=0;
          navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop:()=>window.stoppedTracks++}]});
          window.MediaRecorder=class {static isTypeSupported(){return true} constructor(){this.state='inactive';this.mimeType='audio/webm'} start(){this.state='recording'} stop(){this.state='inactive';this.ondataavailable({data:new Blob(['test audio'],{type:'audio/webm'})});this.onstop()}};
        }''')
        self.page.route('**/api/transcribe-voice',lambda r:r.fulfill(json={'success':True,'transcript':'Water supply is interrupted.','provider':'labelled UI fixture'}))
        self.page.locator('#dfcxMicBtn').click();expect(self.page.locator('#dfcxMicBtn')).to_have_attribute('aria-pressed','true')
        self.page.locator('#dfcxMicBtn').click();expect(self.page.locator('#dfcxInputText')).to_have_value('Water supply is interrupted.')
        expect(self.page.locator('#dfcxLocationPanel')).to_be_hidden()
        self.page.locator('#dfcxMicBtn').click();self.page.locator('#dfcxInputText').press('Escape')
        expect(self.page.locator('#dfcxAssistantWindow')).to_be_hidden()
        self.assertGreater(self.page.evaluate('window.stoppedTracks'),0)

    def test_close_stops_cloud_audio(self):
        self.page.route('**/api/tts/synthesize',lambda r:r.fulfill(json={'success':True,'tts':{'audio_base64':'YXVkaW8=','audio_mime_type':'audio/mpeg'}}))
        self.page.evaluate('''() => {window.audioPaused=0;window.Audio=class {async play(){} pause(){window.audioPaused++} };}''')
        self.page.locator('#dfcxMute').click();self.page.locator('#dfcxReplay').click()
        expect(self.page.locator('#dfcxStatus')).to_contain_text('Speaking')
        self.page.locator('#dfcxInputText').press('Escape');self.assertGreater(self.page.evaluate('window.audioPaused'),0)
