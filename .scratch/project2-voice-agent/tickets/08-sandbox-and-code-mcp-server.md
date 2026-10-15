# 08: Sandbox runner (systemd-run + bwrap) and code MCP server

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 06 (reuses `_paths.resolve_inside` and `docs/MCP-SERVERS.md`)
**Status:** done

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

### 2026-10-15 developer: DONE
- Assumptions verified: A1 `systemd-run --help` → lists `--scope`, `-q/--quiet`, `-p/--property` (MemoryMax/CPUQuota are standard resource-control properties, not run here); A2 `cat /sys/fs/cgroup/user.slice/cgroup.controllers` → `cpuset cpu io memory hugetlb pids rdma misc` (file exists, `memory` readable the same way `preflight` parses it); A3 `timeout 0.1 sleep 1; echo $?` → 124; A4 fetched bubblewrap `bwrap.xml` from GitHub, grep found all of --ro-bind --symlink --proc --dev --tmpfs --bind --chdir --unshare-all --die-with-parent --new-session --share-net.
- Proof: P1 ✅ P2 ✅ P3 ✅ P4 ✅ (`uv run pytest -q tests/sandbox/test_command.py`); P5 ✅ P7 ✅ (`uv run pytest -q tests/mcp_servers/test_code_server.py`); P6 written, 5 tests skipped (marker `pi` + no bwrap on dev host). Combined: 29 passed, 5 skipped.
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (119 passed, 5 skipped)
- Files: src/voice_agent/sandbox/__init__.py, src/voice_agent/sandbox/runner.py, src/voice_agent/mcp_servers/code.py, docs/MCP-SERVERS.md (appended "code" section), tests/sandbox/test_command.py, tests/sandbox/test_run.py, tests/mcp_servers/test_code_server.py
- Deviations: (1) subprocess env is scrubbed to PATH/LANG/LC_ALL/XDG_RUNTIME_DIR/DBUS_SESSION_BUS_ADDRESS + HOME=/sandbox so API keys never reach scripts (not in the review command; build_command itself matches it). (2) `HostLayout.usr_merged` also carries "/lib64": True when it is a symlink into usr; `has_lib64` is true only for a real dir. (3) Extra tests beyond P1-P7 drive `run_in_sandbox` with a faked `build_command` (sh -c) to cover timeout/oom/systemd-failure/memory-refusal/env scrub/wall-clock kill without bwrap. (4) Tool path-escape message is "path outside sandbox root". (5) systemd-run failure is detected by stderr prefix ("Failed to connect to", "Failed to create bus", "Failed to start transient") with nonzero exit.
- Commit: see git log (`08: Sandbox runner (systemd-run + bwrap) and code MCP server`)
- Notes for reviewer: real bwrap isolation is unverified on this host (no bwrap); P6 must run on the Pi. publish_site replace is two renames (old aside, new in) so there is a brief window with no site, never a mix. `oom_killed` is best effort (137/SIGKILL). Security-auditor should look at the env scrub, the symlink/special-file rejection, and resolve_inside use on read/write.

### 2026-10-15 security-auditor: findings (static scan + local repro, main @ 04fff80)
- **H1 (R1)** `publish_site` scan-then-copy race: sandboxed code swaps a file/dir for a symlink between `_scan_site` (lstat) and the copy; `chmod` follows the link, so a host secret is published and made 0644 (reproduced). **H2 (R1)** `write_file`/`read_file` use `resolve_inside` then path-based I/O: same race class as 06 R2-1 (host write via symlinked parent, key read via swapped symlink, FIFO hang). Tool calls run concurrently; `_state.lock` covers only `run_python`.
- **M1** `media_root` sits inside `sandbox_root`; sandboxed code can replace `media/` with a symlink and redirect the speech server's confinement. **M2** no `TasksMax`, no `MemorySwapMax=0`, no disk cap, no dir count/depth cap in `publish_site`. **M3** controller check reads `user.slice` not `user@UID.service`. **M4** `publish_site(".")` publishes the whole sandbox root.
- Low: L1 host env reaches sandbox; L2 `network=True` also exposes host loopback/abstract sockets (document); L3 sandbox can fake `timed_out`/`oom_killed`/"unavailable"; L4 `list_files` reveals host paths and is uncapped; L5 partial site exposed during publish; L6 failed republish can delete the old site; L7 concurrent publish of one name not serialized; L8 static server hardening (ticket 16); L9 `--disable-userns`/`--cap-drop ALL`.
- Unverified (bwrap not run): bwrap starts on the Pi, no network/no $HOME/writes denied, MemoryMax/CPUQuota enforced on a user scope, 137/124 mapping, descendants die with the run.

### 2026-10-15 claude (tech-lead role): FIX-REQUEST to developer (R1/R2 loop, attempt 1 of 2)
Touch only `src/voice_agent/mcp_servers/code.py`, `src/voice_agent/sandbox/runner.py`, `src/voice_agent/mcp_servers/_paths.py`, and tests under `tests/sandbox/` and `tests/mcp_servers/`. Do not change `config.py`.
1. **H1/H2:** use the hardened helpers already in `_paths.py` from ticket 06's fix (`open_regular_for_read`, `write_file_atomic`; read them first). `publish_site` must walk with directory fds (`O_DIRECTORY|O_NOFOLLOW`, `os.scandir(fd)`), open each file `O_NOFOLLOW`, `fstat` as a regular file, copy from that fd, re-check size while copying, and set mode with `os.fchmod` on the fd, never by path. `read_file`/`write_file`/`list_files`/`publish_site` all take `_state.lock` (and so does `run_python`), so tools no longer interleave.
2. **M1 (without touching config):** in the bwrap argv, when `media_root` is inside `sandbox_root`, mask it from the sandbox (`--tmpfs` over its mount point) so sandboxed code cannot replace or read it; and make `_paths` refuse a root whose own last component is a symlink.
3. **M2:** add `-p TasksMax=64` and `-p MemorySwapMax=0` to the systemd-run argv; add a post-run size check on the sandbox dir (cap from the ticket's policy, add a field with a safe default) returning a clear error; cap directory count and depth in `publish_site`.
4. **M3:** check delegated controllers via `user@<UID>.service/cgroup.subtree_control` (cgroup v2), fall back to the existing check only if that file is absent; unit-test both with a fake `root`.
5. **M4:** reject `publish_site` of the sandbox root, anything overlapping `media_root`, and skip or reject dot-entries.
6. **L1:** `--clearenv` plus explicit `--setenv PATH /usr/bin:/bin`, `HOME /sandbox`, `LANG C.UTF-8`. **L3:** derive `timed_out`/`oom_killed`/unavailable from a runner-owned channel (systemd-run's own exit/ stderr preface or a runner-side timer) rather than only the child's exit code or stderr text it controls; add a test where the child exits 124/137 or prints the failure string. **L5:** build the site in a dir that is not world-readable until complete. **L6:** a failed republish must leave the old site intact (swap via rename of old to a backup name, restore on failure). **L7:** serialize per site name (covered by the lock).
7. Tests that really run, each shown failing on the old code first: publish scan/copy race (swap file for symlink during copy via an injected hook), `write_file` parent-swap race, `read_file` symlink and FIFO swap, `publish_site(".")` rejected, root-is-symlink refused, new argv flags present, env scrubbed, forged exit codes not trusted, failed republish keeps old site.
Non-goals: no `config.py` default change (media_root location is parked for the human), no seccomp, no new packages. Record anything bwrap-dependent as unverified; do not set VOICE_AGENT_PI.

### 2026-10-15 developer: DONE (fix-request attempt 1: H1, H2, M1-M4, L1, L3, L5-L7)
- Tests first: written before the fix, run against old code: 21 failed / 26 passed / 5 skipped (`uv run pytest -q tests/sandbox tests/mcp_servers/test_code_server.py`). Failing on old: build_command golden argv, forged 124/137/failure-string (3), new flags + masks, delegated-controller preflight, dir usage, write_file parent-swap, read_file symlink-swap, root-symlink refused, lock serialization, publish scan/copy race, publish dir-swap race, publish(".")/media, dot entries, dir count/depth caps, failed-republish restore, run_python mask/size. Some failed only by missing new API (AttributeError/TypeError on new fields/hooks) rather than by behavior. Passed on old (no regression value): FIFO-swap read (old `is_file()` check already refused it; the hang window is not injectable) and staging-private (mkdtemp already 0700).
- Fix summary: `_paths.open_dir` (fd-based dir open) + root-symlink refusal in `resolve_inside`; `write_file`/`read_file`/`list_files`/`publish_site`/`run_python` all under `_state.lock`, using `write_file_atomic`/`open_regular_for_read`/`open_dir`; `publish_site` is a single fd-walk (O_NOFOLLOW, fstat regular, size re-checked while copying, `fchmod` on fd, dot-entries skipped, dir count <=100, depth <=8, root and media overlap rejected, staging 0700 until complete, backup+restore on failed swap, empty new `sites_root` removed on failure). Runner: `--tmpfs /sandbox/<hidden>` (media masked; `run_python` refuses if that path is a symlink), `TasksMax=64`, `MemorySwapMax=0`, `--clearenv` + `--setenv` PATH/HOME/LANG, `max_dir_bytes` (256 MiB default) checked after each run (`dir_bytes`/`disk_exceeded`, tool error "over the limit"), delegated-controller check via `user@UID.service/cgroup.subtree_control` with user.slice fallback. L3: `timed_out` only from the runner timer (wall clock, or rc 124 after >= timeout_s with bwrap's `--info-fd` start marker); "unavailable" only when no start marker arrived; `oom_killed` needs rc 137/-9 AND an increase of `oom_kill` in `user@UID.service/memory.events` (fallback to rc-only when that file is unreadable).
- Proof: all new tests pass; `uv run pytest -q tests/sandbox tests/mcp_servers` -> 92 passed, 5 skipped (P6 pi/bwrap tests).
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (148 passed, 5 skipped)
- Files: src/voice_agent/mcp_servers/_paths.py, src/voice_agent/mcp_servers/code.py, src/voice_agent/sandbox/runner.py, tests/sandbox/test_command.py, tests/mcp_servers/test_code_server.py
- Deviations: (1) `docs/MCP-SERVERS.md` not updated (outside the fix-request's file list); it still lacks the new limits/flags, needs a follow-up. (2) `publish_site` of a symlinked source dir now errors "not a directory" (O_NOFOLLOW gives ENOTDIR); the old test that expected success was changed. (3) `_paths.resolve_inside` now refuses a symlinked root, so the speech server's `media_root` is refused too if it is a symlink. (4) `SandboxResult` gained defaulted fields `dir_bytes`, `disk_exceeded`; `SandboxPolicy` gained `hidden_paths`, `max_dir_bytes`, `info_fd`; `preflight` gained `uid`. (5) fake-runner tests use `bash -c` (dash cannot redirect to fd >= 10).
- Unverified (no bwrap here, VOICE_AGENT_PI not set): that bwrap accepts `--info-fd` and writes it before exec, that `--tmpfs` over the media mount point works inside the `--bind` root, that `--clearenv`/`--setenv` behave as expected, TasksMax/MemorySwapMax on a user scope, that `memory.events` under `user@UID.service` counts descendant OOM kills, and every P6 test. If bwrap does not write the info fd, a genuine systemd failure is reported as a normal result instead of "sandbox unavailable" (fails safe, not forgeable).
- Not done / parked: L2, L4, L8, L9 (not in the fix-request); `config.py` media_root default untouched (human call). M1 relies on the mask only when `media_root` is inside `sandbox_root`.
- Commit: tip of branch worktree-agent-af48160bd5f8030c5 (`08: security fixes (H1, H2, M1-M4, L1, L3, L5-L7)`)

### 2026-10-16 tech-lead: Review Feedback (main @ 92f3edb)
- R1 [R1] code server spawned by McpClientManager has no XDG_RUNTIME_DIR -> systemd-run fails -> run_python always 'sandbox unavailable' (reproduced). R2 [R2, security] media mask fails open when an ancestor of sandbox_root is a symlink (reproduced). R5 [R2] disk/inode cap post-hoc; _dir_usage fragile. R6 [R2, HUMAN] P6/bwrap behavior unverified. R13 [R2] docs drift. R17-R20 [R3].
Full detail (root cause, evidence, fix): `handoff/REVIEW-main-92f3edb.md`. Not auto-fixed; retries need human approval.

### 2026-10-16 developer: DONE (fix package A: R1, R2, R5 worker part, R17, R16 list cap, R20)
- R1: `runner._scrubbed_env` falls back to `/run/user/<uid>` (must be a dir owned by uid) when `XDG_RUNTIME_DIR` is unset, and sets `DBUS_SESSION_BUS_ADDRESS=unix:path=<dir>/bus` only if that socket exists. Nothing else is added; secrets stay out (test asserts). Tests run with an empty environ and check the env given to the subprocess.
- R2: `code._media_rel` tries lexical (abspath both sides) then resolved comparison, fails closed; `run_python` now refuses if ANY component of the hidden path under the real root is a symlink (`_hidden_has_symlink`).
- R5: run refused up front (returns `disk_exceeded=True`, no process started) when the folder is already over `max_dir_bytes`; `_dir_usage` returns over-limit on any OSError (no path in any message) and past `_MAX_SCAN_ENTRIES` (200k); `prlimit --fsize=<max_dir_bytes>` inserted before `bwrap` in the argv (golden argv + preflight tests updated; preflight reports "prlimit not found" if missing).
- R17: cancel path cancels the readers and does a shielded `proc.wait()` (<= grace) before re-raising.
- R16 (list cap): `list_files` capped at 1000 entries plus a truncation marker. R20: `publish_site` runs `_publish` via `asyncio.to_thread`; dot entries filtered while iterating.
- Skipped: R18 (+0.5 s heuristic could misclassify real timeouts; `--unit` memory.events unverifiable without Pi), R19 (`--disable-userns` needs bwrap >= 0.8, unverifiable here; would break all runs on older bwrap), R16 render cap (package D).
- Proof: new tests fail on old src (11 failed incl. 2 updated goldens), pass on fix. `make check` ok; `uv run pytest -q` -> 205 passed, 6 skipped. No bwrap/Pi tests run.
- Unverified: `prlimit --fsize` ahead of bwrap inside `systemd-run --scope`/`timeout` (Pi checkpoint, add to R6); `/run/user/<uid>` fallback on the Pi. SIGXFSZ kills a script writing an oversize file (rc 153).
- Files: src/voice_agent/sandbox/runner.py, src/voice_agent/mcp_servers/code.py, tests/sandbox/test_command.py, tests/mcp_servers/test_code_server.py

### 2026-10-16 test-engineer: DONE (fix G, review R1 proof + R6 cases)
- Added tests/agent/test_code_via_manager.py: real code server spawned via McpClientManager with a wiped parent env (secrets set in parent), stub systemd-run/bwrap/prlimit on PATH; calls code__run_python; asserts stub systemd-run saw XDG_RUNTIME_DIR=/run/user/<uid> and no ANTHROPIC_API_KEY/DEEPGRAM_API_KEY in its env. Skips if /run/user/<uid> missing or memory cgroup not delegated.
- Verified it fails pre-fix: swapped in runner.py from 76d060d (restored afterwards) -> `AssertionError: assert '' == '/run/user/1077000508'`; passes on current runner.
- Added tests/sandbox/test_run_mask.py: argv-level media-mask cases (symlinked parent of sandbox_root, media via real/symlink path mixes, nested media, media outside root, symlinked media dir and intermediate refused, hidden-path escape) plus one pi-marked real-bwrap test (media hidden, writes not leaking). Pi test skipped here (no bwrap); still needs human checkpoint 5.
