#!/bin/bash
# GCP Cloud Function Deployment Script for Nexus Visbharath Real-Time BigQuery Ingestion

PROJECT_ID="nexus-visbharat"
REGION="asia-south1"
TOPIC_NAME="citizen-ingestion-topic"
FUNCTION_NAME="nvb-ingest-to-bigquery"

echo "=== 1. Creating GCP Pub/Sub Topic '${TOPIC_NAME}' ==="
gcloud pubsub topics create ${TOPIC_NAME} --project=${PROJECT_ID} || true

echo "=== 2. Deploying GCP Cloud Function '${FUNCTION_NAME}' ==="
gcloud functions deploy ${FUNCTION_NAME} \
  --gen2 \
  --runtime=python311 \
  --region=${REGION} \
  --source=./gcp_cloud_function \
  --entry-point=ingest_citizen_request_pubsub \
  --trigger-topic=${TOPIC_NAME} \
  --set-env-vars=BIGQUERY_PROJECT_ID=${PROJECT_ID},BIGQUERY_DATASET=visbharat_analytics,BIGQUERY_TABLE=citizen_requests_fused \
  --project=${PROJECT_ID}

echo "=== Deployment Complete! ==="
