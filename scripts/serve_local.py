import os
import sys

# Ensure local execution uses deterministic simulation if external cloud keys are not configured
if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') and not os.environ.get('GOOGLE_AI_API_KEY'):
    os.environ.setdefault('NVB_DISABLE_EXTERNAL_SERVICES', '1')
    os.environ.setdefault('JURY_REQUIRE_LIVE_MODELS', '0')

from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from visbharat import create_app

app = create_app({'JURY_REQUIRE_LIVE_MODELS': False})

if __name__ == '__main__':
    print("=================================================", flush=True)
    print("    NEXUS VISBHARAT LOCAL SERVER READY           ", flush=True)
    print("    URL: http://127.0.0.1:5000/                  ", flush=True)
    print("=================================================", flush=True)
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
