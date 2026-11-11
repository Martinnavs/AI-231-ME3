"""What can the delegate sandbox do with git, GitHub and Hugging Face? Checked in the real sandbox.

    make doctor-tools                                   # laptop
    .venv/bin/python scripts/delegate_doctor.py --offline   # skip the two network checks

On the Pi: run the same script there, in the Pi's checkout and venv (the Pi is the owner's call;
this script never touches it). It uses the delegate's policy (config/delegate.toml [paths]) and the
`bash` tool's sandbox path, so what it reports is what the delegate's shell gets. No secret file is
opened or listed; ~/.secrets/github and ~/.secrets/huggingface are only tested for existence.

Optional: the owner profile (docs/PROFILE.md). It reports whether config/profile.md is filled in
(never its contents) and whether the delegate can read the copy the agent server installs at
/sandbox/scratch/PROFILE.md. Without a profile "make me a page about me" still works (the agent asks
your name and searches the web), so a missing profile is a warning, never a failure.

Exit 0 when the required checks pass (sandbox, git, local commit, and the GitHub read unless
--offline); gh, hf, Hugging Face reachability, credentials and the profile are warnings only.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
import tomllib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from voice_agent.mcp_servers import code  # noqa: E402
from voice_agent.mcp_servers.shell_policy import check_command  # noqa: E402
from voice_agent.sandbox.runner import SandboxResult, preflight, run_in_sandbox  # noqa: E402

Runner = Callable[[str], Awaitable[SandboxResult]]
OK, MISSING, BLOCKED = "OK", "MISSING", "BLOCKED"
SECRET_WORDS = ("TOKEN", "KEY", "SECRET")
COMMIT_SCRIPT = (
    'd=$(mktemp -d "$PWD/.doctor-XXXXXX") && trap \'rm -rf "$d"\' EXIT && cd "$d" && '
    "git init -q && "
    "echo x > f && git add f && git commit -qm doctor && git log --oneline | wc -l"
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    reason: str
    required: bool = False


PROFILE_IN_SANDBOX = "/sandbox/scratch/PROFILE.md"
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_EMPTY_FIELD = re.compile(r"^[-*]?\s*[^:]{1,60}:\s*$")


def profile_state(path: Path) -> tuple[str, int]:
    """("missing" | "template" | "filled", size in bytes). Mirrors
    voice_pipeline.scratchpad.profile_is_filled (the pipeline venv cannot import from here)."""
    try:
        text = path.read_text()
    except OSError:
        return "missing", 0
    for line in _COMMENT.sub("", text).splitlines():
        line = line.strip()
        empty = not line or line.startswith("#") or line in ("-", "*") or _EMPTY_FIELD.match(line)
        if not empty:
            return "filled", len(text.encode())
    return "template", len(text.encode())


@dataclass
class Probe:
    """Everything the checks touch, so tests can fake it."""

    run: Runner
    preflight_problems: Sequence[str]
    env_names: Sequence[str]
    host_has: Callable[[str], bool]
    dir_exists: Callable[[str], bool]
    ssh_refusal: str | None
    profile: Callable[[], tuple[str, int]] = lambda: ("missing", 0)


async def _sh(run: Runner, command: str) -> tuple[SandboxResult | None, str]:
    try:
        return await run(command), ""
    except Exception as exc:  # noqa: BLE001  the sandbox itself may be unavailable
        return None, f"{type(exc).__name__}: {exc}"


def _line(res: SandboxResult | None) -> str:
    text = (res.stdout.strip() or res.stderr.strip()) if res else ""
    return text.splitlines()[-1] if text else ""


async def _profile_checks(probe: Probe, sandbox_ok: bool) -> list[Check]:
    """The optional owner profile: filled in on the host, and readable by the delegate."""
    state, size = probe.profile()
    if state == "missing":
        why = "no config/profile.md: copy config/profile.example.md to it and fill it in"
        return [Check("owner profile", MISSING, why + " (docs/PROFILE.md)")]
    if state == "template":
        why = "config/profile.md has no value filled in yet (docs/PROFILE.md)"
        return [Check("owner profile", MISSING, why)]
    checks = [Check("owner profile", OK, f"filled in ({size} bytes; its contents are never shown)")]
    if not sandbox_ok:
        return checks + [Check("profile in sandbox", BLOCKED, "the sandbox is not working")]
    res, err = await _sh(probe.run, f"wc -c < {PROFILE_IN_SANDBOX}")
    seen = res.stdout.strip() if res is not None and res.returncode == 0 else ""
    if not seen.isdigit():
        why = (
            err
            or "not in the sandbox yet: restart the agent server (it copies the profile at start)"
        )
        return checks + [Check("profile in sandbox", MISSING, why)]
    if int(seen) != size:
        why = f"out of date ({seen} bytes there, {size} in config/profile.md): restart the agent"
        return checks + [Check("profile in sandbox", MISSING, why)]
    return checks + [
        Check("profile in sandbox", OK, f"the delegate reads it at {PROFILE_IN_SANDBOX}")
    ]


async def run_checks(probe: Probe, *, offline: bool) -> list[Check]:
    checks: list[Check] = []
    if probe.preflight_problems:
        checks.append(
            Check("sandbox preflight", BLOCKED, "; ".join(probe.preflight_problems), True)
        )
        return checks + _static_checks(probe) + await _profile_checks(probe, False)
    checks.append(Check("sandbox preflight", OK, "bwrap, systemd-run, memory limit present", True))

    res, err = await _sh(probe.run, "command -v git")
    if res is None or res.returncode != 0:
        checks.append(Check("git on sandbox PATH", MISSING, err or "not found", True))
        checks.append(Check("git local commit", BLOCKED, "needs git", True))
    else:
        checks.append(Check("git on sandbox PATH", OK, _line(res), True))
        res, err = await _sh(probe.run, COMMIT_SCRIPT)
        if res is not None and res.returncode == 0 and res.stdout.strip() == "1":
            checks.append(
                Check("git local commit", OK, "init + commit worked (identity set)", True)
            )
        else:
            why = err or _line(res) or "commit failed"
            checks.append(Check("git local commit", BLOCKED, why, True))

    if offline:
        checks.append(Check("GitHub https read", BLOCKED, "skipped (--offline)", False))
        checks.append(Check("Hugging Face reachable", BLOCKED, "skipped (--offline)", False))
    else:
        res, err = await _sh(
            probe.run,
            "git ls-remote https://github.com/octocat/Hello-World.git HEAD",
        )
        ok = res is not None and res.returncode == 0 and "HEAD" in res.stdout
        checks.append(
            Check(
                "GitHub https read",
                OK if ok else BLOCKED,
                "anonymous ls-remote of a public repo worked"
                if ok
                else err or _line(res) or "failed",
                True,
            )
        )
        res, err = await _sh(
            probe.run,
            "curl -s -o /dev/null -w '%{http_code}' --max-time 15 "
            "'https://huggingface.co/api/models?limit=1'",
        )
        code_ = res.stdout.strip() if res else ""
        checks.append(
            Check(
                "Hugging Face reachable",
                OK if code_ == "200" else BLOCKED,
                "api/models answered 200" if code_ == "200" else err or f"HTTP {code_ or 'none'}",
            )
        )

    for tool in ("gh", "hf"):
        res, err = await _sh(probe.run, f"command -v {tool}")
        if res is not None and res.returncode == 0:
            checks.append(Check(f"{tool} on sandbox PATH", OK, _line(res)))
            continue
        hint = ""
        if tool == "hf" and probe.host_has("hf"):
            hint = " (exists on the host PATH or ~/.local/bin, but is not visible in the sandbox)"
        checks.append(Check(f"{tool} on sandbox PATH", MISSING, "not found" + hint))
    return checks + _static_checks(probe) + await _profile_checks(probe, True)


def _static_checks(probe: Probe) -> list[Check]:
    out = [
        Check(
            "ssh blocked by policy",
            OK if probe.ssh_refusal else MISSING,
            "shell_policy refuses ssh" if probe.ssh_refusal else "ssh is NOT blocked",
        )
    ]
    for name in ("github", "huggingface"):
        present = probe.dir_exists(f"~/.secrets/{name}")
        out.append(
            Check(
                f"credentials folder {name}",
                OK if present else MISSING,
                "exists (never read or passed in)" if present else "none, so no login is possible",
            )
        )
    leaky = [n for n in probe.env_names if any(w in n.upper() for w in SECRET_WORDS)]
    out.append(
        Check(
            "no secret env in sandbox",
            MISSING if leaky else OK,
            f"secret-looking names: {', '.join(leaky)}" if leaky else "no TOKEN/KEY/SECRET names",
        )
    )
    return out


def exit_code(checks: Sequence[Check]) -> int:
    return 0 if all(c.status == OK for c in checks if c.required) else 1


def format_report(checks: Sequence[Check]) -> str:
    lines = [
        f"{c.status:<8} {c.name}: {c.reason}" + ("" if c.required else "  [optional]")
        for c in checks
    ]
    by = {c.name: c.status == OK for c in checks}
    can = ["run shell commands"] if by.get("sandbox preflight") else []
    if by.get("git local commit"):
        can.append("commit locally in git")
    if by.get("GitHub https read"):
        can.append("clone and read public GitHub repos over https")
    if by.get("Hugging Face reachable"):
        can.append("fetch public Hugging Face URLs with curl")
    if by.get("profile in sandbox"):
        can.append("build a page about you from your profile")
    cannot = ["push, log in, or use private repos (no credentials, no gh, no ssh)"]
    if not by.get("hf on sandbox PATH"):
        cannot.append("use the hf command")
    lines.append("Voice agent can: " + (", ".join(can) or "nothing (sandbox not working)"))
    lines.append("Voice agent cannot: " + "; ".join(cannot))
    return "\n".join(lines)


def real_probe() -> Probe:
    cfg = tomllib.loads((ROOT / "config" / "delegate.toml").read_text())["paths"]
    workspace = Path(cfg.get("workspace", "~/agent_sandbox/workspace")).expanduser()
    code._state.root = workspace
    code._state.network = bool(cfg.get("network", False))
    pylibs = cfg.get("pylibs")
    code._state.pylibs = Path(pylibs).expanduser() if pylibs else None
    policy = code._policy(30, host_readonly=True)

    async def run(command: str) -> SandboxResult:
        return await run_in_sandbox(policy, ["bash", "-c", command])

    problems = list(preflight().problems)
    if not workspace.is_dir():
        problems.append(f"workspace {workspace} does not exist")
    return Probe(
        run=run,
        preflight_problems=problems,
        env_names=[k for k, _ in policy.env],
        host_has=lambda n: bool(shutil.which(n) or (Path.home() / ".local/bin" / n).exists()),
        dir_exists=lambda p: os.path.isdir(os.path.expanduser(p)),
        ssh_refusal=check_command("ssh x"),
        profile=lambda: profile_state(ROOT / "config" / "profile.md"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--offline", action="store_true", help="skip the two network checks")
    args = parser.parse_args(argv)
    checks = asyncio.run(run_checks(real_probe(), offline=args.offline))
    print(format_report(checks))
    return exit_code(checks)


if __name__ == "__main__":
    sys.exit(main())
