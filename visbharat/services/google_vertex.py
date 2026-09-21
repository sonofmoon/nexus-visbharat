from __future__ import annotations
import math


class GoogleVertexPredictionClient:
    def __init__(self, project_id: str, location: str, endpoint_id: str, api_endpoint: str = ''):
        from google.cloud import aiplatform_v1

        if not project_id:
            raise ValueError('VERTEX_PROJECT_ID is required for live Vertex mode')
        if not location:
            raise ValueError('VERTEX_LOCATION is required for live Vertex mode')
        if not endpoint_id:
            raise ValueError('VERTEX_ENDPOINT_ID is required for live Vertex mode')

        self.aiplatform_v1 = aiplatform_v1
        self.project_id = project_id
        self.location = location
        self.endpoint_id = endpoint_id
        self.api_endpoint = api_endpoint or f'{location}-aiplatform.googleapis.com'

        self.client = aiplatform_v1.PredictionServiceClient(
            client_options={'api_endpoint': self.api_endpoint}
        )
        self.endpoint_path = self.client.endpoint_path(
            project=self.project_id,
            location=self.location,
            endpoint=self.endpoint_id,
        )

    def _matrix_instances(self, instances: list[dict]) -> list[list[float]]:
        matrix = []
        for row in instances:
            matrix.append(
                [
                    float(row.get('predicted_stress_score_next_quarter', 0.0) or 0.0),
                    float(row.get('complaints', 0.0) or 0.0),
                    float(row.get('emergency_complaints', 0.0) or 0.0),
                    float(row.get('demand_per_100k', 0.0) or 0.0),
                    float(row.get('emergency_ratio', 0.0) or 0.0),
                ]
            )
        return matrix

    def predict_stress(self, instances: list[dict], allow_fallback: bool = True) -> list[dict]:
        if not instances:
            return []

        try:
            response = self.client.predict(
                endpoint=self.endpoint_path,
                instances=instances,
                timeout=30,
                retry=None,
            )
        except Exception:
            if not allow_fallback:
                raise
            response = self.client.predict(
                endpoint=self.endpoint_path,
                instances=self._matrix_instances(instances),
                timeout=30,
                retry=None,
            )

        parsed = []
        deployed_model_id = str(getattr(response, 'deployed_model_id', '') or '').strip()
        trace_default = f"endpoint:{self.endpoint_id}"
        if deployed_model_id:
            trace_default = f"{trace_default}|deployed_model:{deployed_model_id}"

        for pred in response.predictions:
            if hasattr(pred, 'items'):
                pred_dict = dict(pred.items())
                score = float(pred_dict.get('predicted_stress_score_next_quarter', pred_dict.get('score', 0.0)) or 0.0)
                supplied = pred_dict.get('model_confidence', pred_dict.get('confidence'))
                confidence = float(supplied) if supplied is not None else None
                risk_band = str(pred_dict.get('risk_band', '') or '').strip().lower()
                model_name = str(pred_dict.get('model_name', '') or '').strip() or trace_default
            else:
                score = float(pred or 0.0)
                confidence = None
                risk_band = ''
                model_name = trace_default

            if not math.isfinite(score) or (confidence is not None and (not math.isfinite(confidence) or not 0 <= confidence <= 1)):
                raise ValueError('Prediction contains invalid numeric values')
            parsed.append(
                {
                    'predicted_stress_score_next_quarter': round(score, 3),
                    'model_confidence': round(confidence, 3) if confidence is not None else None,
                    'confidence_basis': 'provider_estimate_not_calibrated' if confidence is not None else 'not_reported',
                    'risk_band': risk_band,
                    'model_name': model_name,
                }
            )

        return parsed

