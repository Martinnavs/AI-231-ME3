"""T1 and T4: the static server for agent-written web apps: own folders only, always the CSP."""

import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path

import pytest

from voice_agent.apps_server import CSP, make_server, resolve


@pytest.fixture
def root(tmp_path: Path) -> Path:
    apps = tmp_path / "apps"
    (apps / "flappy_bird").mkdir(parents=True)
    (apps / "flappy_bird" / "index.html").write_text("<canvas></canvas><script>1</script>")
    (apps / "flappy_bird" / "game.js").write_text("var x = 1;")
    (apps / "flappy_bird" / ".secret").write_text("hidden")
    (apps / "flappy_bird" / "sub").mkdir()
    (apps / "flappy_bird" / "sub" / "a.txt").write_text("a")
    (tmp_path / "outside.txt").write_text("OUTSIDE")
    (apps / "flappy_bird" / "link.txt").symlink_to(tmp_path / "outside.txt")
    (apps / "linked").symlink_to(tmp_path)
    (apps / "Bad Name").mkdir()
    (apps / "Bad Name" / "index.html").write_text("x")
    return apps


@pytest.fixture
def get(root: Path) -> Iterator[object]:
    server = make_server(root, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    def fetch(path: str, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path)
        r = conn.getresponse()
        out = (r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
        conn.close()
        return out

    yield fetch
    server.shutdown()
    server.server_close()


def test_it_binds_to_loopback_only(root: Path) -> None:
    server = make_server(root, 0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_an_app_is_served_with_the_csp_on_every_response(get) -> None:  # type: ignore[no-untyped-def]
    for path, status in (
        ("/flappy_bird/", 200),
        ("/flappy_bird/game.js", 200),
        ("/flappy_bird/missing.js", 404),
        ("/", 404),
        ("/nope/", 404),
    ):
        code, headers, _ = get(path)
        assert code == status, path
        assert headers["content-security-policy"] == CSP, path
        assert headers["x-content-type-options"] == "nosniff"
    code, headers, body = get("/flappy_bird/")
    assert b"<canvas>" in body and headers["content-type"].startswith("text/html")
    assert "connect-src 'none'" in CSP and "default-src 'self'" in CSP
    assert "img-src 'self' data:" in CSP and "script-src 'self' 'unsafe-inline'" in CSP


def test_the_bare_name_redirects_to_the_folder_and_nothing_is_listed(get) -> None:  # type: ignore[no-untyped-def]
    code, headers, _ = get("/flappy_bird")
    assert code == 301 and headers["location"] == "/flappy_bird/"
    assert get("/flappy_bird/sub/")[0] == 404  # a folder without index.html: no listing
    assert b"a.txt" not in get("/flappy_bird/sub/")[2]


@pytest.mark.parametrize(
    "path",
    [
        "/flappy_bird/../outside.txt",
        "/flappy_bird/%2e%2e/outside.txt",
        "/flappy_bird/..%2foutside.txt",
        "/flappy_bird/%2e%2e%2foutside.txt",
        "/..%2f..%2fetc/passwd",
        "/flappy_bird/sub/../../../outside.txt",
        "/flappy_bird//etc/passwd",
        "/flappy_bird/.secret",
        "/flappy_bird/link.txt",
        "/linked/outside.txt",
        "/Bad%20Name/index.html",
        "/flappy_bird/%00",
        "/flappy_bird/..\\outside.txt",
    ],
)
def test_traversal_hidden_files_symlinks_and_odd_names_are_refused(get, path: str) -> None:  # type: ignore[no-untyped-def]
    code, _, body = get(path)
    assert code in (301, 404) and b"OUTSIDE" not in body and b"hidden" not in body


def test_only_reading_is_allowed(get) -> None:  # type: ignore[no-untyped-def]
    assert get("/flappy_bird/", "POST")[0] == 405
    assert get("/flappy_bird/", "HEAD")[0] == 200


def test_resolve_maps_only_files_inside_the_app(root: Path) -> None:
    assert resolve(root, "/flappy_bird/game.js") == root / "flappy_bird" / "game.js"
    assert resolve(root, "/flappy_bird/") == root / "flappy_bird" / "index.html"
    assert resolve(root, "/flappy_bird") == "/flappy_bird/"
    assert resolve(root, "/flappy_bird/../x") is None


def test_it_refuses_to_bind_every_interface(root: Path) -> None:
    import pytest

    for host in ("0.0.0.0", "", "::"):  # noqa: S104
        with pytest.raises(ValueError):
            make_server(root, 0, host)
    server = make_server(root, 0, "127.0.0.1")
    server.server_close()
