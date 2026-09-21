from __future__ import annotations


class GoogleBigQueryClient:
    def __init__(self, project_id: str, dataset: str, table: str, location: str = 'asia-south1'):
        from google.cloud import bigquery

        if not project_id:
            raise ValueError('BIGQUERY_PROJECT_ID is required for live BigQuery mode')
        if not dataset or not table:
            raise ValueError('BIGQUERY_DATASET and BIGQUERY_TABLE are required for live BigQuery mode')

        self.bigquery = bigquery
        self.project_id = project_id
        self.dataset = dataset
        self.table = table
        self.location = location
        self.client = bigquery.Client(project=project_id)

        # Inspect table schema dynamically to map column aliases (e.g. complaints.csv vs citizen_requests_fused)
        self.col_id = 'request_id'
        self.col_source = 'source_channel'
        self.col_date = 'created_at'
        self.col_lang = 'input_language'
        self.schema_fields = set()
        try:
            t = self.client.get_table(f"{project_id}.{dataset}.{table}")
            c_names = {f.name.lower() for f in t.schema}
            self.schema_fields = c_names
            if 'id' in c_names and 'request_id' not in c_names:
                self.col_id = 'id'
            if 'source' in c_names and 'source_channel' not in c_names:
                self.col_source = 'source'
            if 'date' in c_names and 'created_at' not in c_names:
                self.col_date = 'date'
            if 'language' in c_names and 'input_language' not in c_names:
                self.col_lang = 'language'
        except Exception:
            pass

    @property
    def table_ref(self) -> str:
        return f"`{self.project_id}.{self.dataset}.{self.table}`"

    def _run(self, query: str, params: list | None = None):
        cfg = self.bigquery.QueryJobConfig(query_parameters=params or [])
        job = self.client.query(query, location=self.location, job_config=cfg)
        return list(job.result())

    def aggregate_requests(self, limit: int = 50) -> dict:
        safe_limit = min(max(int(limit), 1), 500)
        int_param = [self.bigquery.ScalarQueryParameter('limit', 'INT64', safe_limit)]

        district_rows = self._run(
            f'''
            SELECT district, state,
                   COUNT(1) AS request_count,
                   SUM(CASE WHEN urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count,
                   SUM(CASE WHEN urgency IN ('Emergency','Urgent') THEN 1 ELSE 0 END) AS high_priority_count,
                   COUNT(DISTINCT category) AS category_diversity
            FROM {self.table_ref}
            GROUP BY district, state
            ORDER BY request_count DESC, emergency_count DESC, district ASC
            LIMIT @limit
            ''',
            int_param,
        )

        category_rows = self._run(
            f'''
            SELECT category,
                   COUNT(1) AS request_count,
                   SUM(CASE WHEN urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count
            FROM {self.table_ref}
            GROUP BY category
            ORDER BY request_count DESC, category ASC
            LIMIT @limit
            ''',
            int_param,
        )

        source_rows = self._run(
            f'''
            SELECT {self.col_source} AS source_channel, COUNT(1) AS request_count
            FROM {self.table_ref}
            GROUP BY {self.col_source}
            ORDER BY request_count DESC, {self.col_source} ASC
            LIMIT @limit
            ''',
            int_param,
        )

        daily_rows = self._run(
            f'''
            SELECT SUBSTR(CAST({self.col_date} AS STRING), 1, 10) AS day, COUNT(1) AS request_count
            FROM {self.table_ref}
            GROUP BY day
            ORDER BY day DESC
            LIMIT @limit
            ''',
            int_param,
        )

        return {
            'mode': 'bigquery_live',
            'warehouse': 'bigquery',
            'dataset': self.dataset,
            'table': self.table,
            'district_aggregates': [
                {
                    'district': row['district'],
                    'state': row['state'],
                    'request_count': int(row['request_count'] or 0),
                    'emergency_count': int(row['emergency_count'] or 0),
                    'high_priority_count': int(row['high_priority_count'] or 0),
                    'category_diversity': int(row['category_diversity'] or 0),
                }
                for row in district_rows
            ],
            'category_aggregates': [
                {
                    'category': row['category'],
                    'request_count': int(row['request_count'] or 0),
                    'emergency_count': int(row['emergency_count'] or 0),
                }
                for row in category_rows
            ],
            'source_aggregates': [
                {
                    'source_channel': row['source_channel'],
                    'request_count': int(row['request_count'] or 0),
                }
                for row in source_rows
            ],
            'daily_trend': [
                {
                    'day': row['day'],
                    'request_count': int(row['request_count'] or 0),
                }
                for row in daily_rows
            ],
        }

    def update_request_progress(self, record: dict) -> bool:
        """Keep the analytics status consistent with an officer's saved lifecycle action."""
        parameters = [self.bigquery.ScalarQueryParameter('ticket', 'STRING', record['request_id']),
                      self.bigquery.ScalarQueryParameter('status', 'STRING', record['status'])]
        assignments = ['status = @status']
        if 'sla_due_at' in self.schema_fields:
            assignments.append('sla_due_at = @sla_due_at')
            parameters.append(self.bigquery.ScalarQueryParameter('sla_due_at', 'TIMESTAMP', record.get('sla_due_at')))
        job = self.client.query(f'UPDATE {self.table_ref} SET {", ".join(assignments)} WHERE {self.col_id} = @ticket',
                                location=self.location, job_config=self.bigquery.QueryJobConfig(query_parameters=parameters), timeout=15)
        job.result(timeout=20)
        return bool(job.num_dml_affected_rows)

    def insert_request(self, record: dict) -> bool:
        """Stream a newly ingested citizen request directly into BigQuery in real-time."""
        raw_date = str(record.get('created_at') or record.get('date') or '')
        formatted_date = raw_date[:10] if (self.col_date == 'date' and len(raw_date) >= 10) else raw_date

        row = {
            self.col_id: str(record.get('request_id') or record.get('id') or ''),
            'district': str(record.get('district') or ''),
            'state': str(record.get('state') or ''),
            'urgency': str(record.get('urgency') or 'Routine'),
            'category': str(record.get('category') or 'Other'),
            self.col_source: str(record.get('source_channel') or record.get('source') or 'Web Ingress'),
            self.col_date: formatted_date,
            self.col_lang: str(record.get('input_language') or record.get('language') or 'en'),
            'original_text': str(record.get('original_text') or ''),
            'translated_text': str(record.get('translated_text') or ''),
            'sentiment': str(record.get('sentiment') or 'Neutral'),
            'status': str(record.get('status') or 'New'),
            'lat': float(record.get('lat') or 0.0),
            'lng': float(record.get('lng') or 0.0),
        }
        row_id = str(row[self.col_id])
        if 'date' in self.schema_fields:
            row['date'] = raw_date[:10] or None
        for field in ('ward', 'routed_department', 'service_type', 'sla_due_at'):
            if field in self.schema_fields:
                row[field] = record.get(field) or None
        if 'is_synthetic' in self.schema_fields:
            row['is_synthetic'] = bool(record.get('is_synthetic', False))
        table_ref_str = f"{self.project_id}.{self.dataset}.{self.table}"
        errors = self.client.insert_rows_json(table_ref_str, [row], row_ids=[row_id], retry=None, timeout=10)
        if errors:
            raise RuntimeError(f"BigQuery streaming insert errors: {errors}")
        return True
