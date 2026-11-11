"""GitHub Pages publishing, against a real local bare repository (no network, no token)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import Client

from voice_agent.mcp_servers import pages
from voice_agent.mcp_servers.pages import Settings, publish_app

TOKEN = "tok-AbCdEf0123456789XYZ"


def git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    repo = tmp_path / "remote.git"
    git("init", "-q", "--bare", "-b", "main", str(repo))
    return repo


@pytest.fixture
def settings(tmp_path: Path, bare: Path) -> Settings:
    apps = tmp_path / "apps"
    (apps / "demo").mkdir(parents=True)
    (apps / "demo" / "index.html").write_text("<h1>Hi</h1>")
    (apps / "demo" / "style.css").write_text("h1 { color: teal }")
    token = tmp_path / "pat"
    token.write_text(TOKEN + "\n")
    return Settings(
        repo=str(bare), branch="main", token_file=token, apps_root=apps, allow_local=True
    )


def remote_files(bare: Path) -> list[str]:
    return git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", "main").split()


def test_a_dry_run_says_what_it_would_publish_and_pushes_nothing(
    settings: Settings, bare: Path
) -> None:
    said = publish_app("demo", True, settings, secrets=[])
    assert said.startswith(pages.READY + " demo: 2 files")
    assert "public at" in said
    assert subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "main"]).returncode != 0


def test_publish_pushes_the_folder_with_nojekyll_and_the_agent_identity(
    settings: Settings, bare: Path
) -> None:
    said = publish_app("demo", False, settings, secrets=[])
    assert said.startswith("Published demo:")
    assert remote_files(bare) == [".nojekyll", "demo/index.html", "demo/style.css"]
    author = git("--git-dir", str(bare), "log", "-1", "--format=%an <%ae>", "main").strip()
    assert author == "Voice Agent <voice-agent@localhost>"


def test_publishing_again_unchanged_makes_no_commit_and_a_change_makes_one(
    settings: Settings, bare: Path
) -> None:
    publish_app("demo", False, settings, secrets=[])
    again = publish_app("demo", False, settings, secrets=[])
    assert "already published and unchanged" in again
    assert git("--git-dir", str(bare), "rev-list", "--count", "main").strip() == "1"
    (settings.apps_root / "demo" / "index.html").write_text("<h1>Changed</h1>")
    publish_app("demo", False, settings, secrets=[])
    assert git("--git-dir", str(bare), "rev-list", "--count", "main").strip() == "2"


def test_what_is_already_in_the_repository_is_kept(
    settings: Settings, bare: Path, tmp_path: Path
) -> None:
    seed = tmp_path / "seed"
    git("clone", "-q", str(bare), str(seed))
    git("checkout", "-q", "-b", "main", cwd=seed)
    (seed / "README.md").write_text("mine")
    (seed / "other").mkdir()
    (seed / "other" / "page.html").write_text("<p>other</p>")
    git("add", "-A", cwd=seed)
    git("commit", "-q", "-m", "seed", cwd=seed)
    git("push", "-q", "origin", "main", cwd=seed)
    publish_app("demo", False, settings, secrets=[])
    assert remote_files(bare) == [
        ".nojekyll",
        "README.md",
        "demo/index.html",
        "demo/style.css",
        "other/page.html",
    ]
    assert git("--git-dir", str(bare), "rev-list", "--count", "main").strip() == "2"


def test_a_folder_that_changed_loses_its_old_files(settings: Settings, bare: Path) -> None:
    publish_app("demo", False, settings, secrets=[])
    (settings.apps_root / "demo" / "style.css").unlink()
    publish_app("demo", False, settings, secrets=[])
    assert remote_files(bare) == [".nojekyll", "demo/index.html"]


def refused(settings: Settings, app: str = "demo", secrets: list[str] | None = None) -> str:
    said = publish_app(app, False, settings, secrets=secrets if secrets is not None else [])
    assert said.startswith("I couldn't publish"), said
    return said


def test_a_symlink_the_delegate_planted_is_refused_and_not_read(
    settings: Settings, tmp_path: Path, bare: Path
) -> None:
    secret = tmp_path / "host-secret.txt"
    secret.write_text("do-not-publish-me-12345")
    (settings.apps_root / "demo" / "leak.html").symlink_to(secret)
    assert "cannot publish symlink: leak.html" in refused(settings)
    assert subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "main"]).returncode != 0


def test_an_app_folder_that_is_a_symlink_out_of_the_apps_folder_is_refused(
    settings: Settings, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "index.html").write_text("<p>x</p>")
    (settings.apps_root / "linked").symlink_to(outside)
    assert "linked" in refused(settings, "linked")


def test_a_page_needs_an_index_and_only_allowed_file_types(settings: Settings) -> None:
    folder = settings.apps_root / "demo"
    (folder / "index.html").unlink()
    assert "no index.html" in refused(settings)
    (folder / "index.html").write_text("<p>x</p>")
    (folder / "server.py").write_text("print(1)")
    assert "server.py is not an allowed file type" in refused(settings)


def test_hidden_files_are_left_out(settings: Settings, bare: Path) -> None:
    (settings.apps_root / "demo" / ".env").write_text("API_KEY=whatever")
    publish_app("demo", False, settings, secrets=[])
    assert ".env" not in " ".join(remote_files(bare))


def test_size_limits(settings: Settings) -> None:
    small = Settings(**{**settings.__dict__, "max_file_bytes": 10})
    assert "is larger than" in refused(small)
    few = Settings(**{**settings.__dict__, "max_files": 1})
    assert "has 2 files" in refused(few)


@pytest.mark.parametrize(
    "leak",
    [
        "var k='ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" + "';",
        "const key = 'AIza" + "SyA-1234567890abcdefghijklmnopqrstu';",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        'api_key = "abcdef0123456789abcdef"',
    ],
)
def test_known_secret_shapes_are_refused(settings: Settings, leak: str) -> None:
    (settings.apps_root / "demo" / "app.js").write_text(leak)
    assert "app.js looks like it contains a secret" in refused(settings)


def test_the_exact_contents_of_a_secrets_file_are_refused_whatever_their_shape(
    settings: Settings, tmp_path: Path
) -> None:
    store = tmp_path / "secrets"
    (store / "svc").mkdir(parents=True)
    (store / "svc" / "key.txt").write_text("plain-words-secret-0042\n")
    (settings.apps_root / "demo" / "notes.txt").write_text("see plain-words-secret-0042 ok")
    found = pages.secret_values(store)
    assert "plain-words-secret-0042" in found
    assert "notes.txt looks like it contains a secret" in refused(settings, secrets=found)


def test_short_or_wordlike_secret_file_contents_are_not_scanned_for(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("yes\n")
    (tmp_path / "b").write_text("two words here\nsecond line\n")
    (tmp_path / "c").write_text("some-long-lowercase-words-only\n")  # no digit, one case: a phrase
    assert pages.secret_values(tmp_path) == []


def test_a_json_secret_file_contributes_its_values_not_its_keys(tmp_path: Path) -> None:
    (tmp_path / "token.json").write_text(
        '{"access_token": "ya29.A0ARrdaM-secretvalue99", "expires_at": 1762900000,'
        ' "refresh_token": "1//0gRefreshValueXYZ123", "token_type": "Bearer"}'
    )
    found = pages.secret_values(tmp_path)
    assert found == ["1//0gRefreshValueXYZ123", "ya29.A0ARrdaM-secretvalue99"]
    assert not any("access_token" in v or "expires_at" in v for v in found)


def test_a_notes_file_does_not_turn_its_code_fragments_into_secrets(tmp_path: Path) -> None:
    (tmp_path / "Untitled").write_text('apps/some_game/index.html\n"Authorization": "Bearer x"\n')
    assert pages.secret_values(tmp_path) == []


@pytest.mark.parametrize("name", ["../demo", "Demo", "", "a b", "demo/x", ".hidden"])
def test_bad_app_names_are_refused(settings: Settings, name: str) -> None:
    assert "app name" in refused(settings, name)


@pytest.mark.parametrize("branch", ["--force", "-x", "a b", "main;rm", ""])
def test_a_branch_name_that_could_be_an_option_is_refused(settings: Settings, branch: str) -> None:
    bad = Settings(**{**settings.__dict__, "branch": branch})
    assert "branch name is not valid" in publish_app("demo", False, bad, secrets=[])


def test_an_unknown_app_is_refused(settings: Settings) -> None:
    assert "no app folder called nope" in refused(settings, "nope")


def test_nothing_is_published_when_no_repository_is_set(settings: Settings) -> None:
    unset = Settings(**{**settings.__dict__, "repo": ""})
    assert "not set up" in publish_app("demo", False, unset, secrets=[])


def test_a_missing_token_file_is_a_sentence_not_a_crash(settings: Settings, tmp_path: Path) -> None:
    gone = Settings(**{**settings.__dict__, "token_file": tmp_path / "missing"})
    assert "token file is missing" in publish_app("demo", False, gone, secrets=[])


def test_the_token_goes_through_the_environment_never_a_command_line_and_is_redacted(
    settings: Settings,
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs["env"]))
        if "push" in args:
            return subprocess.CompletedProcess(args, 1, "", f"remote: denied for {TOKEN}")
        return subprocess.run(args, **kwargs)

    said = publish_app("demo", False, settings, secrets=[], run=run)
    assert said.startswith("I couldn't publish demo: git push failed")
    assert TOKEN not in said and "[token]" in said
    assert all(TOKEN not in " ".join(args) for args, _ in calls)
    assert any(env.get("PAGES_TOKEN") == TOKEN for _, env in calls)
    pushes = [args for args, _ in calls if "push" in args]
    assert pushes and all("--force" not in a and "-f" not in a for a in pushes)


def test_pages_url_for_a_project_and_for_a_user_site() -> None:
    assert pages.pages_url("https://github.com/Alice/demo-repo", "game") == (
        "https://alice.github.io/demo-repo/game/"
    )
    assert pages.pages_url("https://github.com/alice/alice.github.io.git", "game") == (
        "https://alice.github.io/game/"
    )
    assert pages.pages_url("/tmp/remote.git", "game") is None
    assert pages.pages_url("https://example.com/a/b", "game") is None


def test_a_repository_that_is_not_a_github_address_is_refused_outside_tests(
    settings: Settings,
) -> None:
    real = Settings(**{**settings.__dict__, "allow_local": False})
    assert "https://github.com/<owner>/<repo>" in publish_app("demo", False, real, secrets=[])


def _api(status: dict[str, int], seen: list[httpx.Request]) -> httpx.Client:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status[request.method])

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_pages_is_switched_on_when_it_is_off_and_left_alone_when_on() -> None:
    cfg = Settings(repo="https://github.com/alice/demo", branch="main")
    seen: list[httpx.Request] = []
    assert pages.ensure_pages(cfg, TOKEN, _api({"GET": 404, "POST": 201}, seen)) is None
    assert [r.method for r in seen] == ["GET", "POST"]
    assert b'"branch":"main"' in seen[1].content.replace(b" ", b"")
    seen.clear()
    assert pages.ensure_pages(cfg, TOKEN, _api({"GET": 200}, seen)) is None
    assert [r.method for r in seen] == ["GET"]
    note = pages.ensure_pages(cfg, TOKEN, _api({"GET": 404, "POST": 403}, []))
    assert note and "not switched on" in note


def test_the_settings_file_is_read_and_an_unset_repo_is_not_configured(tmp_path: Path) -> None:
    cfg = tmp_path / "publish.toml"
    cfg.write_text('repo = ""\nbranch = "gh"\nallow_local = true\nmax_files = 5\n')
    loaded = pages.load_settings(cfg)
    assert loaded.branch == "gh" and loaded.max_files == 5
    assert loaded.allow_local is False  # not settable from the file
    assert not pages.configured(cfg) and not pages.configured(tmp_path / "missing.toml")
    cfg.write_text('repo = "https://github.com/a/b"\n')
    assert pages.configured(cfg)


def test_the_shipped_config_publishes_nothing_until_a_repo_is_set() -> None:
    assert not pages.configured(pages.CONFIG)


async def test_the_server_offers_one_tool_that_defaults_to_a_dry_run(settings: Settings) -> None:
    async with Client(pages.build_server(settings)) as client:
        assert [t.name for t in await client.list_tools()] == ["publish_pages"]
        result = await client.call_tool("publish_pages", {"app": "nope"})
    assert "no app folder called nope" in str(result.structured_content["result"])
