"""Serve the isolated rehearsal on loopback. No cloud resources are provisioned."""
from prepare_ministry_pilot import DEST,configuration,prepare
from visbharat import create_app
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--port',type=int,default=5001)
args=parser.parse_args()
if not DEST.exists():prepare()
app=create_app(configuration())
app.run(host='127.0.0.1',port=args.port,debug=False,use_reloader=False)
