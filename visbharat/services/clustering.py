import datetime
import json
import re
import math
import hashlib

from flask import current_app

from ..db import get_db


DEFAULT_SIMILARITY_THRESHOLD = 0.6
DEFAULT_MIN_SHARED_TOKENS = 2
DEFAULT_MAX_TOKENS_FOR_KEY = 6
DEFAULT_MAX_TOKEN_EXAMPLES = 12
DEFAULT_REVIEW_SIMILARITY_THRESHOLD = 0.72

_TEXT_NORMALIZATION_MAP = {
    'pani': 'water',
    'thanni': 'water',
    'jal': 'water',
    'neer': 'water',
    'sadak': 'road',
    'rasta': 'road',
    'raasta': 'road',
    'bijli': 'electricity',
    'current': 'electricity',
    'kooda': 'garbage',
    'kachra': 'garbage',
    'safai': 'cleaning',
    'nala': 'drainage',
    'naala': 'drainage',
    'gutter': 'drainage',
    'aspataal': 'hospital',
    'chikitsalay': 'hospital',
    'school': 'school',
    'vidyalaya': 'school',
}


def _now_iso_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _normalized_whitespace(text: str):
    return ' '.join(str(text or '').strip().split())


def _normalize_text(text: str):
    cleaned = re.sub(r'[^a-z0-9\s]+', ' ', _normalized_whitespace(text).lower())
    tokens = []
    for token in cleaned.split():
        mapped = _TEXT_NORMALIZATION_MAP.get(token, token)
        if len(mapped) >= 3:
            tokens.append(mapped)
    return ' '.join(tokens)


def _tokenize(text: str):
    normalized_text = _normalize_text(text)
    return set(normalized_text.split())


def _get_threshold_config():
    similarity_threshold = DEFAULT_SIMILARITY_THRESHOLD
    min_shared_tokens = DEFAULT_MIN_SHARED_TOKENS
    max_tokens_for_key = DEFAULT_MAX_TOKENS_FOR_KEY
    max_token_examples = DEFAULT_MAX_TOKEN_EXAMPLES

    try:
        similarity_threshold = float(current_app.config.get('CLUSTER_SIMILARITY_THRESHOLD', similarity_threshold))
    except Exception:
        pass
    try:
        min_shared_tokens = int(current_app.config.get('CLUSTER_MIN_SHARED_TOKENS', min_shared_tokens))
    except Exception:
        pass
    try:
        max_tokens_for_key = int(current_app.config.get('CLUSTER_KEY_MAX_TOKENS', max_tokens_for_key))
    except Exception:
        pass
    try:
        max_token_examples = int(current_app.config.get('CLUSTER_TOKEN_EXAMPLES_LIMIT', max_token_examples))
    except Exception:
        pass

    similarity_threshold = min(max(similarity_threshold, 0.0), 1.0)
    min_shared_tokens = max(min_shared_tokens, 1)
    max_tokens_for_key = max(max_tokens_for_key, 1)
    max_token_examples = max(max_token_examples, 1)

    return {
        'similarity_threshold': similarity_threshold,
        'min_shared_tokens': min_shared_tokens,
        'max_tokens_for_key': max_tokens_for_key,
        'max_token_examples': max_token_examples,
    }


def _review_similarity_threshold():
    threshold = DEFAULT_REVIEW_SIMILARITY_THRESHOLD
    try:
        threshold = float(current_app.config.get('CLUSTER_REVIEW_SIMILARITY_THRESHOLD', threshold))
    except Exception:
        pass
    return min(max(threshold, 0.0), 1.0)


def _cluster_key(category: str, state: str, token_set: set[str], max_tokens_for_key: int):
    head = '-'.join(sorted(list(token_set))[:max_tokens_for_key])
    return f"{category}|{state}|{head}"


def _jaccard_similarity(left_tokens: set[str], right_tokens: set[str]):
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    intersection_count = len(left_tokens.intersection(right_tokens))
    union_count = len(left_tokens.union(right_tokens))
    if union_count == 0:
        return 0.0
    return intersection_count / float(union_count)



def _cosine_similarity(left: list[float], right: list[float]):
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    ln = math.sqrt(sum(float(a) * float(a) for a in left))
    rn = math.sqrt(sum(float(b) * float(b) for b in right))
    if ln <= 0 or rn <= 0:
        return 0.0
    return float(dot / (ln * rn))


def _fallback_text_embedding(text: str, dim: int = 96):
    normalized = _normalize_text(text)
    tokens = normalized.split() or ['empty']
    vec = [0.0] * dim
    for token in tokens:
        digest = hashlib.sha256(token.encode('utf-8')).digest()
        for i in range(0, len(digest), 4):
            bucket = int.from_bytes(digest[i:i + 4], 'big') % dim
            vec[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _embed_text(text: str):
    google_client = current_app.extensions.get('google_ai_client')
    if google_client and hasattr(google_client, 'embed_text'):
        try:
            embedded = google_client.embed_text(text)
            if isinstance(embedded, list) and embedded:
                return [float(x) for x in embedded]
        except Exception:
            pass
    return _fallback_text_embedding(text)
def _extract_cluster_tokens(metadata_json: str, canonical_text: str):
    metadata = {}
    try:
        metadata = json.loads(metadata_json or '{}')
    except Exception:
        metadata = {}

    token_examples = metadata.get('token_examples') or []
    if isinstance(token_examples, list):
        tokens = {str(item).strip().lower() for item in token_examples if str(item).strip()}
        if tokens:
            return tokens, metadata

    return _tokenize(canonical_text), metadata


def _get_cluster_row(cluster_id: str):
    db = get_db()
    row = db.execute(
        '''
        SELECT cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
        FROM demand_clusters WHERE cluster_id = ? LIMIT 1
        ''',
        (str(cluster_id or ''),),
    ).fetchone()
    return dict(row) if row else None


def _refresh_member_count(cluster_id: str):
    db = get_db()
    count_row = db.execute('SELECT COUNT(*) AS c FROM cluster_members WHERE cluster_id = ?', (cluster_id,)).fetchone()
    count = int(count_row['c'] or 0) if count_row else 0
    db.execute('UPDATE demand_clusters SET member_count = ?, updated_at = ? WHERE cluster_id = ?', (count, _now_iso_utc(), cluster_id))
    return count


def ensure_demand_cluster_tables():
    db = get_db()
    if db.backend == 'postgres':
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS demand_clusters (
                id SERIAL PRIMARY KEY,
                cluster_id TEXT UNIQUE NOT NULL,
                state TEXT,
                category TEXT,
                canonical_text TEXT,
                member_count INTEGER NOT NULL DEFAULT 0,
                sample_request_id TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS cluster_members (
                id SERIAL PRIMARY KEY,
                request_id TEXT UNIQUE NOT NULL,
                cluster_id TEXT NOT NULL,
                similarity_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS cluster_member_overrides (
                id SERIAL PRIMARY KEY,
                request_id TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
    else:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS demand_clusters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT UNIQUE NOT NULL,
                state TEXT,
                category TEXT,
                canonical_text TEXT,
                member_count INTEGER NOT NULL DEFAULT 0,
                sample_request_id TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS cluster_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE NOT NULL,
                cluster_id TEXT NOT NULL,
                similarity_score REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL
            )
            '''
        )
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS cluster_member_overrides (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT,
                actor TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            '''
        )
    if db.backend == 'postgres':
        db.execute("ALTER TABLE cluster_member_overrides ADD COLUMN IF NOT EXISTS status TEXT")
        db.execute("ALTER TABLE cluster_member_overrides ADD COLUMN IF NOT EXISTS updated_at TEXT")
        db.execute("ALTER TABLE cluster_member_overrides ADD COLUMN IF NOT EXISTS decision TEXT")
        db.execute("UPDATE cluster_member_overrides SET status = COALESCE(status, decision, 'pending')")
        db.execute("UPDATE cluster_member_overrides SET updated_at = COALESCE(updated_at, created_at)")
    else:
        override_cols = {
            str(row['name'])
            for row in db.execute('PRAGMA table_info(cluster_member_overrides)').fetchall()
        }
        if 'status' not in override_cols:
            db.execute('ALTER TABLE cluster_member_overrides ADD COLUMN status TEXT')
        if 'updated_at' not in override_cols:
            db.execute('ALTER TABLE cluster_member_overrides ADD COLUMN updated_at TEXT')
        if 'decision' not in override_cols:
            db.execute('ALTER TABLE cluster_member_overrides ADD COLUMN decision TEXT')
        db.execute("UPDATE cluster_member_overrides SET status = COALESCE(status, decision, 'pending')")
        db.execute("UPDATE cluster_member_overrides SET updated_at = COALESCE(updated_at, created_at)")

    db.commit()


def _best_cluster_match(clusters, request_tokens: set[str], request_embedding: list[float], threshold_config: dict):
    min_shared_tokens = threshold_config['min_shared_tokens']
    similarity_threshold = threshold_config['similarity_threshold']

    ranked = []
    for cluster in clusters:
        cluster_tokens, metadata = _extract_cluster_tokens(cluster.get('metadata_json'), cluster.get('canonical_text'))
        overlap = len(request_tokens.intersection(cluster_tokens))
        lexical_similarity = _jaccard_similarity(request_tokens, cluster_tokens)

        centroid = metadata.get('embedding_centroid') if isinstance(metadata, dict) else None
        if not isinstance(centroid, list) or not centroid:
            centroid = _embed_text(cluster.get('canonical_text') or '')

        embedding_similarity = _cosine_similarity(request_embedding, [float(x) for x in centroid])

        lexical_pass = overlap >= min_shared_tokens and float(lexical_similarity) >= float(similarity_threshold)
        embedding_gate = max(float(similarity_threshold), 0.7)
        embedding_pass = float(embedding_similarity) >= embedding_gate
        if not lexical_pass and not embedding_pass:
            continue

        # Preserve deterministic lexical behavior when lexical matching is already strong.
        # Use embedding as the primary score only for semantic fallback matches.
        primary_similarity = float(lexical_similarity) if lexical_pass else float(embedding_similarity)
        ranked.append((
            primary_similarity,
            overlap,
            int(cluster.get('member_count') or 0),
            str(cluster.get('cluster_id') or ''),
            metadata,
            cluster_tokens,
            [float(x) for x in centroid],
            cluster,
        ))

    if not ranked:
        return None, 0.0, None, None, None

    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    best = ranked[0]
    return best[7], float(best[0]), best[4], best[5], best[6]


def assign_request_to_cluster(request_id: str, state: str, category: str, text: str):
    ensure_demand_cluster_tables()
    db = get_db()

    normalized_state = str(state or 'Unknown')
    normalized_category = str(category or 'Other')
    request_tokens = _tokenize(text)
    request_embedding = _embed_text(text)
    threshold_config = _get_threshold_config()
    cluster_key = _cluster_key(normalized_category, normalized_state, request_tokens, threshold_config['max_tokens_for_key'])

    rows = db.execute(
        '''
        SELECT cluster_id, member_count, metadata_json, canonical_text
        FROM demand_clusters
        WHERE state = ? AND category = ?
        ORDER BY id DESC
        ''',
        (normalized_state, normalized_category),
    ).fetchall()
    clusters = [dict(row) for row in rows]

    matched_cluster, similarity_score, metadata, matched_tokens, matched_embedding = _best_cluster_match(clusters, request_tokens, request_embedding, threshold_config)
    now = _now_iso_utc()

    if matched_cluster is None:
        seed_input = f"{normalized_state}|{normalized_category}|{cluster_key}"
        cluster_seed = int(hashlib.sha256(seed_input.encode('utf-8')).hexdigest()[:8], 16) % 1000000
        cluster_id = f"CL-{cluster_seed:06d}"
        attempt = 0
        while db.execute('SELECT cluster_id FROM demand_clusters WHERE cluster_id = ? LIMIT 1', (cluster_id,)).fetchone():
            attempt += 1
            alt_input = f"{seed_input}|{request_id}|{attempt}"
            cluster_seed = int(hashlib.sha256(alt_input.encode('utf-8')).hexdigest()[:8], 16) % 1000000
            cluster_id = f"CL-{cluster_seed:06d}"

        metadata = {
            'cluster_key': cluster_key,
            'token_examples': sorted(list(request_tokens))[:threshold_config['max_token_examples']],
        }
        for insert_attempt in range(20):
            try:
                db.execute(
                    '''
                    INSERT INTO demand_clusters (
                        cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        cluster_id,
                        normalized_state,
                        normalized_category,
                        _normalized_whitespace(text)[:500],
                        1,
                        str(request_id or ''),
                        json.dumps(metadata),
                        now,
                        now,
                    ),
                )
                break
            except Exception:
                attempt += 1
                alt_input = f"{seed_input}|{request_id}|{attempt}|{insert_attempt}"
                cluster_seed = (int(hashlib.sha256(alt_input.encode('utf-8')).hexdigest()[:8], 16) + insert_attempt * 10007) % 1000000
                cluster_id = f"CL-{cluster_seed:06d}"
        similarity_score = 1.0
    else:
        cluster_id = str(matched_cluster['cluster_id'])
        merged_tokens = set(matched_tokens or set()).union(request_tokens)
        if metadata is None:
            metadata = {}
        metadata['cluster_key'] = _cluster_key(normalized_category, normalized_state, merged_tokens, threshold_config['max_tokens_for_key'])
        metadata['token_examples'] = sorted(list(merged_tokens))[:threshold_config['max_token_examples']]

        db.execute(
            'UPDATE demand_clusters SET member_count = ?, metadata_json = ?, updated_at = ? WHERE cluster_id = ?',
            (int(matched_cluster.get('member_count') or 0) + 1, json.dumps(metadata), now, cluster_id),
        )

    existing_member = db.execute('SELECT id FROM cluster_members WHERE request_id = ? LIMIT 1', (str(request_id or ''),)).fetchone()
    if existing_member is None:
        db.execute(
            'INSERT INTO cluster_members (request_id, cluster_id, similarity_score, created_at) VALUES (?, ?, ?, ?)',
            (str(request_id or ''), cluster_id, float(similarity_score), now),
        )

    db.commit()
    review_threshold = _review_similarity_threshold()
    return {
        'cluster_id': cluster_id,
        'cluster_key': cluster_key,
        'similarity_score': float(similarity_score),
        'is_low_confidence': bool(float(similarity_score) < float(review_threshold)),
    }


def merge_demand_clusters(source_cluster_id: str, target_cluster_id: str):
    ensure_demand_cluster_tables()
    if not source_cluster_id or not target_cluster_id:
        raise ValueError('source_cluster_id and target_cluster_id are required')
    if str(source_cluster_id) == str(target_cluster_id):
        raise ValueError('source_cluster_id and target_cluster_id must be different')

    db = get_db()
    source = _get_cluster_row(source_cluster_id)
    target = _get_cluster_row(target_cluster_id)
    if source is None:
        raise ValueError('source cluster not found')
    if target is None:
        raise ValueError('target cluster not found')

    moved_rows = db.execute('SELECT request_id FROM cluster_members WHERE cluster_id = ?', (str(source_cluster_id),)).fetchall()
    moved_count = len(moved_rows)
    db.execute('UPDATE cluster_members SET cluster_id = ? WHERE cluster_id = ?', (str(target_cluster_id), str(source_cluster_id)))

    source_tokens, _ = _extract_cluster_tokens(source.get('metadata_json'), source.get('canonical_text'))
    target_tokens, target_meta = _extract_cluster_tokens(target.get('metadata_json'), target.get('canonical_text'))
    merged_tokens = source_tokens.union(target_tokens)
    if target_meta is None:
        target_meta = {}
    threshold_config = _get_threshold_config()
    target_meta['cluster_key'] = _cluster_key(str(target.get('category') or 'Other'), str(target.get('state') or 'Unknown'), merged_tokens, threshold_config['max_tokens_for_key'])
    target_meta['token_examples'] = sorted(list(merged_tokens))[:threshold_config['max_token_examples']]

    db.execute('UPDATE demand_clusters SET metadata_json = ?, updated_at = ? WHERE cluster_id = ?', (json.dumps(target_meta), _now_iso_utc(), str(target_cluster_id)))
    _refresh_member_count(str(target_cluster_id))

    db.execute('DELETE FROM demand_clusters WHERE cluster_id = ?', (str(source_cluster_id),))
    db.commit()

    return {'source_cluster_id': str(source_cluster_id), 'target_cluster_id': str(target_cluster_id), 'moved_members': moved_count}


def split_demand_cluster(source_cluster_id: str, request_ids: list[str], new_cluster_id: str | None = None):
    ensure_demand_cluster_tables()
    if not source_cluster_id:
        raise ValueError('source_cluster_id is required')

    normalized_request_ids = [str(item).strip() for item in (request_ids or []) if str(item).strip()]
    normalized_request_ids = list(dict.fromkeys(normalized_request_ids))
    if not normalized_request_ids:
        raise ValueError('request_ids is required')

    db = get_db()
    source = _get_cluster_row(source_cluster_id)
    if source is None:
        raise ValueError('source cluster not found')

    placeholders = ','.join(['?'] * len(normalized_request_ids))
    moved_rows = db.execute(
        f'SELECT request_id FROM cluster_members WHERE cluster_id = ? AND request_id IN ({placeholders})',
        tuple([str(source_cluster_id)] + normalized_request_ids),
    ).fetchall()
    moved_request_ids = [str(row['request_id']) for row in moved_rows]
    if not moved_request_ids:
        raise ValueError('no matching members found in source cluster')

    if new_cluster_id:
        new_cluster_id = str(new_cluster_id).strip()
    if not new_cluster_id:
        seed = abs(hash(f"{source_cluster_id}|{'|'.join(moved_request_ids)}")) % 1000000
        new_cluster_id = f'CL-{seed:06d}'

    existing = db.execute('SELECT cluster_id FROM demand_clusters WHERE cluster_id = ? LIMIT 1', (new_cluster_id,)).fetchone()
    if existing:
        new_cluster_id = f"CL-{abs(hash(f'{new_cluster_id}|{_now_iso_utc()}')) % 1000000:06d}"

    metadata = {}
    try:
        metadata = json.loads(source.get('metadata_json') or '{}')
    except Exception:
        metadata = {}

    now = _now_iso_utc()
    db.execute(
        '''
        INSERT INTO demand_clusters (
            cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            new_cluster_id,
            str(source.get('state') or ''),
            str(source.get('category') or ''),
            str(source.get('canonical_text') or '')[:500],
            0,
            moved_request_ids[0],
            json.dumps(metadata),
            now,
            now,
        ),
    )

    move_placeholders = ','.join(['?'] * len(moved_request_ids))
    db.execute(
        f'UPDATE cluster_members SET cluster_id = ? WHERE cluster_id = ? AND request_id IN ({move_placeholders})',
        tuple([new_cluster_id, str(source_cluster_id)] + moved_request_ids),
    )

    _refresh_member_count(str(source_cluster_id))
    _refresh_member_count(new_cluster_id)
    db.commit()

    return {'source_cluster_id': str(source_cluster_id), 'new_cluster_id': new_cluster_id, 'moved_members': len(moved_request_ids)}


def recompute_demand_clusters(limit=5000):
    ensure_demand_cluster_tables()
    db = get_db()
    rows = db.execute(
        '''
        SELECT request_id, state, category, translated_text, original_text
        FROM citizen_requests
        ORDER BY id ASC
        LIMIT ?
        ''',
        (min(max(int(limit), 1), 20000),),
    ).fetchall()

    db.execute('DELETE FROM cluster_members')
    db.execute('DELETE FROM demand_clusters')
    db.execute('DELETE FROM cluster_member_overrides')
    db.commit()

    member_count = 0
    for row in rows:
        row_data = dict(row)
        text = str(row_data.get('translated_text') or row_data.get('original_text') or '').strip()
        if not text:
            continue
        assign_request_to_cluster(
            request_id=str(row_data.get('request_id') or ''),
            state=str(row_data.get('state') or 'Unknown'),
            category=str(row_data.get('category') or 'Other'),
            text=text,
        )
        member_count += 1

    cluster_count_after_row = db.execute('SELECT COUNT(*) AS c FROM demand_clusters').fetchone()
    cluster_count = int(cluster_count_after_row['c'] or 0) if cluster_count_after_row else 0
    return {'clusters': cluster_count, 'members': member_count}


def _get_cluster_similarity_stats(cluster_ids: list[str]):
    if not cluster_ids:
        return {}
    db = get_db()
    placeholders = ','.join(['?'] * len(cluster_ids))
    rows = db.execute(
        f'''
        SELECT cluster_id,
               COUNT(*) AS members,
               MIN(similarity_score) AS min_similarity,
               AVG(similarity_score) AS avg_similarity,
               MAX(similarity_score) AS max_similarity
        FROM cluster_members
        WHERE cluster_id IN ({placeholders})
        GROUP BY cluster_id
        ''',
        tuple(cluster_ids),
    ).fetchall()
    out = {}
    for row in rows:
        obj = dict(row)
        out[str(obj['cluster_id'])] = {
            'members': int(obj.get('members') or 0),
            'min_similarity': float(obj.get('min_similarity') or 0.0),
            'avg_similarity': float(obj.get('avg_similarity') or 0.0),
            'max_similarity': float(obj.get('max_similarity') or 0.0),
        }
    return out


def list_demand_clusters(limit=100, state=None, category=None):
    ensure_demand_cluster_tables()
    db = get_db()
    clauses = []
    params = []
    if state:
        clauses.append('state = ?')
        params.append(state)
    if category:
        clauses.append('category = ?')
        params.append(category)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''

    rows = db.execute(
        f'''
        SELECT cluster_id, state, category, canonical_text, member_count, sample_request_id, metadata_json, updated_at
        FROM demand_clusters
        {where_sql}
        ORDER BY member_count DESC, updated_at DESC
        LIMIT ?
        ''',
        tuple(params + [min(max(int(limit), 1), 1000)]),
    ).fetchall()

    cluster_ids = [str(row['cluster_id']) for row in rows]
    similarity_stats = _get_cluster_similarity_stats(cluster_ids)

    out = []
    for row in rows:
        obj = dict(row)
        cluster_id = str(obj.get('cluster_id') or '')
        try:
            obj['metadata'] = json.loads(obj.get('metadata_json') or '{}')
        except Exception:
            obj['metadata'] = {}
        obj['quality'] = similarity_stats.get(cluster_id, {'members': 0, 'min_similarity': 0.0, 'avg_similarity': 0.0, 'max_similarity': 0.0})
        obj.pop('metadata_json', None)
        out.append(obj)
    return out


def _latest_overrides_for_members(request_ids: list[str], cluster_id: str | None = None):
    if not request_ids:
        return {}
    db = get_db()
    placeholders = ','.join(['?'] * len(request_ids))
    params = list(request_ids)
    cluster_clause = ''
    if cluster_id:
        cluster_clause = 'AND cluster_id = ?'
        params.append(str(cluster_id))

    rows = db.execute(
        f'''
        SELECT request_id, cluster_id, status, decision, reason, actor, created_at, updated_at
        FROM cluster_member_overrides
        WHERE request_id IN ({placeholders})
        {cluster_clause}
        ORDER BY id DESC
        ''',
        tuple(params),
    ).fetchall()
    out = {}
    for row in rows:
        req_id = str(row['request_id'])
        key = f"{req_id}|{str(row['cluster_id'])}"
        if key in out:
            continue
        status = str(row['status'] or row['decision'] or 'pending')
        out[key] = {
            'cluster_id': str(row['cluster_id']),
            'status': status,
            'decision': status,
            'reason': str(row['reason'] or ''),
            'actor': str(row['actor']),
            'created_at': row['created_at'],
            'updated_at': row['updated_at'] or row['created_at'],
        }
    return out


def _similarity_explanation(request_text: str, canonical_text: str):
    request_tokens = _tokenize(request_text)
    cluster_tokens = _tokenize(canonical_text)
    overlap = sorted(list(request_tokens.intersection(cluster_tokens)))
    return {
        'overlap_count': len(overlap),
        'top_overlap_tokens': overlap[:5],
        'rationale': f"Overlap {len(overlap)} token(s) between request and cluster canonical text.",
    }


def list_low_confidence_cluster_members(limit=100, state=None, category=None):
    ensure_demand_cluster_tables()
    db = get_db()
    review_threshold = _review_similarity_threshold()
    clauses = ['m.similarity_score < ?']
    params = [review_threshold]

    if state:
        clauses.append('c.state = ?')
        params.append(state)
    if category:
        clauses.append('c.category = ?')
        params.append(category)

    where_sql = f"WHERE {' AND '.join(clauses)}"
    rows = db.execute(
        f'''
        SELECT m.request_id, m.cluster_id, m.similarity_score, m.created_at,
               c.state, c.category, c.canonical_text,
               r.translated_text, r.original_text
        FROM cluster_members m
        JOIN demand_clusters c ON c.cluster_id = m.cluster_id
        LEFT JOIN citizen_requests r ON r.request_id = m.request_id
        {where_sql}
        ORDER BY m.similarity_score ASC, m.created_at DESC
        LIMIT ?
        ''',
        tuple(params + [min(max(int(limit), 1), 1000)]),
    ).fetchall()

    request_ids = [str(row['request_id']) for row in rows]
    overrides = _latest_overrides_for_members(request_ids)

    items = []
    for row in rows:
        req_id = str(row['request_id'])
        cluster_id = str(row['cluster_id'])
        override = overrides.get(f"{req_id}|{cluster_id}")
        status = (override or {}).get('status', '')
        is_overridden_approved = status == 'approved'
        request_text = str(row['translated_text'] or row['original_text'] or '')
        explanation = _similarity_explanation(request_text, str(row['canonical_text'] or ''))

        items.append(
            {
                'request_id': req_id,
                'cluster_id': cluster_id,
                'similarity_score': float(row['similarity_score'] or 0.0),
                'is_low_confidence': not is_overridden_approved,
                'state': row['state'],
                'category': row['category'],
                'canonical_text': row['canonical_text'],
                'created_at': row['created_at'],
                'explanation': explanation,
                'override': override,
            }
        )

    return {
        'review_threshold': float(review_threshold),
        'items': items,
    }


def _allowed_override_transitions(current_status: str):
    matrix = {
        'none': {'pending', 'approved', 'rejected'},
        'pending': {'approved', 'rejected', 'superseded'},
        'approved': {'superseded'},
        'rejected': {'superseded'},
        'superseded': {'pending', 'approved', 'rejected'},
    }
    return matrix.get(current_status, set())


def set_cluster_member_override(request_id: str, cluster_id: str, status: str, reason: str, actor: str):
    ensure_demand_cluster_tables()
    status = str(status or '').strip().lower()
    if status not in {'pending', 'approved', 'rejected', 'superseded'}:
        raise ValueError('status must be one of: pending, approved, rejected, superseded')

    db = get_db()
    membership = db.execute(
        'SELECT request_id, cluster_id FROM cluster_members WHERE request_id = ? AND cluster_id = ? LIMIT 1',
        (str(request_id), str(cluster_id)),
    ).fetchone()
    if membership is None:
        raise ValueError('cluster member not found')

    latest = db.execute(
        '''
        SELECT status, decision FROM cluster_member_overrides
        WHERE request_id = ? AND cluster_id = ?
        ORDER BY id DESC LIMIT 1
        ''',
        (str(request_id), str(cluster_id)),
    ).fetchone()
    current_status = str((latest['status'] if latest and latest['status'] is not None else (latest['decision'] if latest else 'none')) or 'none').lower()
    if status not in _allowed_override_transitions(current_status):
        raise ValueError(f'invalid transition: {current_status} -> {status}')

    now = _now_iso_utc()

    if status in {'pending', 'approved', 'rejected'}:
        db.execute(
            '''
            UPDATE cluster_member_overrides
            SET status = ?, decision = ?, updated_at = ?
            WHERE request_id = ? AND cluster_id = ? AND status IN ('pending', 'approved', 'rejected')
            ''',
            ('superseded', 'superseded', now, str(request_id), str(cluster_id)),
        )

    db.execute(
        '''
        INSERT INTO cluster_member_overrides (request_id, cluster_id, status, decision, reason, actor, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (str(request_id), str(cluster_id), status, status, str(reason or '').strip(), str(actor or 'unknown'), now, now),
    )
    db.commit()
    return {
        'request_id': str(request_id),
        'cluster_id': str(cluster_id),
        'status': status,
        'decision': status,
        'reason': str(reason or '').strip(),
        'actor': str(actor or 'unknown'),
        'created_at': now,
        'updated_at': now,
    }


def list_cluster_member_override_history(request_id: str, cluster_id: str | None = None, limit=100):
    ensure_demand_cluster_tables()
    if not request_id:
        raise ValueError('request_id is required')

    db = get_db()
    clauses = ['request_id = ?']
    params = [str(request_id)]
    if cluster_id:
        clauses.append('cluster_id = ?')
        params.append(str(cluster_id))

    rows = db.execute(
        f'''
        SELECT request_id, cluster_id, status, decision, reason, actor, created_at, updated_at
        FROM cluster_member_overrides
        WHERE {' AND '.join(clauses)}
        ORDER BY id DESC
        LIMIT ?
        ''',
        tuple(params + [min(max(int(limit), 1), 1000)]),
    ).fetchall()

    items = []
    for row in rows:
        status = str(row['status'] or row['decision'] or 'pending')
        items.append(
            {
                'request_id': str(row['request_id']),
                'cluster_id': str(row['cluster_id']),
                'status': status,
                'decision': status,
                'reason': str(row['reason'] or ''),
                'actor': str(row['actor']),
                'created_at': row['created_at'],
                'updated_at': row['updated_at'] or row['created_at'],
            }
        )

    return {'items': items, 'request_id': str(request_id), 'cluster_id': (str(cluster_id) if cluster_id else None)}



def export_cluster_member_override_history(limit=500, cluster_id=None, status=None, actor=None, date_from=None, date_to=None):
    ensure_demand_cluster_tables()
    db = get_db()

    clauses = []
    params = []
    if cluster_id:
        clauses.append('cluster_id = ?')
        params.append(str(cluster_id))
    if status:
        clauses.append('status = ?')
        params.append(str(status).lower())
    if actor:
        clauses.append('actor = ?')
        params.append(str(actor))
    if date_from:
        clauses.append('created_at >= ?')
        params.append(str(date_from))
    if date_to:
        clauses.append('created_at <= ?')
        params.append(str(date_to))

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    rows = db.execute(
        f'''
        SELECT request_id, cluster_id, status, decision, reason, actor, created_at, updated_at
        FROM cluster_member_overrides
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
        ''',
        tuple(params + [min(max(int(limit), 1), 5000)]),
    ).fetchall()

    items = []
    for row in rows:
        effective = str(row['status'] or row['decision'] or 'pending')
        items.append(
            {
                'request_id': str(row['request_id']),
                'cluster_id': str(row['cluster_id']),
                'status': effective,
                'decision': effective,
                'reason': str(row['reason'] or ''),
                'actor': str(row['actor']),
                'created_at': row['created_at'],
                'updated_at': row['updated_at'] or row['created_at'],
            }
        )

    return {'items': items}


def get_cluster_member_override_metrics(limit=2000):
    ensure_demand_cluster_tables()
    db = get_db()

    status_rows = db.execute(
        '''
        SELECT status, COUNT(*) AS c
        FROM cluster_member_overrides
        GROUP BY status
        ''',
    ).fetchall()
    by_status = {str((row['status'] or 'pending')): int(row['c'] or 0) for row in status_rows}

    actor_rows = db.execute(
        '''
        SELECT actor, COUNT(*) AS c
        FROM cluster_member_overrides
        GROUP BY actor
        ORDER BY c DESC, actor ASC
        LIMIT ?
        ''',
        (min(max(int(limit), 1), 5000),),
    ).fetchall()
    by_actor = [{'actor': str(row['actor']), 'count': int(row['c'] or 0)} for row in actor_rows]

    swept_rows = db.execute(
        '''
        SELECT COUNT(*) AS c
        FROM cluster_member_overrides
        WHERE status = 'superseded' AND LOWER(COALESCE(reason, '')) LIKE '%stale sweep%'
        '''
    ).fetchone()
    stale_swept_total = int(swept_rows['c'] or 0) if swept_rows else 0

    transition_rows = db.execute(
        '''
        SELECT request_id, cluster_id, status, created_at
        FROM cluster_member_overrides
        ORDER BY request_id ASC, cluster_id ASC, created_at ASC, id ASC
        '''
    ).fetchall()
    transition_counts = {}
    previous_by_key = {}
    for row in transition_rows:
        key = f"{str(row['request_id'])}|{str(row['cluster_id'])}"
        current = str(row['status'] or 'pending')
        previous = previous_by_key.get(key)
        if previous is not None:
            tkey = f"{previous}->{current}"
            transition_counts[tkey] = int(transition_counts.get(tkey, 0)) + 1
        previous_by_key[key] = current

    return {
        'by_status': by_status,
        'by_actor': by_actor,
        'stale_swept_total': stale_swept_total,
        'transition_funnel': transition_counts,
    }


def get_cluster_member_override_alerts(rejected_spike_threshold=10, superseded_spike_threshold=10, recent_hours=24):
    ensure_demand_cluster_tables()
    db = get_db()
    recent_hours = max(int(recent_hours), 1)
    cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=recent_hours)
    cutoff_iso = cutoff_dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')

    counts_rows = db.execute(
        '''
        SELECT status, COUNT(*) AS c
        FROM cluster_member_overrides
        WHERE created_at >= ?
        GROUP BY status
        ''',
        (cutoff_iso,),
    ).fetchall()
    recent_counts = {str(row['status'] or 'pending'): int(row['c'] or 0) for row in counts_rows}

    alerts = []
    rejected_count = int(recent_counts.get('rejected', 0))
    superseded_count = int(recent_counts.get('superseded', 0))
    if rejected_count >= int(rejected_spike_threshold):
        alerts.append({'type': 'rejected_spike', 'count': rejected_count, 'threshold': int(rejected_spike_threshold), 'severity': 'high'})
    if superseded_count >= int(superseded_spike_threshold):
        alerts.append({'type': 'superseded_spike', 'count': superseded_count, 'threshold': int(superseded_spike_threshold), 'severity': 'medium'})

    return {
        'recent_hours': recent_hours,
        'cutoff_since': cutoff_iso,
        'recent_counts': recent_counts,
        'alerts': alerts,
    }


def get_cluster_member_override_backlog_summary(now_iso=None):
    ensure_demand_cluster_tables()
    db = get_db()

    if now_iso:
        now_dt = datetime.datetime.fromisoformat(str(now_iso).replace('Z', '+00:00'))
    else:
        now_dt = datetime.datetime.now(datetime.timezone.utc)

    rows = db.execute(
        '''
        SELECT created_at
        FROM cluster_member_overrides
        WHERE status = 'pending'
        ORDER BY created_at ASC
        '''
    ).fetchall()

    buckets = {
        'lt_24h': 0,
        'h24_to_72': 0,
        'd3_to_7': 0,
        'gt_7d': 0,
    }

    for row in rows:
        created_at = str(row['created_at'] or '')
        try:
            created_dt = datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
        except Exception:
            continue
        age_hours = (now_dt - created_dt).total_seconds() / 3600.0
        if age_hours < 24:
            buckets['lt_24h'] += 1
        elif age_hours < 72:
            buckets['h24_to_72'] += 1
        elif age_hours < 168:
            buckets['d3_to_7'] += 1
        else:
            buckets['gt_7d'] += 1

    return {
        'pending_total': int(sum(buckets.values())),
        'aging_buckets': buckets,
        'as_of': now_dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
    }

def sweep_stale_cluster_member_overrides(stale_hours=168, limit=500):
    ensure_demand_cluster_tables()
    stale_hours = max(int(stale_hours), 1)
    limit = min(max(int(limit), 1), 5000)

    cutoff_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=stale_hours)
    cutoff_iso = cutoff_dt.replace(microsecond=0).isoformat().replace('+00:00', 'Z')

    db = get_db()
    rows = db.execute(
        '''
        SELECT id, request_id, cluster_id, reason
        FROM cluster_member_overrides
        WHERE status = 'pending' AND created_at < ?
        ORDER BY created_at ASC
        LIMIT ?
        ''',
        (cutoff_iso, limit),
    ).fetchall()

    swept = 0
    now = _now_iso_utc()
    for row in rows:
        reason = str(row['reason'] or '').strip()
        merged_reason = reason if reason else 'auto-superseded by stale sweep'
        if 'stale sweep' not in merged_reason.lower():
            merged_reason = f"{merged_reason}; stale sweep"
        db.execute(
            "UPDATE cluster_member_overrides SET status = 'superseded', decision = 'superseded', reason = ?, updated_at = ? WHERE id = ?",
            (merged_reason[:500], now, int(row['id'])),
        )
        swept += 1

    pending_row = db.execute("SELECT COUNT(*) AS c FROM cluster_member_overrides WHERE status = 'pending'").fetchone()
    db.commit()

    return {
        'stale_hours': stale_hours,
        'cutoff_before': cutoff_iso,
        'swept': int(swept),
        'pending_after': int(pending_row['c'] or 0) if pending_row else 0,
    }


def rescore_low_confidence_cluster_members(limit=200):
    ensure_demand_cluster_tables()
    db = get_db()
    review_threshold = _review_similarity_threshold()

    rows = db.execute(
        '''
        SELECT m.request_id, m.cluster_id, m.similarity_score,
               c.metadata_json, c.canonical_text,
               r.translated_text, r.original_text
        FROM cluster_members m
        JOIN demand_clusters c ON c.cluster_id = m.cluster_id
        LEFT JOIN citizen_requests r ON r.request_id = m.request_id
        WHERE m.similarity_score < ?
        ORDER BY m.similarity_score ASC, m.created_at DESC
        LIMIT ?
        ''',
        (review_threshold, min(max(int(limit), 1), 5000)),
    ).fetchall()

    rescored = 0
    still_low_confidence = 0
    now = _now_iso_utc()

    for row in rows:
        request_text = str(row['translated_text'] or row['original_text'] or '').strip()
        if not request_text:
            continue
        request_tokens = _tokenize(request_text)
        cluster_tokens, _ = _extract_cluster_tokens(row['metadata_json'], row['canonical_text'])
        similarity = _jaccard_similarity(request_tokens, cluster_tokens)

        db.execute(
            'UPDATE cluster_members SET similarity_score = ?, created_at = ? WHERE request_id = ? AND cluster_id = ?',
            (float(similarity), now, str(row['request_id']), str(row['cluster_id'])),
        )
        rescored += 1
        if float(similarity) < float(review_threshold):
            still_low_confidence += 1

    db.commit()
    return {
        'review_threshold': float(review_threshold),
        'rescored': int(rescored),
        'still_low_confidence': int(still_low_confidence),
    }
