"""Y3: the music resolver (laptop): a search phrase in, stream URLs out, token required."""

import json
import sys
import threading
import time
import types
from http.client import HTTPConnection
from typing import Any

import pytest

from voice_agent import music_resolver as mr
from voice_agent.music_resolver import Resolver, make_server, sanitise, wants_long


def entry(
    title: str, minutes: float, i: int = 1, url: str | None = "https://x.example/a"
) -> dict[str, Any]:
    return {"title": title, "duration": int(minutes * 60), "url": url, "id": f"id{i}"}


def test_the_query_is_a_search_phrase_of_at_most_100_characters() -> None:
    assert sanitise("  Taylor   Swift ") == "Taylor Swift"
    assert len(sanitise("a" * 300) or "") == 100
    assert sanitise("play\x00 me\n\tsomething") == "play me something"
    for bad in (
        "",
        "   ",
        "https://youtube.com/watch?v=x",
        "www.example.com/x",
        "x http://evil",
        "vid.be",
    ):
        assert sanitise(bad) is None, bad


def test_a_mix_or_a_long_one_is_allowed_to_be_long() -> None:
    assert (
        wants_long("relaxing music mix")
        and wants_long("one hour of rain")
        and wants_long("nonstop opm")
    )
    assert not wants_long("Taylor Swift") and not wants_long("jazz")


def test_results_over_the_limit_are_skipped_unless_the_query_asks_for_a_mix() -> None:
    entries = [
        entry("Mix", 70, 1),
        entry("Song", 4, 2),
        entry("Live set", 40, 3),
        entry("Short", 3, 4),
    ]
    r = Resolver(lambda q, n: entries, max_minutes=15)
    assert [x["id"] for x in r.search("jazz")] == ["id2", "id4"]
    assert [x["id"] for x in r.search("jazz mix")] == ["id1", "id2", "id3", "id4"]


def test_an_entry_without_a_https_url_or_a_duration_is_dropped() -> None:
    entries = [
        entry("a", 3, 1, url=None),
        entry("b", 3, 2, url="http://x"),
        {"title": "c", "url": "https://x", "id": "3"},
        entry("d", 3, 4),
    ]
    assert [x["id"] for x in Resolver(lambda q, n: entries).search("x")] == ["id4"]


def test_the_title_is_cut_and_the_fields_are_the_ones_the_pi_needs() -> None:
    (hit,) = Resolver(lambda q, n: [entry("T" * 500, 3)]).search("x")
    assert set(hit) == {"title", "duration", "stream_url", "id"} and len(hit["title"]) == 200


def test_an_answer_is_cached_for_ten_minutes() -> None:
    calls: list[tuple[str, int]] = []
    now = [0.0]

    def extract(q: str, n: int) -> list[dict[str, Any]]:
        calls.append((q, n))
        return [entry("Song", 3)]

    r = Resolver(extract, clock=lambda: now[0])
    r.search("Taylor Swift")
    now[0] = 599
    r.search("taylor swift")  # same phrase, any case
    assert calls == [("Taylor Swift", 5)]
    now[0] = 601
    r.search("Taylor Swift")
    assert len(calls) == 2


def test_a_search_that_takes_too_long_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mr, "TIMEOUT_S", 0.2)
    r = Resolver(lambda q, n: (time.sleep(1.0), [])[1])
    with pytest.raises(TimeoutError):
        r.search("slow")


def test_a_url_is_not_a_search() -> None:
    r = Resolver(lambda q, n: pytest.fail("must not search"))
    with pytest.raises(ValueError):
        r.search("https://youtube.com/watch?v=abc")


def test_yt_dlp_is_asked_for_ytsearch5_audio_only_without_cookies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class YDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            seen["opts"] = opts

        def __enter__(self) -> "YDL":
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def extract_info(self, target: str, download: bool) -> dict[str, Any]:
            seen["target"], seen["download"] = target, download
            return {"entries": [entry("a", 3), None]}

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=YDL))
    out = mr.ytdlp_extract("relaxing music", 5)
    assert seen["target"] == "ytsearch5:relaxing music" and seen["download"] is False
    assert seen["opts"]["format"] == "bestaudio/best" and seen["opts"]["cookiefile"] is None
    assert seen["opts"]["socket_timeout"] == 8.0 and len(out) == 1


@pytest.fixture
def served():  # type: ignore[no-untyped-def]
    resolver = Resolver(lambda q, n: [entry(f"hit for {q}", 3)])
    server = make_server(resolver, "127.0.0.1", 0, "secret")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def get(path: str, token: str | None = "secret") -> tuple[int, dict[str, Any]]:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path, headers={"Authorization": f"Bearer {token}"} if token else {})
        r = conn.getresponse()
        body = json.loads(r.read() or b"{}")
        conn.close()
        return r.status, body

    yield get
    server.shutdown()
    server.server_close()


def test_the_service_needs_the_token_and_only_answers_search(served) -> None:  # type: ignore[no-untyped-def]
    assert served("/search?q=jazz", token=None)[0] == 401
    assert served("/search?q=jazz", token="wrong")[0] == 401
    status, body = served("/search?q=jazz")
    assert status == 200 and body["results"][0]["title"] == "hit for jazz"
    assert served("/other?q=jazz")[0] == 404
    assert served("/search?q=")[0] == 400
    assert served("/search?q=https%3A%2F%2Fyoutube.com%2Fx")[0] == 400


def test_it_binds_one_address_only() -> None:
    for host in ("0.0.0.0", "", "::"):  # noqa: S104
        with pytest.raises(ValueError):
            make_server(Resolver(lambda q, n: []), host, 0, "t")


def test_a_failing_search_is_one_bad_gateway_answer() -> None:
    def boom(q: str, n: int) -> list[dict[str, Any]]:
        raise RuntimeError("yt-dlp changed")

    server = make_server(Resolver(boom), "127.0.0.1", 0, "t")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("GET", "/search?q=x", headers={"Authorization": "Bearer t"})
    r = conn.getresponse()
    assert r.status == 502 and json.loads(r.read()) == {"error": "RuntimeError"}
    server.shutdown()
    server.server_close()
