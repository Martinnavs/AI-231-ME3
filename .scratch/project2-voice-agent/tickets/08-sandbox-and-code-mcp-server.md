# 08: Sandbox runner (systemd-run + bwrap) and code MCP server

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 06 (reuses `_paths.resolve_inside` and `docs/MCP-SERVERS.md`)
**Status:** ready-for-agent

## Files
- `src/voice_agent/sandbox/__init__.py`, `src/voice_agent/sandbox/runner.py`
- `src/voice_agent/mcp_servers/code.py`
- `docs/MCP-SERVERS.md` (append "code" section + sandbox model)
- `tests/sandbox/test_command.py`, `tests/sandbox/test_run.py` (integration, `pi`/bwrap-gated), `tests/mcp_servers/test_code_server.py`

## Action
Review §7: the coding subagent's scripts run under `systemd-run --user --scope` (memory/CPU limits) + `timeout` + `bwrap` (filesystem, network and PID isolation), bound to one folder. Start from the review's command, but **build the bind list from what exists on the host** — Debian 13 arm64 has a merged `/usr` and usually no `/lib64`, and binding a missing path makes bwrap exit. Command construction is a pure function (fully unit-testable on any host); execution and preflight are separate.

Then a code MCP server giving the subagent five tools over the sandbox folder: write, read, list, run, and **publish a site**. The professor will give one random complex task during evaluation ("it could be building a website"), so the coding subagent must be able to show its result: `publish_site(subdir, name)` copies a sandbox folder (regular files only, no symlinks, ≤ 200 files and 20 MiB) into `paths.sites_root/<name>/`, which a separate read-only static server (ticket 16's `voice-agent-sites.service`) serves on the LAN. The tool returns the URL for the result text; the voice agent says where to look, not the URL itself. Network stays **off** for code execution (D3: research uses the server-side web search tool instead).

The dev host has **no bwrap** and may lack a user systemd session: integration tests must skip cleanly there. They run on the Pi (human checkpoint 5).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| `/lib64` (or `/bin`, `/lib`, `/sbin`) missing or a symlink | build `--symlink usr/<x> /<x>` when it's a symlink into usr; skip when absent; bind when a real dir |
| `bwrap` not installed | `preflight()` reports it; `run()` raises `SandboxUnavailable`; code server's `run_python` returns tool error "sandbox unavailable: install bubblewrap" |
| memory cgroup controller absent | `preflight().memory_controller=False`; `run()` refuses unless `allow_no_memory_limit=True` (default False) — message points at `cgroup_enable=memory` in `/boot/firmware/cmdline.txt` |
| no user systemd session (`systemd-run --user` fails) | `SandboxUnavailable` with stderr excerpt |
| script exceeds `timeout_s` | killed; `timed_out=True`, returncode 124 |
| script exceeds `MemoryMax` | killed by cgroup OOM; `oom_killed=True` when detectable (exit 137 / systemd result), else returncode reported as-is |
| stdout/stderr > 64 KiB | truncated with a `…[truncated N bytes]` marker |
| script tries network | fails (`--unshare-all`); covered by integration test |
| script tries to read `$HOME` or write outside `/sandbox` | fails (not bound / read-only); covered by integration test |
| path args to code tools escape the sandbox root | `PathEscapeError` → tool error (reuse `resolve_inside`) |
| file to write > 256 KiB | tool error |
| concurrent `run_python` calls | serialized by an `asyncio.Lock` (one sandboxed job at a time on a Pi) |
| `publish_site` source contains a symlink, device file or `..` path | tool error naming the entry; nothing copied |
| `publish_site` over the file-count or size cap | tool error with the counts; nothing copied |
| `publish_site` name invalid (`../x`, uppercase, too long) | tool error; nothing copied |
| `publish_site` when `sites_root` is missing | created with mode 0755; the server never writes anywhere else |

### C — Contracts
```python
# src/voice_agent/sandbox/runner.py
@dataclass(frozen=True)
class SandboxPolicy:
    root: Path                         # host folder bound at /sandbox (rw)
    memory_max: str = "512M"
    cpu_quota: str = "100%"
    timeout_s: int = 60
    network: bool = False              # True adds --share-net (unused in plan 1)
    ro_binds: tuple[Path, ...] = ()    # e.g. a read-only venv
    allow_no_memory_limit: bool = False

@dataclass(frozen=True)
class HostLayout:
    usr_merged: dict[str, bool]        # {"/bin": True, "/lib": True, "/sbin": True}
    has_lib64: bool
    has_etc_alternatives: bool

@dataclass(frozen=True)
class Preflight:
    bwrap: Path | None
    systemd_run: Path | None
    memory_controller: bool
    layout: HostLayout
    problems: list[str]                # human-readable, empty when ready

def detect_layout(root: Path = Path("/")) -> HostLayout: ...
def preflight(root: Path = Path("/")) -> Preflight: ...
def build_command(policy: SandboxPolicy, argv: Sequence[str], layout: HostLayout) -> list[str]: ...   # pure

@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    oom_killed: bool
    duration_s: float

class SandboxUnavailable(RuntimeError): ...
async def run_in_sandbox(policy: SandboxPolicy, argv: Sequence[str]) -> SandboxResult: ...

# src/voice_agent/mcp_servers/code.py   (run: python -m voice_agent.mcp_servers.code [--root DIR])
mcp = FastMCP("code")
@mcp.tool()
async def write_file(path: str, content: str) -> dict[str, object]: ...        # {"path","bytes"}
@mcp.tool()
async def read_file(path: str) -> str: ...
@mcp.tool()
async def list_files(path: str = ".") -> list[str]: ...
@mcp.tool()
async def run_python(path: str, args: list[str] | None = None, timeout_s: int = 60) -> dict[str, object]: ...
    # {"returncode","stdout","stderr","timed_out","oom_killed","duration_s"}; timeout_s capped at 120
@mcp.tool()
async def publish_site(subdir: str, name: str) -> dict[str, object]: ...
    # {"url", "files", "bytes"}; name matches ^[a-z0-9][a-z0-9-]{0,39}$; replaces an existing site of that name atomically
    # (copy to a temp dir under sites_root, then rename); url = f"http://{sites_host}:{sites_port}/{name}/" (AppConfig)
```
`build_command` output for the review's layout must equal (as a list) the review §7 command with the `/lib64` handling above; argv order: `systemd-run --user --scope --quiet -p MemoryMax=… -p CPUQuota=… timeout <s> bwrap … --chdir /sandbox --unshare-all --die-with-parent --new-session <argv>`.

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `systemd-run --user --scope` accepts `-p MemoryMax=` and `-p CPUQuota=` and `--quiet` | `systemd-run --help` and `man systemd.resource-control` (WebFetch if no man page) |
| A2 | Memory controller availability is visible in `/sys/fs/cgroup/user.slice/cgroup.controllers` (cgroup v2) | review §7; `detect` reads that file under `root` |
| A3 | `timeout` exits 124 on timeout | `timeout 0.1 sleep 1; echo $?` on dev host |
| A4 | bwrap flags `--ro-bind --symlink --proc --dev --tmpfs --bind --chdir --unshare-all --die-with-parent --new-session --share-net` exist in bwrap ≥ 0.4 | WebFetch bubblewrap man page |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_build_command_debian13_arm64` — fake root with merged usr, no `/lib64` → golden argv (no `/lib64` bind, `--symlink usr/lib /lib`) | `uv run pytest -q tests/sandbox/test_command.py` |
| P2 | `test_build_command_with_lib64`, `test_network_flag_adds_share_net`, `test_ro_binds` | same |
| P3 | `test_preflight_reports_missing_bwrap_and_memory` — fake root | same |
| P4 | `test_output_truncation` | same |
| P7 | `test_publish_site` — tmp sandbox with `index.html` + `css/a.css` → copied to tmp `sites_root/demo/`, url uses config host/port; symlink inside → error and `sites_root` unchanged; republish replaces atomically (no mix of old and new files); bad names rejected | `uv run pytest -q tests/mcp_servers/test_code_server.py` |
| P5 | `test_code_server_paths_and_size_limits` — escapes and 257 KiB write rejected; write/read/list roundtrip; `run_python` returns "sandbox unavailable" tool error when bwrap absent | `uv run pytest -q tests/mcp_servers/test_code_server.py` |
| P6 | Integration (`pi` marker, also skip if `preflight().problems`): `test_no_network` (socket connect to 1.1.1.1:53 fails), `test_cannot_read_home`, `test_cannot_write_outside`, `test_timeout_kills` (`timed_out`, rc 124), `test_memory_limit_kills` (allocate 1 GiB with `memory_max="128M"`) | `VOICE_AGENT_PI=1 uv run pytest -q tests/sandbox/test_run.py` — **written now, run by the human on the Pi** |

## Unknowns
- How systemd reports the OOM kill for a `--scope` unit on the Pi; implement `oom_killed` best-effort (exit 137 or SIGKILL) and let P6 confirm on hardware.

## Dependencies/license
None new.

## Acceptance Criteria
- [ ] Contracts implemented; sandbox model + Pi prerequisites documented in `docs/MCP-SERVERS.md`
- [ ] P1–P5 green on the dev host; P6 written, skipped here with a clear reason
- [ ] Gate suite green

## Non-Goals
- No container runtimes (Docker/podman); no seccomp profile in plan 1
- No package installation inside the sandbox

## Security-sensitive
yes — process isolation for LLM-written code. Flag for `security-auditor`.

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/MCP-SERVERS.md`; `voice_agent.mcp_servers._paths.resolve_inside` (ticket 06)

## Execution Log
