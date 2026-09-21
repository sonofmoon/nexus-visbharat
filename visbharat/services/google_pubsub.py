from __future__ import annotations

import json
from typing import Dict, Any


class GooglePubSubPublisherClient:
    def __init__(self, project_id: str, topic_id: str = 'citizen-ingestion-topic'):
        from google.cloud import pubsub_v1

        if not project_id:
            raise ValueError('GCP PROJECT_ID is required for live Pub/Sub Publisher mode')

        self.project_id = project_id
        self.topic_id = topic_id or 'citizen-ingestion-topic'
        self.publisher = pubsub_v1.PublisherClient()
        self.topic_path = self.publisher.topic_path(self.project_id, self.topic_id)

    def publish_request(self, record: Dict[str, Any]) -> str:
        """Publish a newly ingested citizen complaint record to GCP Pub/Sub topic.
        This triggers the GCP Cloud Function (nvb-ingest-to-bigquery) to stream the record into BigQuery.
        """
        payload = json.dumps(record, default=str).encode('utf-8')
        future = self.publisher.publish(self.topic_path, payload)
        message_id = future.result(timeout=10)
        return str(message_id)
