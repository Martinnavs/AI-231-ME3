"""Generate the level-3 tickets for the echo work from the verified reference code.

    python3 gen_tickets.py <repo path the tickets should name> <output dir>

skeleton/ holds the new files; edits_a.py / edits_b.py hold the exact replacements for existing
files (applied here in memory to the repo's files to get exact post-edit line counts).
"""

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_SRC = HERE.parents[1]  # where the real files are read from (line counts)
SK = HERE / "skeleton"
REPO, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)


def load(name: str) -> dict[str, list[tuple[str, str]]]:
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.EDITS  # type: ignore[no-any-return]


EDITS_A, EDITS_B = load("edits_a"), load("edits_b")


def edited_lines(files: list[str], *edit_sets: dict[str, list[tuple[str, str]]]) -> dict[str, int]:
    counts = {}
    for f in files:
        s = (REPO_SRC / f).read_text()
        for edits in edit_sets:
            for old, new in edits.get(f, []):
                assert s.count(old) == 1, (f, old[:60])
                s = s.replace(old, new)
        counts[f] = len(s.splitlines())
    return counts


def code(rel: str, lang: str = "python") -> str:
    return f"```{lang}\n{(SK / rel).read_text()}```\n"


def n(rel: str) -> int:
    return len((SK / rel).read_text().splitlines())


def cap(rel: str, test: bool = False) -> str:
    lines = n(rel)
    return f"**{int(lines * (1.25 if test else 1.1)) + 1}** (skeleton {lines})"


def render_edits(edits: dict[str, list[tuple[str, str]]]) -> str:
    out = []
    for path, pairs in edits.items():
        lang = "python"
        out.append(
            f"Edit list for `{path}` ({len(pairs)} replacements; each old block occurs exactly "
            "once in the file as it is now; apply them in the order given and change nothing "
            "else):\n"
        )
        for i, (old, new) in enumerate(pairs, 1):
            out.append(f"{i}. Replace\n\n```{lang}\n{old}```\n\nwith\n\n```{lang}\n{new}```\n")
    return "\n".join(out)


APPROVAL = (
    "| 2026-10-23 | design | cross-device echo cancellation in the live loop (lead 50 ms, "
    "behind --aec); duck-then-stop (pi_speaker.py gets a gain message); livekit added to the "
    "laptop group; agy builds at level 3 | owner |"
)


def ticket(
    num: str,
    title: str,
    decision: str,
    blocked: str,
    objective: str,
    files: list[tuple[str, str, str]],
    nongoals: str,
    security: str,
    flow: str,
    seq: str,
    edges: list[tuple[str, str]],
    contracts: str,
    inventory: str,
    assumptions: list[tuple[str, str]],
    proof_intro: str,
    proofs: list[tuple[str, str]],
) -> str:
    table = "\n".join(f"| `{p}` | {a} | {c} |" for p, a, c in files)
    edge_rows = "\n".join(f"| {e} | {b} |" for e, b in edges)
    a_rows = "\n".join(f"| {a} | {b} |" for a, b in assumptions)
    p_rows = "\n".join(f"| {i} | {t} | `{c}` | |" for i, (t, c) in enumerate(proofs, 1))
    return f"""# Feature Spec: {num} — {title}

**Status:** approved
**Date:** 2026-10-23
**Blocked by:** {blocked}

{decision} Read `../GUARDRAILS.md` first; it binds this ticket.

## Context

- **Objective:** {objective}
- **Role:** strict systems architect; you are transcribing an approved implementation.
- **User goal:** the assistant's voice from the Pi speaker no longer ends up in the transcripts, and
  the speaker gets quieter the moment the user starts talking over it.
- **Source:** `{REPO}/.scratch/echo-fix/PLAN.md`; measurements in `docs/AEC-REMOTE-SPEAKER.md`.
- **Repo:** `{REPO}`

| File | Action | Line cap |
|---|---|---|
{table}

**Non-goals / touch nothing else:** {nongoals} No other file in the repo.

**Security-sensitive:** {security}

## Diagrams

### Data flow

```mermaid
{flow}
```

### Sequence / component

```mermaid
{seq}
```

## RECAP

### E — Edges

| Edge | Behavior |
|---|---|
{edge_rows}

Nothing else.

### C — Contracts

{contracts}
Final inventory: {inventory}

### A — Assumptions

| Assumption | Verified when / how |
|---|---|
{a_rows}

### P — Proof (acceptance criteria)

{proof_intro}

| # | Test / acceptance criterion | Command | Result |
|---|---|---|---|
{p_rows}

## Deviations

| Date | Deviation | Minor: rationale / Material: design-gate re-run |
|---|---|---|

## Approvals

| Date | Gate | What was approved / decided | By |
|---|---|---|---|
{APPROVAL}

## Execution Log
"""


VERIFIED = "Established: ran green in the ticket-writer's overlay of this repo, 2026-10-23"
ROOT = "uv run --offline --group laptop"
FILES_A = ["src/doc_chat/speech.py", "src/doc_chat/playback.py", "src/doc_chat/__main__.py"]
FILES_B = [
    "src/doc_chat/playback.py",
    "src/doc_chat/bargein.py",
    "scripts/pi_speaker.py",
    "src/doc_chat/__main__.py",
]
CA = edited_lines(FILES_A, EDITS_A)
CB = edited_lines(FILES_B, EDITS_A, EDITS_B)
NOW = {f: len((REPO_SRC / f).read_text().splitlines()) for f in set(FILES_A + FILES_B)}


def edit_row(f: str, final: int) -> tuple[str, str, str]:
    return (f, "edit (list below)", f"**{final}** (now {NOW[f]}; exactly {final} after the edits)")


A_LINT = (
    "src/doc_chat/aec.py tests/doc_chat/test_aec.py src/doc_chat/speech.py "
    "src/doc_chat/playback.py src/doc_chat/__main__.py"
)
B_LINT = (
    "tests/doc_chat/test_duck.py src/doc_chat/playback.py src/doc_chat/bargein.py "
    "scripts/pi_speaker.py src/doc_chat/__main__.py"
)

T01 = ticket(
    "01",
    "echo canceller across two devices, behind --aec",
    "`doc_chat --aec` removes the Pi speaker's echo from the microphone using what is sent to the "
    "speaker as WebRTC's reference (LiveKit's AudioProcessingModule, already in the laptop "
    "dependency group). A new `EchoCanceller` is handed every 50 ms slice that `RemotePlayer` "
    "sends, and `MicHub` passes every microphone block through it before anything else sees it.",
    "None (can start immediately)",
    "one new module with its tests, and edits to three existing files. Medium-small.",
    [
        ("src/doc_chat/aec.py", "new, verbatim", cap("src/doc_chat/aec.py")),
        ("tests/doc_chat/test_aec.py", "new, verbatim", cap("tests/doc_chat/test_aec.py", True)),
        edit_row("src/doc_chat/speech.py", CA["src/doc_chat/speech.py"]),
        edit_row("src/doc_chat/playback.py", CA["src/doc_chat/playback.py"]),
        edit_row("src/doc_chat/__main__.py", CA["src/doc_chat/__main__.py"]),
    ],
    "no ducking or gain (ticket 02); no change to the guard, stop rules or STT; "
    "`pyproject.toml`/`uv.lock` are already updated, do not touch them; the Pi is not changed.",
    "no — local audio only; no secrets.",
    'flowchart LR\n    P["RemotePlayer: slices sent to the Pi"] -- "16 kHz reference" --> C["EchoCanceller (new)"]\n    M["microphone block (100 ms)"] --> H["MicHub._take (changed)"]\n    H -- "block" --> C\n    C -- "echo removed" --> Q["queue: guard, STT, pre-roll"]',
    "sequenceDiagram\n    participant P as RemotePlayer thread\n    participant C as EchoCanceller\n    participant H as MicHub (event loop)\n    P->>C: reference(16 kHz bytes) per 50 ms slice\n    H->>C: process(100 ms block)\n    C->>C: per 10 ms frame: render frame (or silence) then capture frame\n    C-->>H: block with the echo removed\n    P->>C: clear() on abort",
    [
        ("no reference queued", "a silence frame is fed as the render frame; the microphone passes through almost unchanged"),
        ("first frame of a reply", "`lead_ms` (default 50) of extra reference is fed ahead, once, when the queue goes from empty to non-empty"),
        ("block with a partial last frame", "the partial bytes pass through unchanged; the output has the input's length"),
        ("`abort()` on the player", "the unused reference is dropped (`clear`)"),
        ("`--aec` without `--play-remote`, `--mic` and `--barge-in`", "exit 2 with `--aec needs --play-remote, --mic and --barge-in`"),
        ("`--aec` not given", "no behaviour change: no canceller is built and MicHub/RemotePlayer get `None`"),
    ],
    f"`src/doc_chat/aec.py` (whole file, verbatim):\n\n{code('src/doc_chat/aec.py')}\n{render_edits(EDITS_A)}",
    "aec.py: constants `RATE`, `FRAME`, `FRAME_BYTES`, `SILENCE`; class `EchoCanceller` (methods `__init__`, `reference`, `clear`, `_render`, `_feed_render`, `process`). Edited files: no new top-level functions; `MicHub._take`, `RemotePlayer`'s reference feed and the `--aec`/`--aec-lead-ms` flags are added by the edit lists.",
    [
        ("LiveKit's AudioProcessingModule takes 10 ms 16-bit frames (`rtc.AudioFrame(data, 16000, 1, 160)`), `process_reverse_stream` for the reference and `process_stream` for the microphone, modifying the capture frame in place", "Established: read and run 2026-10-23"),
        ("A lead of 0-100 ms is robust for path delays of 0-300 ms on a real recording; 200 ms or more degrades as the delay grows", "Established: `docs/AEC-REMOTE-SPEAKER.md`, sweep of 25 settings on `out/remote_echo/fresh80`"),
        ("`livekit` 1.1.21 is in the root laptop group and imports offline", "Established: `uv add` ran 2026-10-23; no other package changed version"),
        ("Unknowns", "None"),
        ("Dependencies/license", "livekit (Apache-2.0), already added"),
    ],
    f"Tests in `tests/doc_chat/test_aec.py` (whole file, verbatim):\n\n{code('tests/doc_chat/test_aec.py')}\nRun from the repo root.",
    [
        ("the 6 new tests pass; the existing playback and remote-speaker tests still pass", f"{ROOT} pytest -q tests/doc_chat/test_aec.py tests/doc_chat/test_playback.py tests/doc_chat/test_remote_speaker.py"),
        ("lint and format clean on the five files", f"{ROOT} ruff check {A_LINT} && {ROOT} ruff format --check {A_LINT}"),
        ("root mypy strict clean", f"{ROOT} mypy"),
        ("the CLI lists the flags", f"{ROOT} python -m doc_chat --help | grep -c -e '--aec'"),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

T02 = ticket(
    "02",
    "duck, then stop: turn the Pi speaker down at the first sign of a voice",
    "At the first sign of the user's voice over the answer (a loud-enough block and two Silero "
    "speech windows), `doc_chat --duck-db -15` turns the speaker down 15 dB at once; if the 7-of-10 "
    "guard then fires the reply stops as before, and if the voice goes away the volume comes "
    "back after 0.6 s. The Pi applies the gain to audio it has not yet played (a `gain` message), "
    "and the canceller's reference follows. Before any code, confirm ticket 01 landed: "
    "`src/doc_chat/aec.py` exists, `RemotePlayer.__init__` takes `canceller`, and `client.canceller` "
    "exists. A mismatch means stop.",
    "01",
    "four edited files and one new test file. Medium-small.",
    [
        ("tests/doc_chat/test_duck.py", "new, verbatim", cap("tests/doc_chat/test_duck.py", True)),
        edit_row("src/doc_chat/playback.py", CB["src/doc_chat/playback.py"]),
        edit_row("src/doc_chat/bargein.py", CB["src/doc_chat/bargein.py"]),
        edit_row("scripts/pi_speaker.py", CB["scripts/pi_speaker.py"]),
        edit_row("src/doc_chat/__main__.py", CB["src/doc_chat/__main__.py"]),
    ],
    "no change to `aec.py`, `speech.py` or the guard's thresholds; the copy of `pi_speaker.py` on the Pi is deployed by the human, not by you.",
    "no — local audio only; the Pi message carries a number.",
    'flowchart LR\n    G["BargeInGuard + SileroOnset.recent_speech (changed)"] -- "voiced" --> D["Ducker (new)"]\n    D -- "gain dB" --> R["RemotePlayer.set_gain (new)"]\n    R -- "{type: gain, db}" --> Pi["pi_speaker.py: Player.set_gain (new)"]\n    R -- "gain" --> C["EchoCanceller.gain"]',
    "sequenceDiagram\n    participant F as feed() per 100 ms block\n    participant D as Ducker\n    participant R as RemotePlayer\n    participant Pi as Pi Player\n    F->>D: update(voiced, fired, now)\n    D-->>F: -15.0\n    F->>R: set_gain(-15)\n    R->>Pi: gain message\n    Pi->>Pi: scale slices from now on, with a ramp\n    alt guard fires\n        F->>R: abort (existing)\n    else voice gone for 0.6 s\n        D-->>F: 0.0\n        F->>R: set_gain(0)\n    end",
    [
        ("`--duck-db` 0 (the default)", "no ducker is built; behaviour is unchanged"),
        ("voiced and not yet ducked", "`Ducker.update` returns the duck gain once"),
        ("already ducked and still voiced", "None (no repeat)"),
        ("ducked, voice gone for `restore_s`", "returns 0.0 and resets"),
        ("ducked and the guard fires", "resets without returning a gain (the reply is aborted anyway)"),
        ("a new reply", "a new player starts at gain 1.0 (and `RemotePlayer.__init__` sets the canceller's gain to 1.0)"),
        ("`gain` message before the Pi has a player", "ignored"),
    ],
    f"`tests/doc_chat/test_duck.py` is in the Proof section below.\n\n{render_edits(EDITS_B)}",
    "No new top-level functions in edited files except `apply_gain` (playback.py) and class `Ducker` (bargein.py); new methods `Player.set_gain`, `RemotePlayer.set_gain`, `SileroOnset.recent_speech`, `Ducker.__init__`, `Ducker.ducked`, `Ducker.update`; the `--duck-db`, `--duck-k` flags.",
    [
        ("Ticket 01's exports as listed above", "Must verify: Step 0"),
        ("`pi_speaker.py` gets `player.set_gain` from `doc_chat.playback.Player`; the Pi's copy of `playback.py` must be updated by the human before `--duck-db` is used", "Established: pi_speaker.py imports `Player` from `doc_chat.playback` (read 2026-10-23)"),
        ("`BargeInGuard` exposes `last_level` and `last_bar`", "Established: bargein.py, used in the guard trace"),
        ("Unknowns", "None"),
        ("Dependencies/license", "none new"),
    ],
    f"Tests in `tests/doc_chat/test_duck.py` (whole file, verbatim):\n\n{code('tests/doc_chat/test_duck.py')}\nRun from the repo root.",
    [
        ("the 7 new tests and the earlier tests of these modules pass", f"{ROOT} pytest -q tests/doc_chat/test_duck.py tests/doc_chat/test_aec.py tests/doc_chat/test_playback.py tests/doc_chat/test_remote_speaker.py tests/doc_chat/test_bargein.py"),
        ("lint and format clean on the five files", f"{ROOT} ruff check {B_LINT} && {ROOT} ruff format --check {B_LINT}"),
        ("root mypy strict clean", f"{ROOT} mypy"),
        ("the CLI lists the flags", f"{ROOT} python -m doc_chat --help | grep -c -e '--duck-db'"),
        ("line caps respected", "wc -l on the files in the table"),
    ],
)

for name, text in [("01-echo-canceller.md", T01), ("02-duck-then-stop.md", T02)]:
    (OUT / name).write_text(text)
    print("wrote", OUT / name, len(text.splitlines()), "lines")
