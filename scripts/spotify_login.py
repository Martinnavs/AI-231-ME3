"""One-time Spotify OAuth login for the laptop."""

import argparse
import secrets
import sys
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from voice_agent.spotify_api import (
    Credentials,
    TokenStore,
    authorize_url,
    exchange_code,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Spotify one-time OAuth login")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL without opening a browser",
    )
    args = parser.parse_args()

    creds = Credentials.load()
    store = TokenStore()
    state = secrets.token_urlsafe(16)
    url = authorize_url(creds.client_id, state)

    print(f"Authorize Spotify by visiting:\n{url}")
    if not args.no_browser:
        webbrowser.open(url)

    auth_code: str | None = None
    state_mismatch = False

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal auth_code, state_mismatch
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(parsed.query)
            got_state = params.get("state", [""])[0]
            if got_state != state:
                state_mismatch = True
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"State mismatch")
                return

            auth_code = params.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Spotify login successful! You can close this window.")

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 8888), CallbackHandler)
    server.timeout = 120.0

    while auth_code is None and not state_mismatch:
        server.handle_request()

    server.server_close()

    if state_mismatch:
        print("state mismatch", file=sys.stderr)
        sys.exit(2)

    if not auth_code:
        print("Login timed out or no code received", file=sys.stderr)
        sys.exit(1)

    token = exchange_code(creds, auth_code)
    store.save(token)
    print(f"Saved the Spotify login to {store.path}")


if __name__ == "__main__":
    main()
