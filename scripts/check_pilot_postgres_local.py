"""Run PostgreSQL contracts using workspace-local binaries; no system service."""
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
BIN=ROOT/'scratch/pilot-implementation/tools/pgsql/bin'
DATA=ROOT/'scratch/pilot-implementation/postgres-test-data'
LOG=ROOT/'scratch/pilot-implementation/postgres-test.log'
flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
def run(args,**kw):return subprocess.run([str(a) for a in args],cwd=ROOT,creationflags=flags,check=True,**kw)
if not (DATA/'PG_VERSION').exists():
    run([BIN/'initdb.exe','-D',DATA,'-U','pilot_test','-A','trust','--encoding=UTF8','--locale=C'],stdout=subprocess.DEVNULL)
with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
run([BIN/'pg_ctl.exe','-D',DATA,'-l',LOG,'-o',f'-p {port} -h 127.0.0.1','-w','start'],stdout=subprocess.DEVNULL)
try:
    env={**os.environ,'NVB_TEST_POSTGRES_URL':f'postgresql://pilot_test@127.0.0.1:{port}/postgres','NVB_DISABLE_EXTERNAL_SERVICES':'1','JURY_REQUIRE_LIVE_MODELS':'0',
        'PATH':str(BIN)+os.pathsep+os.environ.get('PATH',''),'NVB_TEST_POSTGRES_BIN':str(BIN),'PYTHONIOENCODING':'utf-8'}
    result=subprocess.run([sys.executable,'-m','unittest','tests.test_ministry_pilot_postgres','-v','-f'],cwd=ROOT,env=env,creationflags=flags,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,encoding='utf-8',errors='replace')
    print(result.stdout.encode('ascii',errors='backslashreplace').decode())
    import re
    summary=re.search(r'Ran (\d+) tests in ([\d.]+)s',result.stdout)
    report={'backend':'PostgreSQL 16.10','exit_code':result.returncode,'scope':'Isolated per-test schemas in workspace-local server','cloud_resources_used':False,
        'tests':int(summary[1]) if summary else None,'seconds':float(summary[2]) if summary else None}
    (ROOT/'docs/evaluation/ministry-pilot-postgres.json').write_text(json.dumps(report,indent=2))
finally:run([BIN/'pg_ctl.exe','-D',DATA,'-m','fast','-w','stop'],stdout=subprocess.DEVNULL)
sys.exit(result.returncode)
