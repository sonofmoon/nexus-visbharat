import os
import argparse
import http.server
import json
import socketserver
import sys
import urllib.parse
import webbrowser
import requests

CLIENT_ID = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
REDIRECT_PORT = 8080
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/"

auth_code = None


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;text-align:center;padding:50px;'>"
                b"<h2 style='color:#137333;'>Authorization Successful!</h2>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )
        else:
            error = params.get("error", ["Unknown error"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(f"<html><body><h2>Authorization Failed</h2><p>{error}</p></body></html>".encode())

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description="Get Gmail OAuth Refresh Token")
    parser.add_argument("--client-id", default=CLIENT_ID, help="OAuth Client ID")
    parser.add_argument("--client-secret", default=CLIENT_SECRET, help="OAuth Client Secret")
    parser.add_argument("--port", type=int, default=REDIRECT_PORT, help="Local redirect port")
    args = parser.parse_args()

    client_id = args.client_id
    client_secret = args.client_secret
    redirect_uri = f"http://localhost:{args.port}/"

    auth_params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)

    print("\n=======================================================")
    print("  GMAIL OAUTH REFRESH TOKEN GENERATOR")
    print("=======================================================\n")
    print("Opening browser for login as nexusvisbharat@gmail.com...")
    print("If your browser doesn't open automatically, copy & paste this URL into your browser:\n")
    print(auth_url)
    print("\nWaiting for authorization...\n")

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

    global auth_code
    server = socketserver.TCPServer(("localhost", args.port), OAuthCallbackHandler)
    server.timeout = 180

    while auth_code is None:
        server.handle_request()

    server.server_close()

    if not auth_code:
        print("ERROR: Did not receive authorization code.")
        sys.exit(1)

    print("Authorization code received! Exchanging for refresh token...")

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": auth_code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    resp = requests.post(token_url, data=data, timeout=15)
    token_json = resp.json()

    if "error" in token_json:
        print(f"\nERROR exchanging code: {token_json.get('error')} - {token_json.get('error_description')}")
        sys.exit(1)

    refresh_token = token_json.get("refresh_token")
    access_token = token_json.get("access_token")

    print("\n=======================================================")
    print("  SUCCESS! TOKENS RECEIVED")
    print("=======================================================\n")
    print(f"REFRESH TOKEN:\n{refresh_token}\n")
    print(f"ACCESS TOKEN:\n{access_token[:20]}...\n")
    print("Save the REFRESH TOKEN above!")

    with open("scratch/gmail_oauth_tokens.json", "w", encoding="utf-8") as f:
        json.dump(token_json, f, indent=2)
    print("Saved tokens to scratch/gmail_oauth_tokens.json")


if __name__ == "__main__":
    main()
