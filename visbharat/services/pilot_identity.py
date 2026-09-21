"""OIDC code/PKCE login with explicit local identity and geography provisioning."""
import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode, urlparse
import requests
from flask import current_app, g, request, session, redirect, jsonify
from . import pilot


def session_user():
    uid=session.get('pilot_user_id')
    if not uid:return None
    if session.get('pilot_expires',0)<=time.time():session.clear();return None
    users=pilot.rows('SELECT id,name,role FROM users WHERE id=?',(uid,))
    if not users:return None
    if request.method not in ('GET','HEAD','OPTIONS') and not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('pilot_csrf','missing')):
        raise PermissionError('Refresh the page before submitting this action')
    return users[0]


def endpoint(name):
    url=current_app.config.get(name,'')
    p=urlparse(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password:
        raise PermissionError('Configure the approved HTTPS identity endpoints')
    return url


def register_identity(app):
    from ..db import get_db
    import click

    @app.get('/pilot/login')
    def pilot_login():
        verifier=secrets.token_urlsafe(48);state=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32)
        session['pilot_oidc']={'verifier':verifier,'state':state,'nonce':nonce,'created':time.time()}
        query={'client_id':app.config.get('OIDC_CLIENT_ID'),'redirect_uri':endpoint('OIDC_REDIRECT_URI'),
            'response_type':'code','scope':'openid profile','state':state,'nonce':nonce,
            'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),
            'code_challenge_method':'S256'}
        return redirect(endpoint('OIDC_AUTHORIZATION_ENDPOINT')+'?'+urlencode(query))

    @app.get('/pilot/callback')
    def pilot_callback():
        flow=session.pop('pilot_oidc',{})
        if not flow or time.time()-flow['created']>600 or not hmac.compare_digest(request.args.get('state',''),flow['state']):
            raise PermissionError('Login expired or state did not match')
        payload={'grant_type':'authorization_code','code':request.args.get('code',''),'redirect_uri':endpoint('OIDC_REDIRECT_URI'),
            'client_id':app.config.get('OIDC_CLIENT_ID'),'code_verifier':flow['verifier']}
        if app.config.get('OIDC_CLIENT_SECRET'):payload['client_secret']=app.config['OIDC_CLIENT_SECRET']
        response=requests.post(endpoint('OIDC_TOKEN_ENDPOINT'),data=payload,timeout=15,allow_redirects=False)
        if response.status_code!=200:raise PermissionError('Identity provider did not complete sign-in')
        from google.oauth2 import id_token
        from google.auth.transport.requests import Request
        try:
            claims=id_token.verify_token(response.json().get('id_token',''),Request(),audience=app.config['OIDC_CLIENT_ID'],certs_url=endpoint('OIDC_JWKS_URI'))
        except Exception:raise PermissionError('Identity token could not be verified')
        if claims.get('iss')!=app.config.get('OIDC_ISSUER') or claims.get('nonce')!=flow['nonce']:
            raise PermissionError('Identity issuer or nonce did not match')
        identities=pilot.rows('SELECT user_id FROM pilot_identities WHERE issuer=? AND subject=?',(claims['iss'],claims['sub']))
        if not identities:raise PermissionError('A programme administrator must provision this identity')
        session.clear();session.update(pilot_user_id=identities[0]['user_id'],pilot_expires=min(claims['exp'],time.time()+3600),pilot_csrf=secrets.token_urlsafe(24))
        return redirect('/pilot/dashboard')

    @app.post('/pilot/logout')
    def pilot_logout():
        session_user();session.clear();return jsonify(success=True)

    @app.cli.command('pilot-provision-identity')
    @click.option('--subject',required=True)
    @click.option('--name',required=True)
    @click.option('--role',type=click.Choice(['admin','analyst','auditor']),required=True)
    @click.option('--state',default='')
    @click.option('--district',default='')
    def provision(subject,name,role,state,district):
        issuer=app.config.get('OIDC_ISSUER');pid=app.config.get('PILOT_ID')
        if not issuer or not pid:raise click.ClickException('Configure the issuer and programme first')
        if pilot.rows('SELECT user_id FROM pilot_identities WHERE issuer=? AND subject=?',(issuer,subject)):
            raise click.ClickException('Identity exists; review its membership instead of creating another')
        if district and not state:raise click.ClickException('District requires a state')
        locs=pilot.rows('SELECT state,district FROM pilot_locations WHERE pilot_id=?',(pid,))
        if state and not any(r['state']==state and (not district or r['district']==district) for r in locs):raise click.ClickException('Unenrolled geography')
        # Generated credential is never returned; operational officers use OIDC.
        credential=secrets.token_urlsafe(48);stamp=pilot.now();db=get_db()
        db.execute('INSERT INTO users(name,api_token,role,created_at) VALUES(?,?,?,?)',(name,credential,role,stamp))
        uid=pilot.rows('SELECT id FROM users WHERE api_token=?',(credential,))[0]['id']
        db.execute('INSERT INTO pilot_identities VALUES(?,?,?)',(issuer,subject,uid))
        db.execute('INSERT INTO pilot_memberships VALUES(?,?,?,?,1)',(pid,uid,state,district));db.commit()
        click.echo('Named identity and explicit programme assignment provisioned.')
