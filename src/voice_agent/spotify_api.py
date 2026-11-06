import json
import os
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

SECRETS_DIR = Path.home() / ".secrets" / "spotify"
SCOPES = "user-read-playback-state user-modify-playback-state"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
ACCOUNTS_BASE = "https://accounts.spotify.com"
API_BASE = "https://api.spotify.com/v1"


class SpotifyError(Exception):
    """kind: "premium"|"no_device"|"auth"|"rate"|"network"|"other"; no secrets."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class Credentials:
    client_id: str
    client_secret: str

    def __repr__(self) -> str:
        return f"Credentials(client_id={self.client_id!r}, client_secret='***')"

    @classmethod
    def load(cls, folder: Path = SECRETS_DIR) -> "Credentials":
        id_path, sec_path = folder / "client-id.txt", folder / "api-key.txt"
        if not id_path.is_file():
            raise SpotifyError("auth", "missing client-id.txt")
        if not sec_path.is_file():
            raise SpotifyError("auth", "missing api-key.txt")
        return cls(id_path.read_text().strip(), sec_path.read_text().strip())


class TokenStore:
    def __init__(self, path: Path = SECRETS_DIR / "token.json") -> None:
        self.path = path

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def save(self, token: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # never readable by others
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(token))
        os.replace(tmp, self.path)


def authorize_url(client_id: str, state: str) -> str:
    qs = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{ACCOUNTS_BASE}/authorize?{qs}"


def _post_token(http: httpx.Client, creds: Credentials, data: dict[str, str]) -> dict[str, Any]:
    try:
        url = f"{ACCOUNTS_BASE}/api/token"
        resp = http.post(url, data=data, auth=(creds.client_id, creds.client_secret))
    except (httpx.TransportError, httpx.TimeoutException) as err:
        raise SpotifyError("network", "Failed to connect to Spotify accounts") from err
    if resp.status_code == 429:
        raise SpotifyError("rate", "Spotify rate limit reached")
    if resp.status_code in (400, 401):
        raise SpotifyError("auth", "Spotify login expired; run make spotify-login")
    if resp.status_code != 200:
        raise SpotifyError("other", f"Spotify auth failed ({resp.status_code})")
    body = resp.json()
    return body if isinstance(body, dict) else {}


def exchange_code(
    creds: Credentials,
    code: str,
    http: httpx.Client | None = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    client = http or httpx.Client(timeout=5.0)
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    body = _post_token(client, creds, data)
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", ""),
        "expires_at": clock() + body.get("expires_in", 3600),
    }


def _item(k: str, uri: str, title: str, subtitle: str, dur_s: int | None = None) -> dict[str, Any]:
    return {"kind": k, "uri": uri, "title": title, "subtitle": subtitle, "duration_s": dur_s}


class SpotifyClient:
    def __init__(
        self,
        creds: Credentials,
        store: TokenStore,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._creds = creds
        self._store = store
        self._http = http or httpx.Client(timeout=5.0)
        self._clock = clock
        self._albums: dict[str, str] = {}  # track uri -> album uri, from search

    def _refresh(self, token: dict[str, Any]) -> dict[str, Any]:
        refr = token.get("refresh_token")
        if not refr:
            raise SpotifyError("auth", "Spotify login expired; run make spotify-login")
        data = {"grant_type": "refresh_token", "refresh_token": refr}
        body = _post_token(self._http, self._creds, data)
        new_token = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token") or refr,
            "expires_at": self._clock() + body.get("expires_in", 3600),
        }
        self._store.save(new_token)
        return new_token

    def _request(
        self, method: str, path: str, params: Any = None, json_body: Any = None
    ) -> httpx.Response:
        token = self._store.load()
        if not token or "access_token" not in token:
            raise SpotifyError("auth", "Not logged in to Spotify; run make spotify-login")
        refreshed = False
        if self._clock() >= token.get("expires_at", 0) - 60:
            token = self._refresh(token)
            refreshed = True

        def send(tok: str) -> httpx.Response:
            try:
                h = {"Authorization": f"Bearer {tok}"}
                u = f"{API_BASE}{path}"
                return self._http.request(method, u, params=params, json=json_body, headers=h)
            except (httpx.TransportError, httpx.TimeoutException) as err:
                raise SpotifyError("network", "Failed to communicate with Spotify API") from err

        resp = send(token["access_token"])
        if resp.status_code == 401 and not refreshed:
            token = self._refresh(token)
            resp = send(token["access_token"])
        if resp.status_code == 401:
            raise SpotifyError("auth", "Spotify login expired; run make spotify-login")
        if resp.status_code == 429:
            raise SpotifyError("rate", "Spotify rate limit reached")

        try:
            reason = resp.json().get("error", {}).get("reason")
        except Exception:
            reason = None
        if resp.status_code == 403:
            k = "premium" if reason == "PREMIUM_REQUIRED" else "other"
            raise SpotifyError(k, "Spotify access forbidden")
        if resp.status_code == 404:
            k = "no_device" if (reason == "NO_ACTIVE_DEVICE" or "/me/player" in path) else "other"
            raise SpotifyError(k, "No active Spotify device found")
        if resp.status_code >= 400:
            raise SpotifyError("other", f"Spotify API error ({resp.status_code})")
        return resp

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        clean = query.strip()
        if not clean:
            return []
        p = {"q": clean[:100], "type": "track,artist,playlist", "limit": limit}
        data = self._request("GET", "/search", params=p).json()
        tracks: list[dict[str, Any]] = []
        for t in data.get("tracks", {}).get("items", []) or []:
            if t:
                subs = ", ".join(a["name"] for a in t.get("artists", []) if "name" in a)
                d = (t["duration_ms"] // 1000) if t.get("duration_ms") is not None else None
                if (t.get("album") or {}).get("uri"):
                    self._albums[t["uri"]] = t["album"]["uri"]
                tracks.append(_item("track", t["uri"], t["name"], subs, d))
        exact_a: list[dict[str, Any]] = []
        other_a: list[dict[str, Any]] = []
        q_norm = clean[:100].lower()
        for a in data.get("artists", {}).get("items", []) or []:
            if a:
                dest = exact_a if a.get("name", "").strip().lower() == q_norm else other_a
                dest.append(_item("artist", a["uri"], a["name"], "Artist"))
        playlists = [
            _item("playlist", p["uri"], p["name"], sub)
            for p in data.get("playlists", {}).get("items", []) or []
            if p and (sub := p.get("owner", {}).get("display_name") or "Playlist")
        ]
        return (exact_a + tracks + playlists + other_a)[:limit]

    def device_id(self, name: str) -> str | None:
        resp = self._request("GET", "/me/player/devices")
        for dev in resp.json().get("devices", []):
            if dev.get("name") == name:
                val = dev.get("id")
                return str(val) if val is not None else None
        return None

    def play(self, device_id: str, uri: str) -> None:
        # the desktop app ignores a bare {"uris": [track]}: start the track's album at that track
        album = self._albums.get(uri)
        body: dict[str, Any] = (
            {"context_uri": album, "offset": {"uri": uri}}
            if album
            else {"uris": [uri]}
            if uri.startswith("spotify:track:")
            else {"context_uri": uri}
        )
        self._request("PUT", "/me/player/play", params={"device_id": device_id}, json_body=body)

    def pause(self) -> None:
        self._request("PUT", "/me/player/pause")

    def resume(self, device_id: str) -> None:
        self._request("PUT", "/me/player/play", params={"device_id": device_id})

    def next(self) -> None:
        self._request("POST", "/me/player/next")
