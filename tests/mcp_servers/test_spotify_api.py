import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from voice_agent.spotify_api import Credentials, SpotifyClient, SpotifyError, TokenStore

CREDS = Credentials("test_client_id", "test_secret_12345")
DEF_TOK = {"access_token": "tok", "refresh_token": "refr", "expires_at": 2000.0}


def make_client(
    tmp_path: Path,
    handler: Any,
    token: dict[str, Any] | None = None,
    clock_val: float = 100.0,
    creds: Credentials = CREDS,
) -> tuple[SpotifyClient, TokenStore]:
    store = TokenStore(tmp_path / "token.json")
    if token is not None:
        store.save(token)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return SpotifyClient(creds, store, http=http, clock=lambda: clock_val), store


def test_load_credentials_strips_and_hides_secret(tmp_path: Path) -> None:
    (tmp_path / "client-id.txt").write_text("  client_id_val  \n")
    (tmp_path / "api-key.txt").write_text("  secret_val_xyz  \n")
    creds = Credentials.load(tmp_path)
    assert creds.client_id == "client_id_val"
    assert creds.client_secret == "secret_val_xyz"
    assert "secret_val_xyz" not in repr(creds)
    assert "client_id_val" in repr(creds)

    (tmp_path / "client-id.txt").unlink()
    with pytest.raises(SpotifyError) as exc_id:
        Credentials.load(tmp_path)
    assert exc_id.value.kind == "auth" and "missing client-id.txt" in str(exc_id.value)


def test_refreshes_when_expired_and_saves(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}")
        if req.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "new_acc", "expires_in": 3600})
        return httpx.Response(200, json={"devices": [{"id": "d1", "name": "laptop"}]})

    old_tok = {"access_token": "old_acc", "refresh_token": "refr_123", "expires_at": 50.0}
    client, store = make_client(tmp_path, handler, token=old_tok)
    assert client.device_id("laptop") == "d1"
    assert calls == ["POST /api/token", "GET /v1/me/player/devices"]
    saved = store.load()
    assert (
        saved is not None
        and saved["access_token"] == "new_acc"
        and saved["refresh_token"] == "refr_123"
    )
    assert (store.path.stat().st_mode & 0o777) == 0o600


def test_401_refreshes_once_then_auth_error(tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}")
        if req.url.path == "/api/token":
            return httpx.Response(200, json={"access_token": "tok2", "expires_in": 3600})
        return httpx.Response(401, json={"error": {"status": 401, "message": "Expired"}})

    client, _ = make_client(tmp_path, handler, token=DEF_TOK)
    with pytest.raises(SpotifyError) as exc:
        client.pause()
    assert exc.value.kind == "auth" and "Spotify login expired; run make spotify-login" in str(
        exc.value
    )
    assert calls == ["PUT /v1/me/player/pause", "POST /api/token", "PUT /v1/me/player/pause"]


def test_premium_required_maps_to_premium(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"status": 403, "reason": "PREMIUM_REQUIRED"}})

    client, _ = make_client(tmp_path, handler, token=DEF_TOK)
    with pytest.raises(SpotifyError) as exc:
        client.play("dev", "spotify:track:123")
    assert exc.value.kind == "premium"


def test_no_active_device_maps_to_no_device(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"status": 404, "reason": "NO_ACTIVE_DEVICE"}})

    client, _ = make_client(tmp_path, handler, token=DEF_TOK)
    with pytest.raises(SpotifyError) as exc:
        client.resume("dev")
    assert exc.value.kind == "no_device"


def test_search_shapes_and_ordering(tmp_path: Path) -> None:
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(
            200,
            json={
                "artists": {"items": [{"name": "Radiohead", "uri": "spotify:artist:1"}]},
                "tracks": {
                    "items": [
                        {
                            "name": "Karma Police",
                            "uri": "spotify:track:2",
                            "duration_ms": 261000,
                            "artists": [{"name": "Radiohead"}],
                        }
                    ]
                },
                "playlists": {
                    "items": [
                        {
                            "name": "Radiohead Best Of",
                            "uri": "spotify:playlist:3",
                            "owner": {"display_name": "Spotify"},
                        }
                    ]
                },
            },
        )

    client, _ = make_client(tmp_path, handler, token=DEF_TOK)
    assert client.search("   ") == []
    assert not called

    res = client.search("radiohead")
    assert len(res) == 3
    assert (
        res[0]["kind"] == "artist"
        and res[0]["title"] == "Radiohead"
        and res[0]["duration_s"] is None
    )
    assert (
        res[1]["kind"] == "track"
        and res[1]["duration_s"] == 261
        and res[1]["subtitle"] == "Radiohead"
    )
    assert (
        res[2]["kind"] == "playlist"
        and res[2]["subtitle"] == "Spotify"
        and res[2]["duration_s"] is None
    )


def test_play_body_track_vs_context(tmp_path: Path) -> None:
    bodies: list[tuple[str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        bodies.append((str(req.url), json.loads(req.content.decode("utf-8"))))
        return httpx.Response(204)

    client, _ = make_client(tmp_path, handler, token=DEF_TOK)
    client.play("d1", "spotify:track:abc")
    assert bodies[0][0] == "https://api.spotify.com/v1/me/player/play?device_id=d1"
    assert bodies[0][1] == {"uris": ["spotify:track:abc"]}

    client.play("d2", "spotify:playlist:xyz")
    assert bodies[1][0] == "https://api.spotify.com/v1/me/player/play?device_id=d2"
    assert bodies[1][1] == {"context_uri": "spotify:playlist:xyz"}


def test_no_secret_in_errors(tmp_path: Path) -> None:
    sec, tok, refr = "SECRET_XYZ_999", "ACCESS_TOK_888", "REFR_TOK_777"
    creds = Credentials("cid", sec)
    token = {"access_token": tok, "refresh_token": refr, "expires_at": 2000.0}

    errors_to_test = [
        (401, {"error": {"status": 401, "message": "Bad token"}}, "auth"),
        (403, {"error": {"status": 403, "reason": "PREMIUM_REQUIRED"}}, "premium"),
        (404, {"error": {"status": 404, "reason": "NO_ACTIVE_DEVICE"}}, "no_device"),
        (429, {}, "rate"),
        (500, {}, "other"),
    ]

    for status, body, expected_kind in errors_to_test:

        def handler(req: httpx.Request, s=status, b=body) -> httpx.Response:
            if req.url.path == "/api/token":
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(s, json=b)

        client, _ = make_client(tmp_path, handler, token=token, creds=creds)
        with pytest.raises(SpotifyError) as exc:
            client.pause()
        assert exc.value.kind == expected_kind
        msg = str(exc.value)
        assert sec not in msg and tok not in msg and refr not in msg

    def net_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client_net, _ = make_client(tmp_path, net_handler, token=token, creds=creds)
    with pytest.raises(SpotifyError) as exc_net:
        client_net.pause()
    assert exc_net.value.kind == "network" and sec not in str(exc_net.value)
