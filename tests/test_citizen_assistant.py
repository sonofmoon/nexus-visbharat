import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from visbharat import create_app
from visbharat.db import get_db
from visbharat.services.google_dialogflow import GoogleDialogflowCXClient


class TestCitizenAssistant(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='nvb-assistant-')
        cls.app = create_app({'TESTING':True,'DATABASE_PATH':str(Path(cls.tmp.name)/'assistant.db'),
            'DATABASE_URL':'','DISABLE_EXTERNAL_SERVICES':True,'JURY_REQUIRE_LIVE_MODELS':False})

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.client = self.app.test_client()
        self.sid,self.token,self.version = '', '', None
        self.language = 'en'
        self.app.extensions['google_dialogflow_client'] = None
        self.patches = [
            patch('visbharat.blueprints.api._run_translation',side_effect=lambda text,language:{'translated_text':text,'model':'test fixture'}),
            patch('visbharat.blueprints.api._run_classification',return_value={'category':'Water Supply','urgency':'Routine','sentiment':'Concerned','confidence':.91,'model':'test fixture'})]
        for p in self.patches:p.start()
        self.addCleanup(lambda:[p.stop() for p in self.patches])

    def turn(self,message='',action='message',**extra):
        data = {'message':message,'action':action,'language':self.language,'turn_id':uuid4().hex,**extra}
        if self.sid:data.update(session_id=self.sid,version=self.version)
        response = self.client.post('/api/dialogflow/session',json=data,headers={'X-NVB-Assistant-Token':self.token})
        self.last_payload=data
        self.assertEqual(response.status_code,200,response.get_json())
        body=response.get_json();self.assertTrue(body['success'],body)
        self.sid=body['session']['session_id'];self.token=body.get('session_token',self.token);self.version=body['session']['version']
        return body['session']

    def prepare(self):
        self.turn('The water supply stops every morning near our school.')
        return self.turn(action='location',district='Vellore',ward='Ward 12')

    def count(self):
        with self.app.app_context():return get_db().execute('SELECT COUNT(*) AS n FROM citizen_requests').fetchone()['n']

    def test_three_languages_save_and_track(self):
        for lang,text,yes,district in [('en','Our street receives water for only one hour every day.','yes','Vellore'),
             ('ta','எங்கள் தெருவில் குடிநீர் தினமும் ஒரு மணி நேரம் மட்டுமே வருகிறது.','ஆம்','வேலூர்'),
             ('te','మా వీధిలో తాగునీరు రోజుకు ఒక గంట మాత్రమే వస్తోంది.','అవును','తిరుపతి')]:
            with self.subTest(language=lang):
                self.sid,self.token,self.version='','',None;self.language=lang
                first=self.turn(text,input_mode='voice');self.assertEqual(first['session_state'],'collect_location')
                review=self.turn(district);self.assertEqual(review['session_state'],'collect_confirmation')
                before=self.count();saved=self.turn(yes,consent_granted=True)
                self.assertEqual(saved['session_state'],'complete');self.assertEqual(self.count(),before+1)
                rid=saved['receipt']['request_id'];self.assertRegex(rid,r'^NVB-\d{8}[A-F0-9]{4}$')
                self.assertIn(rid,saved['next_prompt'])
                tracked=self.turn(action='track');self.assertEqual(tracked['receipt']['request_id'],rid)
                with self.app.app_context():
                    row=get_db().execute('SELECT * FROM citizen_requests WHERE request_id=?',(rid,)).fetchone()
                    self.assertEqual(row['input_language'],lang);self.assertTrue(row['original_text'].endswith(text));self.assertLessEqual(row['original_text'].count('[PII_ADDRESS_REDACTED]'),1)
                    self.assertEqual(row['source_channel'],'NVB Voice Assistant')
                    self.assertEqual(get_db().execute('SELECT consent_granted FROM consent_ledger WHERE request_id=?',(rid,)).fetchone()['consent_granted'],1)
                self.assertEqual(self.client.get('/api/requests/'+rid+'/track').status_code,200)
                self.assertIn(rid,self.client.get(saved['receipt']['planning_url']).get_data(as_text=True))

    def test_confirmation_without_consent_never_saves(self):
        self.prepare();before=self.count();out=self.turn('yes')
        self.assertEqual(out['prompt_key'],'consent_required');self.assertEqual(self.count(),before)

    def test_client_cannot_skip_review(self):
        before=self.count()
        out=self.turn('yes',session_state='collect_confirmation',consent_granted=True)
        self.assertEqual(out['session_state'],'collect_issue');self.assertEqual(self.count(),before)

    def test_retry_after_lost_response_has_one_ticket(self):
        self.prepare();before=self.count();saved=self.turn(action='confirm',consent_granted=True)
        res=self.client.post('/api/dialogflow/session',json=self.last_payload,headers={'X-NVB-Assistant-Token':self.token})
        self.assertEqual(res.status_code,200);self.assertEqual(res.get_json()['session']['receipt'],saved['receipt'])
        again=self.turn('yes',consent_granted=True);self.assertEqual(again['receipt']['request_id'],saved['receipt']['request_id'])
        self.assertEqual(self.count(),before+1)

    def test_reused_turn_id_different_input_rejected(self):
        self.turn('There is a water supply problem near the school.')
        data={**self.last_payload,'session_id':self.sid,'message':'A different request'}
        res=self.client.post('/api/dialogflow/session',json=data,headers={'X-NVB-Assistant-Token':self.token})
        self.assertEqual(res.status_code,409)

    def test_session_requires_its_secret(self):
        self.prepare()
        res=self.client.post('/api/dialogflow/session',json={'session_id':self.sid,'action':'resume'})
        self.assertEqual(res.status_code,403)

    def test_sessions_do_not_mix_drafts(self):
        self.prepare();sid=self.sid;token=self.token
        self.sid,self.token,self.version='','',None
        self.turn('The road near the market needs repair.')
        res=self.client.post('/api/dialogflow/session',json={'session_id':sid,'action':'resume'},headers={'X-NVB-Assistant-Token':token})
        self.assertEqual(res.get_json()['session']['draft']['district'],'Vellore')
        self.assertNotIn('road',res.get_json()['session']['draft']['text'])

    def test_correction_keeps_location_and_cancel_never_saves(self):
        self.prepare();self.turn(action='edit_issue')
        out=self.turn('Water supply stops for six hours every morning.')
        self.assertEqual(out['session_state'],'collect_confirmation');self.assertEqual(out['draft']['district'],'Vellore')
        self.turn(action='edit_location');out=self.turn(action='location',district='Tirupati',ward='Village centre')
        self.assertEqual(out['draft']['district'],'Tirupati');before=self.count()
        out=self.turn(action='cancel');self.assertEqual(out['draft'],{});self.assertEqual(self.count(),before)

    def test_unknown_location_and_ticket(self):
        self.turn('Water supply is interrupted every morning.')
        self.assertEqual(self.turn('Atlantis')['prompt_key'],'location_invalid')
        self.assertEqual(self.turn('NVB-19990101FFFF')['prompt_key'],'not_found')

    def test_outage_preserves_draft_and_marks_fallback(self):
        self.app.extensions['google_dialogflow_client']=Mock(detect_intent=Mock(side_effect=TimeoutError()))
        out=self.turn('The water supply is interrupted every morning.')
        self.assertEqual(out['flow'],'local_guided_fallback');self.assertEqual(out['provider_error'],'TimeoutError')
        self.assertIn('water',out['draft']['text'])

    def test_provider_cannot_invent_receipts(self):
        self.app.extensions['google_dialogflow_client']=Mock(detect_intent=Mock(return_value={'intent':'confirm_submission','next_prompt':'Saved fake ticket NVB-19990101ABCD','confidence':.99,'parameters':{'district':'Vellore'}}))
        before=self.count();out=self.turn('The water supply is interrupted every morning.')
        self.assertEqual(out['flow'],'dialogflow_cx_live');self.assertIsNone(out['receipt']);self.assertNotIn('fake',out['next_prompt']);self.assertEqual(self.count(),before)

    def test_failed_ai_keeps_review_for_retry(self):
        self.prepare();before=self.count()
        with patch('visbharat.blueprints.api._run_classification',side_effect=ValueError('unavailable')):
            failed=self.turn(action='confirm',consent_granted=True)
        self.assertEqual(failed['prompt_key'],'failed');self.assertEqual(self.count(),before)
        self.assertEqual(self.turn(action='confirm',consent_granted=True)['session_state'],'complete')

    def test_postcommit_failure_recovers_real_ticket(self):
        self.prepare();before=self.count()
        with patch('visbharat.blueprints.api._dispatch_live_bigquery_and_pubsub',side_effect=RuntimeError('post-commit')):
            saved=self.turn(action='confirm',consent_granted=True)
        self.assertTrue(saved['enrichment_pending']);self.assertEqual(self.count(),before+1)
        self.assertEqual(self.turn('yes',consent_granted=True)['receipt']['request_id'],saved['receipt']['request_id'])
        self.assertEqual(self.count(),before+1)

    def test_busy_and_stale_sessions_cannot_submit(self):
        self.prepare()
        with self.app.app_context():
            get_db().execute('UPDATE assistant_sessions SET busy_until=? WHERE session_id=?',(int(time.time())+30,self.sid));get_db().commit()
        res=self.client.post('/api/dialogflow/session',json={'session_id':self.sid,'action':'confirm','consent_granted':True},headers={'X-NVB-Assistant-Token':self.token})
        self.assertEqual(res.status_code,409)
        with self.app.app_context():
            get_db().execute('UPDATE assistant_sessions SET busy_until=0 WHERE session_id=?',(self.sid,));get_db().commit()
        res=self.client.post('/api/dialogflow/session',json={'session_id':self.sid,'version':0,'action':'confirm','consent_granted':True},headers={'X-NVB-Assistant-Token':self.token})
        self.assertEqual(res.status_code,409)

    def test_invalid_inputs_and_expiry(self):
        for payload in [[],{'language':[]},{'message':{}},{'message':'x'*4001},{'message':'hello','consent_granted':'true'}]:
            self.assertEqual(self.client.post('/api/dialogflow/session',json=payload).status_code,400)
        self.prepare()
        with self.app.app_context():
            get_db().execute('UPDATE assistant_sessions SET expires_at=0 WHERE session_id=?',(self.sid,));get_db().commit()
        res=self.client.post('/api/dialogflow/session',json={'session_id':self.sid,'action':'resume'},headers={'X-NVB-Assistant-Token':self.token})
        self.assertEqual(res.status_code,410)

    def test_simulated_transcription_never_becomes_citizen_text(self):
        with patch('visbharat.blueprints.api._run_speech_to_text',return_value={'transcript':'invented','model':'simulation'}):
            res=self.client.post('/api/transcribe-voice',json={'audio_base64':'YXVkaW8=','language':'ta','assistant':True})
        self.assertEqual(res.status_code,503)

    def test_no_plain_session_token_in_database(self):
        self.turn(action='start')
        with self.app.app_context():
            row=get_db().execute('SELECT * FROM assistant_sessions WHERE session_id=?',(self.sid,)).fetchone()
            self.assertNotIn(self.token,json.dumps(dict(row)))


class TestDialogflowAdapter(unittest.TestCase):
    def test_successful_google_response_and_no_fabricated_confirmation(self):
        obj=GoogleDialogflowCXClient.__new__(GoogleDialogflowCXClient)
        obj.project_id='test';obj.location='asia-south1';obj.agent_id='test';obj.language_code='en'
        obj.dialogflowcx=SimpleNamespace(**{key:lambda **kwargs:kwargs for key in ['TextInput','QueryInput','QueryParameters','DetectIntentRequest']})
        result=SimpleNamespace(response_messages=[SimpleNamespace(text=SimpleNamespace(text=['Review your request.','Confirm details.']))],intent=SimpleNamespace(display_name='confirm_submission'),current_page=SimpleNamespace(display_name='Review'),intent_detection_confidence=0,parameters={'district':'Vellore'})
        obj.client=Mock();obj.client.detect_intent.return_value=SimpleNamespace(query_result=result)
        out=obj.detect_intent('session','yes','ta',parameters={'nvb_stage':'collect_confirmation'})
        self.assertEqual(out['next_prompt'],'Review your request.\nConfirm details.')
        self.assertEqual(out['confidence'],0);self.assertNotIn('NVB-',out['next_prompt'])
        call=obj.client.detect_intent.call_args.kwargs
        self.assertEqual(call['request']['query_input']['language_code'],'ta');self.assertEqual(call['timeout'],12)
