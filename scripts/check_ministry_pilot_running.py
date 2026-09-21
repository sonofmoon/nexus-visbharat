"""Read-only localhost smoke check; never print embedded demo credentials."""
import json
import re
import requests
base='http://127.0.0.1:5001'
page=requests.get(base+'/pilot',timeout=15);page.raise_for_status()
cfg=json.loads(re.search(r'window.NVBPilotConfig=(.*?);</script>',page.text).group(1))
assert cfg['demo'] is True,'This smoke check is only for the synthetic local rehearsal'
result={'url':base+'/pilot','pages':{},'scopes':{}}
for path in ('/pilot','/pilot/submit','/dashboard?pilot_id='+cfg['pilotId']+'&workspace=analyst','/health/ready'):
    response=requests.get(base+path,timeout=15);response.raise_for_status();result['pages'][path]=response.status_code
for role in ('admin','vellore','tirupati'):
    response=requests.get(base+'/api/v2/pilot/snapshot',headers={'Authorization':'Bearer '+cfg['tokens'][role]},timeout=15)
    response.raise_for_status();data=response.json()
    result['scopes'][role]={'reports':data['stats']['total_complaints'],'district':data['scope']['district'],'data_mode':data['programme']['data_mode']}
assert result['scopes']['vellore']['district']=='Vellore'
assert result['scopes']['tirupati']['district']=='Tirupati'
print(json.dumps(result,indent=2))
