import os
if not os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') and not os.environ.get('GOOGLE_AI_API_KEY'):
    os.environ.setdefault('NVB_DISABLE_EXTERNAL_SERVICES', '1')
    os.environ.setdefault('JURY_REQUIRE_LIVE_MODELS', '0')

from visbharat import create_app

app = create_app()


if __name__ == '__main__':
    print("Starting VisBharat on http://127.0.0.1:5000 ...", flush=True)
    app.run(host='127.0.0.1', port=5000, debug=False, use_reloader=False)
