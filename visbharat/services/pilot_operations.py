"""Explicit operational bootstrap and opening; never convert an existing demo corpus."""
import json
import click
from . import pilot
from ..db import get_db


def register(app):
    @app.cli.command('pilot-bootstrap')
    @click.option('--operational',is_flag=True,required=True)
    def bootstrap(operational):
        if app.config.get('DEMO_MODE') or app.config.get('SEED_DEMO_DATA'):
            raise click.ClickException('Set DEMO_MODE=false and SEED_DEMO_DATA=false')
        db=get_db()
        if pilot.rows('SELECT request_id FROM citizen_requests LIMIT 1') or pilot.rows('SELECT pilot_id FROM pilot_programmes LIMIT 1'):
            raise click.ClickException('Bootstrap requires a fresh migrated operational database')
        pilot.create_default();p=pilot.programme(pilot.PILOT_ID);cfg=p['config']
        cfg.update(live_intake_enabled=False,notice_version='pilot-operational-v1',
            notice='Opt-in water-service pilot. Reports are used for service review and planning. Contact your participating officer for access, correction or withdrawal.',
            routing={'Vellore':'Vellore review team — assignment pending','Tirupati':'Tirupati review team — assignment pending'})
        db.execute("UPDATE pilot_programmes SET data_mode='operational',status='preparing',config_json=? WHERE pilot_id=?",(json.dumps(cfg),pilot.PILOT_ID))
        db.commit();click.echo('Empty operational programme prepared. Intake remains closed until evidence gates are accepted.')

    @app.cli.command('pilot-open')
    @click.option('--authority-reference',required=True)
    def open_programme(authority_reference):
        from .auditor_workbench import safe_uri
        from ..audit import write_audit_log
        reference=safe_uri(authority_reference);pid=app.config.get('PILOT_ID');p=pilot.programme(pid)
        if app.config.get('DEMO_MODE') or p['data_mode']!='operational' or reference.startswith('urn:nvb:demo:'):
            raise click.ClickException('Opening requires an operational deployment and authority evidence')
        checks=pilot.rows('SELECT * FROM pilot_checks WHERE pilot_id=?',(pid,))
        if len(checks)!=len(pilot.GATES) or any(c['status']!='accepted' for c in checks):
            raise click.ClickException('Every launch gate must have accepted evidence')
        if any(l['verification_status']!='reviewed' for l in pilot.rows('SELECT * FROM pilot_locations WHERE pilot_id=?',(pid,))):
            raise click.ClickException('All enrolled locations need reviewed official geography')
        identities=pilot.rows('SELECT DISTINCT u.role FROM users u JOIN pilot_identities i ON i.user_id=u.id JOIN pilot_memberships m ON m.user_id=u.id WHERE m.pilot_id=? AND m.active=1',(pid,))
        if {r['role'] for r in identities}!={'admin','analyst','auditor'}:raise click.ClickException('Provision named Admin, Analyst and independent Auditor identities')
        if not app.config.get('OIDC_ISSUER'):raise click.ClickException('Configure the ministry identity provider')
        p['config']['live_intake_enabled']=True
        db=get_db();db.execute("UPDATE pilot_programmes SET status='active',config_json=?,version=version+1,updated_at=? WHERE pilot_id=?",(json.dumps(p['config']),pilot.now(),pid))
        write_audit_log('deployment_operator','pilot_opened','pilot',pid,{'authority_reference':reference},commit=False)
        db.commit();click.echo('Operational intake enabled under the recorded authority reference.')
