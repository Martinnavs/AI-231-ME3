"""scripts/stack/pi_sync.sh in DRY mode against a throwaway git repo: what make pi-sync runs."""

from __future__ import annotations

import subprocess

import pytest
from test_stack_scripts import TOKEN, run_script, stack_env, world  # noqa: F401

GIT = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def repo(w) -> dict:  # type: ignore[no-untyped-def]
    """A git repo at STACK_REPO with the synced folders committed; returns the script's env."""
    env = stack_env(w)
    tmp = w.tmp
    for rel in (
        "src/a.py",
        "pipeline/src/b.py",
        "pipeline/config/c.txt",
        "config/d.json",
        "corpus/e.md",
        "scripts/f.sh",
    ):
        (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp / rel).write_text("x\n")
    (tmp / "uv.lock").write_text("lock\n")
    (tmp / ".gitignore").write_text("config/pi.env\nout/\nfake/\nbin/\nhome/\n")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-C", str(tmp), *cmd], env={**env, **GIT}, check=True)
    return {**env, "HARNESS": "pi"}


def sync(w, env: dict, *args: str):  # type: ignore[no-untyped-def]
    """The script's result, with the shell quoting (printf %q) taken out of stdout."""
    r = run_script("pi_sync.sh", env, *(args or ("sync",)))
    r.stdout = r.stdout.replace("\\", "")
    return r


def test_dry_sync_runs_every_step_in_order_and_never_prints_the_token(world):  # noqa: F811
    r = sync(world, repo(world))
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert TOKEN not in out + r.stderr
    for folder in ("src", "pipeline/src", "pipeline/config", "config", "corpus", "scripts"):
        assert f"169.254.7.7:/home/pi/repo/{folder}/" in out
    assert out.count("--delete") == 6
    assert (
        "--exclude=pi.env" in out
        and "--exclude=profile.md" in out
        and "--exclude=publish.toml" in out
    )
    assert "--exclude=pi_known_hosts" in out
    order = [
        out.index("rsync"),
        out.index("chmod +x"),
        out.index(".deployed"),
        out.index("tavily_mode"),
        out.index("systemctl --user restart pi-speaker pi-agent"),
    ]
    assert order == sorted(order)
    assert "echo demo" in out  # the default search mode


@pytest.mark.parametrize(
    ("mode", "expect"),
    [("benchmark", "echo benchmark"), ("demo", "echo demo"), ("auto", "rm -f")],
)
def test_the_search_mode_is_written_or_cleared_on_the_pi(world, mode, expect):  # noqa: F811
    r = sync(world, {**repo(world), "PI_SEARCH_MODE": mode})
    assert r.returncode == 0 and expect in r.stdout


def test_keep_leaves_the_search_mode_alone_and_a_bad_one_stops(world):  # noqa: F811
    env = repo(world)
    assert "tavily_mode" not in sync(world, {**env, "PI_SEARCH_MODE": "keep"}).stdout
    bad = sync(world, {**env, "PI_SEARCH_MODE": "loud"})
    assert bad.returncode != 0 and "search mode must be" in bad.stderr


def test_restart_can_be_limited_or_skipped(world):  # noqa: F811
    env = repo(world)
    only_agent = sync(world, {**env, "PI_RESTART": "agent"}).stdout
    assert "restart pi-agent" in only_agent and "pi-speaker pi-agent" not in only_agent
    none = sync(world, {**env, "PI_RESTART": "none"}).stdout
    assert "systemctl" not in none and "rsync" in none


def test_the_mode_subcommand_only_sets_the_mode(world):  # noqa: F811
    env = repo(world)
    r = sync(world, env, "mode", "benchmark")
    assert r.returncode == 0 and "echo benchmark" in r.stdout
    assert "rsync" not in r.stdout and "systemctl" not in r.stdout
    assert sync(world, env, "mode").returncode != 0


def test_uncommitted_edits_to_synced_files_are_refused_unless_forced(world):  # noqa: F811
    env = repo(world)
    (world.tmp / "src" / "a.py").write_text("edited\n")
    live = {**env, "DRY": "0"}
    r = sync(world, live)
    assert r.returncode != 0 and "uncommitted changes" in r.stderr
    assert "rsync" not in r.stdout  # nothing was sent
    forced = sync(world, {**env, "FORCE": "1"})  # DRY: prints the plan for the committed tree
    assert forced.returncode == 0


def test_an_unknown_ref_stops_before_anything_is_sent(world):  # noqa: F811
    r = sync(world, {**repo(world), "PI_REF": "no-such-ref"})
    assert r.returncode != 0 and "unknown git ref" in r.stderr and "rsync" not in r.stdout


def test_the_synced_tree_is_the_commit_not_the_working_directory(world):  # noqa: F811
    env = repo(world)
    (world.tmp / "src" / "untracked.py").write_text("secret\n")
    # the plan syncs a temp export of HEAD: the untracked file is never in it
    r = sync(world, env)
    assert r.returncode == 0
    export = [line for line in r.stdout.splitlines() if "rsync" in line and "/src/" in line][0]
    assert str(world.tmp / "src") not in export  # the source is the temp export, not the repo


def test_the_pi_harness_is_the_default_for_make_up(world):  # noqa: F811
    env = stack_env(world)
    env.pop("HARNESS")
    r = run_script("up.sh", env, "laptop")
    assert r.returncode == 0, r.stderr
    assert "(wait for 169.254.7.7:8781)" in r.stdout  # the agent is waited for on the Pi
    laptop = run_script("up.sh", {**env, "HARNESS": "laptop"}, "laptop")
    assert "(wait for 169.254.7.7:8781)" not in laptop.stdout


def test_make_up_starts_the_pi_services_as_systemd_units_by_default(world):  # noqa: F811
    """Port 8780 was 'already in use': the boot service already runs the speaker."""
    env = stack_env(world)
    env.pop("HARNESS")
    r = run_script("up.sh", env, "all")
    assert r.returncode == 0, r.stderr
    out = r.stdout.replace("\\", "")
    assert "systemctl --user restart pi-speaker pi-agent" in out
    assert "pi_speaker.py" not in out  # no second copy over ssh


def test_the_laptop_harness_still_starts_the_speaker_over_ssh(world):  # noqa: F811
    r = run_script("up.sh", stack_env(world), "all")  # stack_env pins HARNESS=laptop
    assert "pi_speaker.py" in r.stdout and "systemctl --user restart" not in r.stdout.replace(
        "\\", ""
    )


def test_make_down_stops_the_units_the_same_way(world):  # noqa: F811
    env = stack_env(world)
    env.pop("HARNESS")
    r = run_script("down.sh", env, "pi")
    assert "systemctl --user stop pi-agent pi-speaker" in r.stdout.replace("\\", "")


def test_make_talk_says_to_run_make_up_when_the_stack_is_down(world):  # noqa: F811
    env = stack_env(world, HARNESS="laptop")
    env["DRY"] = "0"  # nothing listens on the fake cable address or on loopback
    env["PORT_SPEECH"] = "1"  # a closed local port, answered at once
    env["LAPTOP_IP"] = "127.0.0.1"
    world.pi_env.write_text(
        world.pi_env.read_text().replace("LAPTOP_IP=169.254.1.1", "LAPTOP_IP=127.0.0.1")
    )
    r = run_script("talk.sh", env)
    assert r.returncode != 0
    assert "run make up first" in r.stderr and "speech server is not running" in r.stderr
