"""Publish one app folder to GitHub Pages (fastmcp, stdio). Host side: the delegate's sandbox never
sees the token, and only the front agent is offered this tool, behind a spoken yes the pipeline
enforces in code (agent_tools.py, `_pending_publish`).

    uv run python -m voice_agent.mcp_servers.pages          # config/publish.toml

`publish_pages(app, dry_run)`: the folder `<apps_root>/<app>` is copied with no-follow reads into a
private staging directory (a symlink the delegate planted, or a file swapped mid-check, cannot
reach anything), the staged copy is checked (an index.html, allowed file types, size limits, no
secrets: known key shapes, and the exact contents of the owner's `~/.secrets` files), and only then
pushed with an ordinary push (never forced) to `<repo>` as `<app>/` next to a `.nojekyll`. A dry run
stops after the checks and says what would be published and where.

Nothing here reads the token except the push: it goes to git through an askpass script and the
environment, never on a command line, and any output is redacted before it is returned.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastmcp import FastMCP

from voice_agent.mcp_servers._paths import PathEscapeError, open_dir
from voice_agent.mcp_servers.code import _DIR_FLAGS, _copy_tree, _Tally

log = logging.getLogger("voice_agent.pages")
CONFIG = Path(__file__).resolve().parents[3] / "config" / "publish.toml"
# a passed dry run starts with this; agent_tools.py (`PUBLISH_READY`) then asks "Should I go ahead?"
READY = "Ready to publish"
APP_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
BRANCH = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._/-]{0,60}"
)  # never starts with "-": no option injection
GITHUB_REPO = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")
# what a published page may hold: text the browser runs or shows, and small images and fonts
TEXT_SUFFIXES = frozenset({".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".svg"})
ALLOWED_SUFFIXES = TEXT_SUFFIXES | {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".woff",
    ".woff2",
}
SECRET_SHAPES = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\btvly-[A-Za-z0-9_-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"""(?i)\b(?:api[_-]?key|secret|token|password)["']?\s*[:=]\s*["'][^"'\s]{16,}["']"""
    ),
)
SECRETS_DIR = Path("~/.secrets")
MIN_SECRET_CHARS = 12  # a shorter file content would match ordinary words


@dataclass(frozen=True)
class Settings:
    repo: str = ""
    branch: str = "main"
    token_file: Path = Path("~/.secrets/github/pat")
    apps_root: Path = Path("~/agent_sandbox/workspace/apps")
    max_files: int = 100
    max_bytes: int = 5 * 1024 * 1024
    max_file_bytes: int = 1024 * 1024
    allow_local: bool = False  # tests only: a local path or file:// repository


def load_settings(path: Path | None = None) -> Settings:
    path = path or Path(os.environ.get("PUBLISH_CONFIG") or CONFIG)
    try:
        raw = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return Settings()
    known = {
        k: v for k, v in raw.items() if k in Settings.__dataclass_fields__ and k != "allow_local"
    }
    for key in ("token_file", "apps_root"):
        if key in known:
            known[key] = Path(str(known[key]))
    return Settings(**known)


def configured(path: Path | None = None) -> bool:
    return bool(load_settings(path).repo.strip())


def pages_url(repo: str, name: str) -> str | None:
    """Where GitHub serves `name` for a github.com repository, else None."""
    match = GITHUB_REPO.fullmatch(repo.strip())
    if match is None:
        return None
    owner, project = match.groups()
    root = f"https://{owner.lower()}.github.io/"
    if project.lower() == f"{owner.lower()}.github.io":
        return f"{root}{name}/"
    return f"{root}{project}/{name}/"


def _json_strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [t for v in node.values() for t in _json_strings(v)]
    if isinstance(node, list):
        return [t for v in node for t in _json_strings(v)]
    return []


def _looks_secret(token: str) -> bool:
    """A long single token with a digit or both letter cases: not a word, a path or a JSON key."""
    if len(token) < MIN_SECRET_CHARS or any(c in token for c in ' \n"{}[]'):
        return False
    if token.endswith((":", ",")) or "/" in token and token.count("/") > 3:
        return False
    return any(c.isdigit() for c in token) or (token.lower() != token and token.upper() != token)


def secret_values(directory: Path | None = None) -> list[str]:
    """The exact secret strings in the owner's small files under ~/.secrets: a page holding any of
    them is refused, whatever shape the secret has. A JSON file contributes its string values (not
    its keys); a text file its whole one-line content and each token that looks like a secret."""
    root = (directory or SECRETS_DIR).expanduser()
    found: set[str] = set()
    try:
        for path in sorted(root.rglob("*"))[:200]:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                    continue
                text = path.read_text(errors="ignore").strip()
            except OSError:
                continue
            try:
                found.update(t for t in _json_strings(json.loads(text)) if _looks_secret(t))
                continue
            except ValueError:
                pass
            if "\n" not in text and _looks_secret(text):
                found.add(text)
            found.update(t for t in text.split() if _looks_secret(t))
    except OSError:
        pass
    return sorted(found)


def stage(apps_root: Path, app: str) -> tuple[Path, _Tally]:
    """Copy `<apps_root>/<app>` into a private temp directory without following any symlink."""
    root = apps_root.expanduser()
    try:
        src_fd = open_dir(root, app)
    except PathEscapeError:
        raise ValueError("the folder is not inside the apps folder") from None
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError(f"there is no app folder called {app}") from None
    tmp = Path(tempfile.mkdtemp(prefix="pages-stage-"))
    tally = _Tally()
    try:
        dst_fd = os.open(tmp, _DIR_FLAGS)
        try:
            _copy_tree(src_fd, dst_fd, "", 0, tally)
        finally:
            os.close(dst_fd)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    finally:
        os.close(src_fd)
    return tmp, tally


def problems(staged: Path, settings: Settings, secrets: Sequence[str] = ()) -> list[str]:
    """Why this staged folder may not be published (empty: it may)."""
    out: list[str] = []
    files = [p for p in sorted(staged.rglob("*")) if p.is_file()]
    if not (staged / "index.html").is_file():
        out.append("it has no index.html at its top")
    if len(files) > settings.max_files:
        out.append(f"it has {len(files)} files (the limit is {settings.max_files})")
    total = 0
    for path in files:
        rel = path.relative_to(staged).as_posix()
        size = path.stat().st_size
        total += size
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            out.append(f"{rel} is not an allowed file type")
            continue
        if size > settings.max_file_bytes:
            out.append(f"{rel} is larger than {settings.max_file_bytes // 1024} KB")
            continue
        data = path.read_bytes()
        text = data.decode("utf-8", errors="ignore") if path.suffix.lower() in TEXT_SUFFIXES else ""
        if any(s.encode() in data for s in secrets) or any(p.search(text) for p in SECRET_SHAPES):
            out.append(f"{rel} looks like it contains a secret")
    if total > settings.max_bytes:
        out.append(
            f"the folder is {total // 1024} KB (the limit is {settings.max_bytes // 1024} KB)"
        )
    return out


def _redact(text: str, token: str) -> str:
    return text.replace(token, "[token]") if token else text


def _git_env(home: Path, token: str, askpass: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": str(askpass),
        "PAGES_TOKEN": token,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_AUTHOR_NAME": "Voice Agent",
        "GIT_COMMITTER_NAME": "Voice Agent",
        "GIT_AUTHOR_EMAIL": "voice-agent@localhost",
        "GIT_COMMITTER_EMAIL": "voice-agent@localhost",
    }


def push_site(
    staged: Path,
    name: str,
    settings: Settings,
    token: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Put `staged` at `<name>/` on the repository's branch with an ordinary push; the new commit's
    id, or None when nothing changed."""
    repo = settings.repo.strip()
    match = GITHUB_REPO.fullmatch(repo)
    if match is not None:
        remote = f"https://x-access-token@github.com/{match.group(1)}/{match.group(2)}.git"
    elif settings.allow_local:
        remote = repo
    else:
        raise ValueError("the repository must be a https://github.com/<owner>/<repo> address")
    branch = settings.branch
    with tempfile.TemporaryDirectory(prefix="pages-push-") as scratch:
        base = Path(scratch)
        work = base / "work"
        work.mkdir()
        askpass = base / "askpass.sh"
        askpass.write_text('#!/bin/sh\nprintf "%s\\n" "$PAGES_TOKEN"\n')
        askpass.chmod(0o700)
        env = _git_env(base, token, askpass)

        def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
            done = run(
                ["git", "-c", "core.hooksPath=/dev/null", *args],
                cwd=work,
                env=env,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if check and done.returncode != 0:
                detail = _redact((done.stderr or done.stdout).strip(), token)[-300:]
                raise RuntimeError(f"git {args[0]} failed: {detail}")
            return done

        git("init", "-q")
        git("remote", "add", "origin", remote)
        if (
            git("ls-remote", "--exit-code", "--heads", "origin", branch, check=False).returncode
            == 0
        ):
            git("fetch", "-q", "--depth", "1", "origin", branch)
            git("checkout", "-q", "-b", branch, "FETCH_HEAD")
        else:
            git("checkout", "-q", "-b", branch)
        target = work / name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(staged, target)
        (work / ".nojekyll").touch()
        git("add", "-A")
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        git("commit", "-q", "-m", f"Publish {name}")
        git("push", "-q", "origin", f"HEAD:refs/heads/{branch}")  # never forced
        return git("rev-parse", "--short", "HEAD").stdout.strip()


def ensure_pages(settings: Settings, token: str, client: httpx.Client | None = None) -> str | None:
    """Turn Pages on for the repository when it is off (GitHub's API); a note when that fails."""
    match = GITHUB_REPO.fullmatch(settings.repo.strip())
    if match is None:
        return None
    owner, project = match.groups()
    url = f"https://api.github.com/repos/{owner}/{project}/pages"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    http = client or httpx.Client(timeout=10.0)
    try:
        if http.get(url, headers=headers).status_code == 200:
            return None
        made = http.post(
            url, headers=headers, json={"source": {"branch": settings.branch, "path": "/"}}
        )
        if made.status_code in (201, 409):  # 409: already on
            return None
        return "GitHub Pages is not switched on for the repository yet"
    except httpx.HTTPError:
        return "I could not check whether GitHub Pages is switched on"
    finally:
        if client is None:
            http.close()


def publish_app(
    app: str,
    dry_run: bool,
    settings: Settings,
    secrets: Sequence[str] | None = None,
    *,
    http: httpx.Client | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """The sentence to say: a dry run's summary, the published address, or why not."""
    name = app.strip()
    if not settings.repo.strip():
        return "Publishing is not set up yet: no repository is configured."
    if not BRANCH.fullmatch(settings.branch):
        return "I couldn't publish: the configured branch name is not valid."
    if not APP_NAME.fullmatch(name):
        return "I couldn't publish that: the app name must be lowercase letters, digits, - or _."
    try:
        staged, tally = stage(settings.apps_root, name)
    except (ValueError, OSError) as exc:
        return f"I couldn't publish {name}: {exc}."
    try:
        found = problems(staged, settings, secret_values() if secrets is None else secrets)
        if found:
            return f"I couldn't publish {name}: {'; '.join(found[:3])}."
        url = pages_url(settings.repo, name) or f"{settings.repo.rstrip('/')}/{name}/"
        if dry_run:
            kb = max(1, tally.bytes // 1024)
            return f"{READY} {name}: {tally.files} files, {kb} KB, public at {url}."
        try:
            token = settings.token_file.expanduser().read_text().strip()
        except OSError:
            return "I couldn't publish: the GitHub token file is missing."
        if not token and not settings.allow_local:
            return "I couldn't publish: the GitHub token file is empty."
        try:
            commit = push_site(staged, name, settings, token, run)
        except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            return f"I couldn't publish {name}: {_redact(str(exc), token)}"
        note = ensure_pages(settings, token, http) if commit else None
        said = f"Published {name}: {url}. It can take a minute to appear."
        if commit is None:
            said = f"{name} is already published and unchanged: {url}."
        return f"{said} {note}." if note else said
    finally:
        shutil.rmtree(staged, ignore_errors=True)


def build_server(settings: Settings | None = None) -> FastMCP:
    server = FastMCP("pages")

    @server.tool(
        description=(
            "Publish an app the delegate already made (its folder name, as open_app lists it) to "
            "GitHub Pages. The user is asked to confirm before anything is published; just say "
            "what it returns."
        )
    )
    async def publish_pages(app: str, dry_run: bool = True) -> str:
        cfg = settings or load_settings()
        return await asyncio.to_thread(publish_app, app, dry_run, cfg)

    return server


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    parser = argparse.ArgumentParser(prog="voice_agent.mcp_servers.pages")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_settings(args.config)
    build_server(cfg).run(show_banner=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
