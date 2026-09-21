"""One ordered, hash-linked writer for new events; not external notarization."""
import hashlib
import json
from datetime import datetime, timezone
from flask import request, has_request_context
from .db import get_db

VERSION = 'nvb-audit-v2'


def canonical(row):
    return json.dumps({key: row.get(key) for key in (
        'event_version','chain_seq','actor','action','resource_type','resource_id',
        'details_json','ip_address','created_at','prev_hash')}, sort_keys=True, separators=(',',':'), ensure_ascii=False)


def write_audit_log(actor, action, resource_type, resource_id=None, details=None, *, db=None, commit=True):
    db = db or get_db()
    # Acquire the write/row lock before reading the head, including inside a caller's transaction.
    db.execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    head = dict(db.execute('SELECT * FROM auditor_chain_state WHERE id=1').fetchone())
    row = dict(actor=str(actor), action=str(action), resource_type=str(resource_type), resource_id=resource_id,
               details_json=json.dumps(details or {}, sort_keys=True, separators=(',',':'), ensure_ascii=False),
               ip_address=request.remote_addr if has_request_context() else None,
               created_at=datetime.now(timezone.utc).isoformat(), prev_hash=head['head_hash'],
               chain_seq=head['head_seq']+1, event_version=VERSION)
    row['current_hash'] = hashlib.sha256(canonical(row).encode()).hexdigest()
    keys = list(row)
    db.execute(f"INSERT INTO audit_logs({','.join(keys)}) VALUES({','.join('?' for _ in keys)})", tuple(row[k] for k in keys))
    row['id'] = db.execute('SELECT id FROM audit_logs WHERE chain_seq=?',(row['chain_seq'],)).fetchone()['id']
    db.execute('UPDATE auditor_chain_state SET head_seq=?,head_hash=?,head_id=? WHERE id=1',
               (row['chain_seq'],row['current_hash'],row['id']))
    if commit: db.commit()
    return {**row, 'timestamp':row['created_at'], 'details':details or {}}


def security_detection(detector, severity, message, resource_id=None, actor='unauthenticated'):
    """Only typed detections. Alert-history reads are not incidents."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute('''INSERT INTO auditor_detections(detector,severity,message,resource_id,occurred_at,rule_version)
        VALUES(?,?,?,?,?,?)''', (detector,severity,message,resource_id,now,'nvb-security-v1'))
    write_audit_log(actor,'security_detection','security',resource_id,
                    {'detector':detector,'severity':severity,'message':message},db=db,commit=False)
    db.commit()
