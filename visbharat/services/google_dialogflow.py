from __future__ import annotations


class GoogleDialogflowCXClient:
    def __init__(self, project_id: str, location: str, agent_id: str, language_code: str = 'en', api_endpoint: str = ''):
        from google.cloud import dialogflowcx_v3 as dialogflowcx

        if not project_id:
            raise ValueError('DIALOGFLOW_PROJECT_ID is required for live Dialogflow CX mode')
        if not location:
            raise ValueError('DIALOGFLOW_LOCATION is required for live Dialogflow CX mode')
        if not agent_id:
            raise ValueError('DIALOGFLOW_AGENT_ID is required for live Dialogflow CX mode')

        self.dialogflowcx = dialogflowcx
        self.project_id = project_id
        self.location = location
        self.agent_id = agent_id
        self.language_code = language_code or 'en'
        self.api_endpoint = api_endpoint or f'{location}-dialogflow.googleapis.com'

        self.client = dialogflowcx.SessionsClient(
            client_options={'api_endpoint': self.api_endpoint}
        )

    def _session_path(self, session_id: str) -> str:
        safe_session = (session_id or 'local-session').strip().replace(' ', '-')
        return (
            f'projects/{self.project_id}/locations/{self.location}'
            f'/agents/{self.agent_id}/sessions/{safe_session}'
        )

    def detect_intent(self, session_id: str, text: str, language_code: str = '', parameters=None) -> dict:
        session = self._session_path(session_id)
        lang = language_code or self.language_code

        text_input = self.dialogflowcx.TextInput(text=text)
        query_input = self.dialogflowcx.QueryInput(text=text_input, language_code=lang)
        request = self.dialogflowcx.DetectIntentRequest(
            session=session,
            query_input=query_input,
            query_params=self.dialogflowcx.QueryParameters(parameters=parameters or {}),
        )

        response = self.client.detect_intent(request=request, timeout=12, retry=None)
        return self._format_response(response.query_result, text, lang)

    def _format_response(self, query_result, text: str, lang: str) -> dict:
        messages = []
        for msg in query_result.response_messages:
            if msg.text and msg.text.text:
                messages.extend(list(msg.text.text))

        next_prompt = '\n'.join(messages)

        intent_name = ''
        if query_result.intent and query_result.intent.display_name:
            intent_name = query_result.intent.display_name

        current_page = ''
        if query_result.current_page and query_result.current_page.display_name:
            current_page = query_result.current_page.display_name

        return {
            'session_state': current_page or 'dialogflow_live',
            'intent': intent_name or 'dialogflow_live_intent',
            'next_prompt': next_prompt,
            'confidence': float(query_result.intent_detection_confidence or 0.0),
            'language': lang,
            'channel': 'dialogflow_cx',
            'flow': 'dialogflow_cx_live',
            'parameters': dict(query_result.parameters or {}),
        }
