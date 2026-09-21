"""Validate portable decisions and a serialization round-trip on an isolated fixture."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jsonschema import Draft202012Validator
from tests.test_analyst_workbench import AnalystWorkbenchTest

root=Path(__file__).resolve().parents[1]
fixture=AnalystWorkbenchTest();fixture.setUp()
try:
    data=fixture.call();p=data['scenario']['items'][0]
    detail=fixture.call('/projects/'+p['project_id'])
    exchange=fixture.call('/projects/'+p['project_id']+'/export')['record']
    schema=json.loads((root/'docs/release/analyst-decision.schema.json').read_text(encoding='utf-8'))
    validator=Draft202012Validator(schema);validator.validate(exchange)
    imported=json.loads(json.dumps(exchange,ensure_ascii=False,sort_keys=True));validator.validate(imported)
    assert imported==exchange
    response=fixture.client.post('/api/v2/analyst/exchange/validate',json=imported,headers=fixture.headers)
    assert response.status_code==200,response.get_json()
    assert response.get_json()['record']==exchange
    assert response.get_json()['persisted'] is False
    assert fixture.client.post('/api/v2/analyst/exchange/validate',json={'status':'approved'},headers=fixture.headers).status_code==400
    assert fixture.client.get('/api/v2/analyst/snapshot').status_code==401
    viewer={'Authorization':'Bearer auditor-test'}
    assert fixture.client.post('/api/v2/analyst/projects/'+p['project_id']+'/draft',json={'notes':'read-only user test'},headers=viewer).status_code==403
    result={'status':'passed','checks':['Export from the actual project API','Schema validation','Lossless API validation round-trip without persistence','Invalid import rejected','Unauthenticated read rejected','Auditor mutation rejected'],
            'schema':'docs/release/analyst-decision.schema.json','geography':'Stable NVB local codes; official LGD crosswalk remains unverified',
            'limitations':['This validates the local exchange contract, not interoperability certification or another government deployment.']}
    path=root/'docs/evaluation/interoperability.json';path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    (root/'docs/release/analyst-decision.sample.json').write_text(json.dumps(exchange,indent=2),encoding='utf-8')
    print(json.dumps(result))
finally:fixture.doCleanups()
