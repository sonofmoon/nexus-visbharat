import base64
import json
import os
from google.cloud import bigquery
import functions_framework

# Initialize BigQuery Client
raw_project = str(os.environ.get("BIGQUERY_PROJECT_ID", "nexus-visbharat") or "nexus-visbharat").strip()
PROJECT_ID = raw_project.split()[0] if " " in raw_project else raw_project
raw_dataset = str(os.environ.get("BIGQUERY_DATASET", "visbharat_analytics") or "visbharat_analytics").strip()
DATASET_ID = raw_dataset.split()[0] if " " in raw_dataset else raw_dataset
raw_table = str(os.environ.get("BIGQUERY_TABLE", "citizen_requests_fused") or "citizen_requests_fused").strip()
TABLE_ID = raw_table.split()[0] if " " in raw_table else raw_table

bq_client = bigquery.Client(project=PROJECT_ID)

@functions_framework.cloud_event
def ingest_citizen_request_pubsub(cloud_event):
    """GCP Cloud Function triggered by Pub/Sub event when a new citizen complaint is ingested.
    Streams the complaint record into BigQuery analytics table in real time.
    """
    try:
        # 1. Decode Pub/Sub message payload
        pubsub_data = cloud_event.data.get("message", {}).get("data")
        if not pubsub_data:
            print("No payload data found in Pub/Sub cloud event.")
            return

        payload_json = base64.b64decode(pubsub_data).decode("utf-8")
        record = json.loads(payload_json)

        # 2. Format row for BigQuery table schema
        row = {
            "id": str(record.get("request_id") or record.get("id") or ""),
            "district": str(record.get("district") or ""),
            "state": str(record.get("state") or ""),
            "urgency": str(record.get("urgency") or "Routine"),
            "category": str(record.get("category") or "Other"),
            "source": str(record.get("source_channel") or record.get("source") or "Web Ingress"),
            "date": str(record.get("created_at") or record.get("date") or "")[:10],
            "language": str(record.get("input_language") or record.get("language") or "en"),
            "original_text": str(record.get("original_text") or ""),
            "translated_text": str(record.get("translated_text") or ""),
            "sentiment": str(record.get("sentiment") or "Neutral"),
            "status": str(record.get("status") or "New"),
            "lat": float(record.get("lat") or 0.0),
            "lng": float(record.get("lng") or 0.0),
        }

        # 3. Stream row into BigQuery table with row_ids for streaming deduplication
        table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
        errors = bq_client.insert_rows_json(table_ref, [row], row_ids=[row["id"]])

        if errors:
            print(f"Error streaming row to BigQuery {table_ref}: {errors}")
            raise RuntimeError(f"BigQuery streaming error: {errors}")

        print(f"Successfully streamed request {row['id']} to BigQuery table {table_ref}")

    except Exception as e:
        print(f"Cloud Function execution error: {e}")
        raise e
