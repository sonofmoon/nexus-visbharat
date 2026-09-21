import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from flask import Flask
from visbharat.services import public_data as data, analyst_workbench as work
from visbharat.services.geo_ward import resolve_geo_ward_context, _to_float

RESOURCE = '11111111-1111-4111-8111-111111111111'


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv('NVB_DISABLE_EXTERNAL_SERVICES', raising=False)
    monkeypatch.setattr(data.requests, 'get', Mock(side_effect=AssertionError('Unexpected external request')))
    app = Flask(__name__)
    app.config.update(PUBLIC_DATA_SNAPSHOT_DIR=str(tmp_path), DATA_GOV_IN_API_KEY='test-only',
                      DATA_GOV_IN_TN_ULB_RESOURCE=RESOURCE, DATA_GOV_IN_LGD_RESOURCE=RESOURCE,
                      DATA_GOV_IN_JJM_FUNDS_RESOURCE=RESOURCE, PUBLIC_DATA_MAX_AGE_SECONDS=86400)
    with app.app_context():
        yield app


def response(rows, total=None):
    raw=json.dumps({'records':rows,'total':len(rows) if total is None else total,'updated_date':'2025-01-01'})
    return Mock(status_code=200, text=raw, content=raw.encode())


def refresh(monkeypatch, source, rows):
    transport=Mock(return_value=response(rows))
    monkeypatch.setattr(data.requests,'get',transport)
    return data.load(source,refresh=True),transport


def test_missing_reference_never_claims_live_verification(app):
    assert work.get_official_ulb_population('Tamil Nadu','Vellore')['population'] is None
    assert not work.get_official_ulb_population('Tamil Nadu','Vellore')['verified_live']
    assert not work.get_official_tirupati_rural_jjm()['verified_live']
    assert not work.get_official_lgd_crosswalk('Tamil Nadu','Vellore')['mapping_validated']
    assert data.requests.get.call_count==0


def test_retained_snapshot_hash_and_no_network_on_page_read(app, monkeypatch):
    result,transport=refresh(monkeypatch,'ulb',[{'name_of_the_ulb':'Vellore','_total':423425}])
    assert result['status']=='retrieved' and result['complete']
    assert not result['publisher_verified']
    assert not result['verified_live']
    assert result['retrieval_verification']=='retrieved_from_configured_api_not_independently_verified'
    assert result['publisher_verification']=='not_independently_verified'
    assert result['field_validation']=='not_completed'
    path=data._path('ulb'); envelope=json.loads(path.read_text())
    assert result['sha256']==hashlib.sha256(envelope['payload'].encode()).hexdigest()
    assert result['sha256']!=RESOURCE
    population=work.get_official_ulb_population('Tamil Nadu','Vellore')
    assert population['population']==423425 and population['status']=='cached'
    assert not population['verified_live'] and transport.call_count==1
    assert work.get_official_ulb_population('Andhra Pradesh','Vellore')['population'] is None


def test_app_does_not_auto_select_a_repository_service_account(monkeypatch):
    monkeypatch.delenv('GOOGLE_APPLICATION_CREDENTIALS', raising=False)
    from visbharat import create_app

    create_app({'DISABLE_EXTERNAL_SERVICES': True})

    assert 'GOOGLE_APPLICATION_CREDENTIALS' not in os.environ


def test_checksum_tampering_is_rejected(app,monkeypatch):
    refresh(monkeypatch,'ulb',[{'name_of_the_ulb':'Vellore','_total':423425}])
    path=data._path('ulb');envelope=json.loads(path.read_text())
    envelope['payload']=envelope['payload'].replace('423425','999999')
    path.write_text(json.dumps(envelope))
    assert data.load('ulb')['status']=='invalid_snapshot'
    assert work.get_official_ulb_population('Tamil Nadu','Vellore')['population'] is None


def test_stale_population_is_not_a_current_planning_fact(app,monkeypatch):
    refresh(monkeypatch,'ulb',[{'name_of_the_ulb':'Vellore','_total':423425}])
    path=data._path('ulb');envelope=json.loads(path.read_text())
    envelope['retrieved_at']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
    path.write_text(json.dumps(envelope))
    assert data.load('ulb')['status']=='stale'
    assert work.get_official_ulb_population('Tamil Nadu','Vellore')['population'] is None


def test_zero_values_are_preserved_and_wrong_states_do_not_match(app,monkeypatch):
    refresh(monkeypatch,'jjm_funds',[{'state_ut':'Tamil Nadu','_2024_25___available_fund':0,
        '_2024_25___reported_utilization':0,'_2024_25___expenditure_under_state_share':0}])
    assert work.get_official_jjm_fiscal_status('Tamil Nadu')['available_fund_cr']==0
    assert work.get_official_jjm_fiscal_status('Andhra Pradesh')['available_fund_cr'] is None


def test_ambiguous_rows_and_partial_results_do_not_supply_population(app,monkeypatch):
    refresh(monkeypatch,'ulb',[{'name_of_the_ulb':'Vellore','_total':1},{'name_of_the_ulb':'Vellore','_total':2}])
    assert work.get_official_ulb_population('Tamil Nadu','Vellore')['population'] is None
    app.config['PUBLIC_DATA_MAX_PAGES']=1
    monkeypatch.setattr(data.requests,'get',Mock(return_value=response([{'name_of_the_ulb':'Vellore','_total':3}],total=100)))
    assert data.load('ulb',refresh=True)['status']=='partial'
    assert work.get_official_ulb_population('Tamil Nadu','Vellore')['population'] is None


def test_lgd_requires_matching_rows_and_does_not_confuse_city_with_district(app,monkeypatch):
    refresh(monkeypatch,'lgd',[{'state_name':'Tamil Nadu','district_name':'Vellore','state_code':33,
        'district_code':572,'local_body_name':'Test Municipality','local_body_code':999,'pincode':'632001'}])
    mapped=work.geography('Tamil Nadu','Vellore')
    assert mapped['district_code']=='LGD-D-572' and mapped['geographic_level']=='district'
    assert mapped['lgd_code']=='572'
    assert work.geography('Andhra Pradesh','Tirupati')['code_system']=='NVB-local-v1'
    assert work.get_official_lgd_crosswalk('Tamil Nadu','Vellore')['local_body_lgd_code'] is None


def test_credentials_and_errors_are_not_exposed(app,monkeypatch):
    import requests
    monkeypatch.setattr(data.requests,'get',Mock(side_effect=requests.RequestException('secret=test-only')))
    result=data.load('ulb',refresh=True)
    assert result['status']=='unavailable'
    assert 'test-only' not in json.dumps(result)
    assert data.requests.get.call_args.kwargs['allow_redirects'] is False


def test_disabled_external_services_override_refresh(app,monkeypatch):
    app.config['DISABLE_EXTERNAL_SERVICES']=True
    assert data.load('ulb',refresh=True)['error']=='External services disabled'
    assert data.requests.get.call_count==0


def test_city_population_is_never_assigned_to_ward_projects(app,monkeypatch):
    refresh(monkeypatch,'ulb',[{'name_of_the_ulb':'Vellore','_total':423425}])
    base=dict(state='Tamil Nadu',district='Vellore',category='Water Supply',reports=1,issues=1,department='Water',active_reports=1)
    monkeypatch.setattr(work,'query',lambda *a:[dict(base,ward='Ward 1'),dict(base,ward='Ward 2')])
    monkeypatch.setattr(work,'reference_index',lambda:{('Tamil Nadu','Vellore'):{'population':100000,'deprivation_index':0.6,'water_coverage':40}})
    monkeypatch.setattr(work,'project_decisions',lambda *a:{})
    items=work.candidates({})
    assert all(p['beneficiaries'] is None for p in items)
    assert all(p['population_context']['population']==423425 for p in items)
    plan=work.allocate(items,1000,10,'mid')
    assert plan['selected_count']==2 and plan['beneficiaries'] is None
    items[0]['beneficiaries']=100;items[1]['beneficiaries']=100
    plan=work.allocate(items,1000,10,'mid')
    assert plan['project_beneficiary_sum']==200 and plan['beneficiaries'] is None


@pytest.mark.parametrize('value',[None,'','invalid','nan','inf',True])
def test_bad_coordinates_do_not_raise(value):
    assert _to_float(value) is None


def test_geo_paths_handle_explicit_text_and_polygon_locations():
    assert resolve_geo_ward_context('Vellore',ward='Ward 1',lat='12.9',lng='79.1')['lat']==12.9
    assert resolve_geo_ward_context('Vellore',text='Water issue in Ward 12')['ward']=='12'
    polygons={'Vellore':[{'ward':'Test ward','points':[{'lat':0,'lng':0},{'lat':2,'lng':0},{'lat':2,'lng':2},{'lat':0,'lng':2}]}]}
    assert resolve_geo_ward_context('Vellore',lat=1,lng=1,ward_polygons=polygons)['ward_source']=='ward_polygon'
