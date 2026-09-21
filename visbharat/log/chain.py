import hashlib
from ..db import get_db
from ..audit import write_audit_log, canonical, VERSION

GENESIS_HASH = '0'*64


def append_transparency_log(actor, action, resource_type, details, db=None):
    return write_audit_log(actor,action,resource_type,details=details,db=db)


def verify_chain_integrity(limit=None, db=None):
    """Check the complete protected segment through a captured head in batches.

    Legacy entries remain unverified. Independently retained checkpoints are needed
    to detect an administrator rewriting both events and the head. The compatibility
    ``limit`` argument never silently truncates this verification.
    """
    db = db or get_db()
    head = dict(db.execute('SELECT * FROM auditor_chain_state WHERE id=1').fetchone())
    legacy = db.execute('SELECT COUNT(*) AS n FROM audit_logs WHERE id<=?',(head['legacy_end_id'],)).fetchone()['n']
    unexpected = db.execute('SELECT COUNT(*) AS n FROM audit_logs WHERE id>? AND chain_seq IS NULL',(head['legacy_end_id'],)).fetchone()['n']
    last_seq, prev, last_id, verified = 0, GENESIS_HASH, head['legacy_end_id'], 0
    failure = None
    while last_seq < head['head_seq']:
        rows = db.execute('SELECT * FROM audit_logs WHERE chain_seq>? AND chain_seq<=? ORDER BY chain_seq LIMIT 2000',
                          (last_seq,head['head_seq'])).fetchall()
        if not rows:
            failure = 'Protected events are missing before the recorded head'; break
        for item in rows:
            row = dict(item)
            valid = (row['chain_seq']==last_seq+1 and row['prev_hash']==prev and
                     row['event_version']==VERSION and row['id']>last_id and row['current_hash'] and
                     row['current_hash']==hashlib.sha256(canonical(row).encode()).hexdigest())
            if not valid:
                failure = f"Event {row['id']} failed sequence, link or content verification"; break
            prev, last_id, last_seq = row['current_hash'],row['id'],row['chain_seq']
            verified += 1
        if failure: break
    if not failure and (prev!=head['head_hash'] or last_id!=head['head_id'] or unexpected):
        failure = 'Recorded head does not match the event stream, or unsigned events were appended'
    protected_valid = failure is None
    return {'valid':protected_valid and legacy==0 and verified>0,'protected_valid':protected_valid,
            'status':'failed' if failure else ('legacy_unverified' if legacy else ('verified' if verified else 'empty')),
            'count':verified,'verified_events':verified,'legacy_unverified':legacy,'unexpected_unsigned':unexpected,
            'total_events':legacy+head['head_seq']+unexpected,'head_seq':head['head_seq'],
            'head_id':head['head_id'],'head_hash':head['head_hash'],'legacy_end_id':head['legacy_end_id'],
            'reason':failure,'complete_through_declared_head':protected_valid,'external_anchor':'Not independently anchored',
            'message':failure or f'{verified} protected events checked; {legacy} legacy events remain unverified.'}
