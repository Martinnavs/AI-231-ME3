"""The git identity in the sandbox env, the prompt sentence, the doctor."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from voice_agent.agent.subagent import SYSTEM
from voice_agent.mcp_servers import code
from voice_agent.sandbox.runner import SandboxResult

_spec = importlib.util.spec_from_file_location(
    "delegate_doctor", Path(__file__).resolve().parents[2] / "scripts" / "delegate_doctor.py"
)
assert _spec and _spec.loader
doctor = importlib.util.module_from_spec(_spec)
sys.modules["delegate_doctor"] = doctor
_spec.loader.exec_module(doctor)


def test_git_identity_is_always_in_the_sandbox_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(code._state, "pylibs", None)
    plain = dict(code._extras()["env"])
    libs = tmp_path / "libs"
    libs.mkdir()
    monkeypatch.setattr(code._state, "pylibs", libs)
    with_libs = dict(code._extras()["env"])
    for env in (plain, with_libs):
        assert env["GIT_AUTHOR_NAME"] == env["GIT_COMMITTER_NAME"] == "Voice Agent"
        assert env["GIT_AUTHOR_EMAIL"] == env["GIT_COMMITTER_EMAIL"] == "voice-agent@localhost"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "PYTHONPATH" not in plain and with_libs["PYTHONPATH"] == str(libs)


def test_system_prompt_states_the_git_limits() -> None:
    assert "no gh, hf, ssh or login" in SYSTEM and "cannot push" in SYSTEM


def result(rc: int = 0, out: str = "", err: str = "") -> SandboxResult:
    return SandboxResult(rc, out, err, False, False, 0.1)


def probe(replies: Mapping[str, SandboxResult | Exception], **kw: Any) -> Any:
    calls: list[str] = []

    async def run(command: str) -> SandboxResult:
        calls.append(command)
        for key, val in replies.items():
            if key in command:
                if isinstance(val, Exception):
                    raise val
                return val
        return result(1, "", "not found")

    base = {
        "run": run,
        "preflight_problems": [],
        "env_names": ["GIT_AUTHOR_NAME", "PATH"],
        "host_has": lambda n: n == "hf",
        "dir_exists": lambda p: False,
        "ssh_refusal": "refused",
    }
    p = doctor.Probe(**{**base, **kw})
    p.calls = calls
    return p


GOOD: dict[str, SandboxResult | Exception] = {
    "command -v git": result(0, "/usr/bin/git"),
    "git init": result(0, "1\n"),
    "ls-remote": result(0, "abc\tHEAD\n"),
    "curl": result(0, "200"),
}


async def test_all_good_exits_zero_and_warns_about_optionals() -> None:
    checks = await doctor.run_checks(probe(dict(GOOD)), offline=False)
    by = {c.name: c for c in checks}
    assert doctor.exit_code(checks) == 0
    assert (
        by["gh on sandbox PATH"].status == doctor.MISSING and not by["gh on sandbox PATH"].required
    )
    assert "host" in by["hf on sandbox PATH"].reason
    assert by["ssh blocked by policy"].status == doctor.OK
    assert by["no secret env in sandbox"].status == doctor.OK
    text = doctor.format_report(checks)
    assert "Voice agent can:" in text and "Voice agent cannot:" in text


async def test_missing_git_is_required_and_fails() -> None:
    replies = {**GOOD, "command -v git": result(1)}
    checks = await doctor.run_checks(probe(replies), offline=False)
    assert doctor.exit_code(checks) == 1
    assert {c.name: c.status for c in checks}["git local commit"] == doctor.BLOCKED


async def test_failed_commit_fails() -> None:
    replies = {**GOOD, "git init": result(128, "", "Author identity unknown")}
    checks = await doctor.run_checks(probe(replies), offline=False)
    assert doctor.exit_code(checks) == 1
    assert "Author identity unknown" in {c.name: c for c in checks}["git local commit"].reason


async def test_github_failure_fails_online_but_not_offline() -> None:
    replies = {**GOOD, "ls-remote": result(128, "", "could not resolve host")}
    p = probe(replies)
    assert doctor.exit_code(await doctor.run_checks(p, offline=False)) == 1
    p = probe(replies)
    checks = await doctor.run_checks(p, offline=True)
    assert doctor.exit_code(checks) == 0
    assert not any("ls-remote" in c or "curl" in c for c in p.calls)


async def test_hugging_face_failure_is_only_a_warning() -> None:
    replies = {**GOOD, "curl": result(0, "000")}
    checks = await doctor.run_checks(probe(replies), offline=False)
    assert doctor.exit_code(checks) == 0
    assert {c.name: c.status for c in checks}["Hugging Face reachable"] == doctor.BLOCKED


async def test_preflight_problem_fails_and_runs_nothing() -> None:
    p = probe(dict(GOOD), preflight_problems=["bwrap not found: install bubblewrap"])
    checks = await doctor.run_checks(p, offline=False)
    assert doctor.exit_code(checks) == 1 and p.calls == []


async def test_sandbox_exception_is_reported_not_raised() -> None:
    replies = {**GOOD, "command -v git": RuntimeError("boom")}
    checks = await doctor.run_checks(probe(replies), offline=False)
    assert doctor.exit_code(checks) == 1


async def test_secret_looking_env_and_unblocked_ssh_are_flagged() -> None:
    p = probe(dict(GOOD), env_names=["GITHUB_TOKEN"], ssh_refusal=None, dir_exists=lambda p: True)
    by = {c.name: c for c in await doctor.run_checks(p, offline=False)}
    assert by["no secret env in sandbox"].status == doctor.MISSING
    assert by["ssh blocked by policy"].status == doctor.MISSING
    assert by["credentials folder github"].status == doctor.OK
