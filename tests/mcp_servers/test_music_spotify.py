from typing import Any

from voice_agent.mcp_servers.music import MusicControl
from voice_agent.spotify_api import SpotifyError

CFG_SPOTIFY = {
    "source": "spotify",
    "spotify_device": "ME3 Laptop",
    "spotify_control": "connect",
    "default_query": "relaxing music mix",
    "level_db": -6.0,
    "step_db": 3.0,
    "min_db": -30.0,
}

CFG_YT = {
    "source": "youtube",
    "default_query": "relaxing music mix",
    "level_db": -6.0,
    "step_db": 3.0,
    "min_db": -30.0,
}


class StubSpotify:
    def __init__(
        self,
        hits: list[dict[str, Any]] | Exception | None = None,
        device_id_val: str | None = "dev_laptop",
        play_err: Exception | None = None,
        pause_err: Exception | None = None,
        resume_err: Exception | None = None,
        next_err: Exception | None = None,
    ) -> None:
        self.hits = hits
        self.dev_id = device_id_val
        self.play_err = play_err
        self.pause_err = pause_err
        self.resume_err = resume_err
        self.next_err = next_err
        self.played: list[tuple[str, str]] = []
        self.resumed: list[str] = []
        self.next_count = 0

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if isinstance(self.hits, Exception):
            raise self.hits
        if self.hits is None:
            return [
                {
                    "kind": "track",
                    "uri": "spotify:track:song1",
                    "title": "Song 1",
                    "subtitle": "Artist",
                }
            ]
        return self.hits

    def device_id(self, name: str) -> str | None:
        return self.dev_id

    def play(self, device_id: str, uri: str) -> None:
        if self.play_err:
            raise self.play_err
        self.played.append((device_id, uri))

    def pause(self) -> None:
        if self.pause_err:
            raise self.pause_err

    def resume(self, device_id: str) -> None:
        if self.resume_err:
            raise self.resume_err
        self.resumed.append(device_id)

    def next(self) -> None:
        if self.next_err:
            raise self.next_err
        self.next_count += 1


def test_spotify_play_uses_device_and_emits_event() -> None:
    stub = StubSpotify()
    ctrl = MusicControl(lambda q: [], dict(CFG_SPOTIFY), spotify=stub)
    out = ctrl.act("play", "radiohead")
    assert out["ok"] is True
    assert out["sentence"] == "Playing Song 1."
    assert out["event"] == {
        "event": "music",
        "action": "play",
        "source": "spotify",
        "title": "Song 1",
        "db": -6.0,
    }
    assert stub.played == [("dev_laptop", "spotify:track:song1")]
    assert ctrl.state == "playing"


def test_spotify_no_device_sentence() -> None:
    stub = StubSpotify(device_id_val=None)
    ctrl = MusicControl(lambda q: [], dict(CFG_SPOTIFY), spotify=stub)
    out = ctrl.act("play", "radiohead")
    assert out == {"ok": False, "sentence": "The laptop's Spotify player isn't running."}
    assert ctrl.state == "idle"


def test_spotify_premium_error_sentence() -> None:
    stub = StubSpotify(play_err=SpotifyError("premium", "Premium required"))
    ctrl = MusicControl(lambda q: [], dict(CFG_SPOTIFY), spotify=stub)
    out = ctrl.act("play", "radiohead")
    assert out == {"ok": False, "sentence": "Spotify needs a Premium account for that."}
    assert ctrl.state == "idle"

    err_mappings = [
        ("auth", "Spotify needs me to log in again."),
        ("rate", "Spotify is busy, try again in a moment."),
        ("network", "I can't reach Spotify right now."),
        ("other", "I can't reach Spotify right now."),
    ]
    for kind, expected_sent in err_mappings:
        stub_err = StubSpotify(play_err=SpotifyError(kind, "fail"))
        c = MusicControl(lambda q: [], dict(CFG_SPOTIFY), spotify=stub_err)
        res = c.act("play", "x")
        assert res["sentence"] == expected_sent and not res["ok"] and c.state == "idle"


def test_spotify_pause_resume_next_stop() -> None:
    stub = StubSpotify()
    ctrl = MusicControl(lambda q: [], dict(CFG_SPOTIFY), spotify=stub)
    ctrl.act("play", "radiohead")

    out_pause = ctrl.act("pause")
    assert out_pause == {
        "ok": True,
        "sentence": "Paused.",
        "event": {"event": "music", "action": "pause", "source": "spotify"},
    }
    assert ctrl.state == "paused"
    assert ctrl.act("pause")["sentence"] == "It is already paused."

    out_resume = ctrl.act("resume")
    assert out_resume == {
        "ok": True,
        "sentence": "Resuming.",
        "event": {"event": "music", "action": "resume", "source": "spotify"},
    }
    assert ctrl.state == "playing"
    assert stub.resumed == ["dev_laptop"]

    out_next = ctrl.act("next")
    assert out_next == {"ok": True, "sentence": "Next."}
    assert "event" not in out_next
    assert stub.next_count == 1

    out_stop = ctrl.act("stop")
    assert out_stop == {
        "ok": True,
        "sentence": "Music stopped.",
        "event": {"event": "music", "action": "stop", "source": "spotify"},
    }
    assert ctrl.state == "idle"


def test_spotify_idle_commands_inactive() -> None:
    stub = StubSpotify()
    ctrl = MusicControl(lambda q: [], dict(CFG_SPOTIFY), spotify=stub)
    for action in ("pause", "stop", "next", "volume_up", "volume_down"):
        assert ctrl.act(action) == {"ok": False, "inactive": True, "sentence": ""}, action


def test_youtube_path_unchanged() -> None:
    hits = [{"title": "YT Song", "duration": 200, "stream_url": "https://yt/0", "id": "0"}]
    ctrl = MusicControl(lambda q: hits, dict(CFG_YT))
    out_play = ctrl.act("play", "jazz")
    assert out_play["ok"] is True
    assert out_play["sentence"] == "Playing YT Song."
    assert out_play["event"] == {
        "event": "music",
        "action": "play",
        "url": "https://yt/0",
        "title": "YT Song",
        "db": -6.0,
    }
    out_pause = ctrl.act("pause")
    assert out_pause == {
        "ok": True,
        "sentence": "Paused.",
        "event": {"event": "music", "action": "pause"},
    }


def test_titles_not_in_latin_script_are_said_as_the_song() -> None:
    from voice_agent.mcp_servers.music import spoken_title

    assert spoken_title("バードランドの子守唄") == "the song"
    assert spoken_title("Plastic Love") == "Plastic Love"
    assert spoken_title("Café del Mar") == "Café del Mar"


def test_spotify_local_control_needs_no_device_and_sends_the_uri() -> None:
    class Stub:
        def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
            return [
                {
                    "kind": "track",
                    "uri": "spotify:track:x",
                    "title": "Misty",
                    "subtitle": "",
                    "duration_s": 180,
                }
            ]

        def device_id(self, name: str) -> str | None:
            raise AssertionError("local control must not look for a Connect device")

    ctrl = MusicControl(lambda q: [], {**CFG_SPOTIFY, "spotify_control": "local"}, spotify=Stub())  # type: ignore[arg-type]
    out = ctrl.act("play", "misty")
    assert out["sentence"] == "Playing Misty." and out["event"]["uri"] == "spotify:track:x"
    assert ctrl.act("pause")["event"] == {"event": "music", "action": "pause", "source": "spotify"}
    assert ctrl.act("resume")["event"] == {
        "event": "music",
        "action": "resume",
        "source": "spotify",
    }
    assert ctrl.act("next")["event"] == {"event": "music", "action": "next", "source": "spotify"}


def test_bare_play_after_a_stop_replays_the_last_request() -> None:
    queries: list[str] = []

    def search(q: str) -> list[dict[str, Any]]:
        queries.append(q)
        return [{"title": "Crazy", "stream_url": "u", "duration": 160}]

    ctrl = MusicControl(search, CFG_YT)
    ctrl.act("play", "crazy patsy cline")
    ctrl.act("stop")
    ctrl.act("play", "")
    assert queries == ["crazy patsy cline", "crazy patsy cline"]
