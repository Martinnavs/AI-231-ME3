# 12: Barge-in gating, cancel/flush, speculative LLM start, fillers

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 11
**Status:** ready-for-agent

## Files
- `src/voice_agent/pipeline/barge_in.py`, `src/voice_agent/pipeline/gain.py`, `src/voice_agent/pipeline/speculative.py`, `src/voice_agent/pipeline/fillers.py`
- `src/voice_agent/pipeline/processors.py`, `src/voice_agent/pipeline/app.py` (wire the above)
- `scripts/make_fillers.py`, `assets/fillers/README.md`
- `src/voice_agent/config.py`, `config/agent.toml`, `tests/test_config.py` (add the two `TurnConfig` fields below, nothing else)
- `tests/pipeline/test_barge_in.py`, `tests/pipeline/test_gain.py`, `tests/pipeline/test_speculative.py`, `tests/pipeline/test_fillers.py`

## Action
The review's full-duplex refinements:

1. **Barge-in gate** (review §2 item 2, decision 2): hardware AEC reduces echo but doesn't remove it, so while the bot is speaking, interrupt only after **`min_barge_in_ms` (250) of continuous VAD speech**. Shorter blips (coughs, clicks, residual echo) don't interrupt. When the bot is silent, normal VAD behavior applies.
   - **Duck first** (`FULL-DUPLEX-RESEARCH.md` §3.3): the moment VAD reports speech while the bot is speaking, ramp the bot's output gain to `barge_in_duck_db` (−15 dB) over 5 ms and emit `BOT_DUCKED`. If the speech ends before the gate fires, ramp back to 0 dB over `3 × barge_in_fade_ms` and emit `BOT_RESTORED`. The user hears the bot back off within one VAD frame instead of talking over them for 250 ms, and quieter playback also leaves less echo for the AEC.
   - **Fade, don't cut**: when the gate fires, ramp from the current gain to silence over `barge_in_fade_ms` (15 ms, linear in dB is fine; must reach ≤ −60 dB) before the queue is dropped, so the stop doesn't click.
   - Gain is applied by one `OutputGain` stage just before `transport.output()`, sample-accurate across frame boundaries.
2. **On barge-in:** emit `BARGE_IN_DETECTED`; start the fade; push Pipecat's interruption; `synthesizer.cancel()` (flush, review §2 item 8: cancel and flush, no "POSIX signal"); drop queued output audio; cancel the agent's `respond()` and call `record_interruption(turn_id, spoken_chars)`; emit `BARGE_IN_MUTE` when the fade has finished and the output transport has actually stopped emitting bot audio. After the mute, reset the gain to 0 dB for the next reply.
3. **Speculative LLM start** (review §3, off by default via `TurnConfig.speculative_llm`): when an interim transcript looks like a complete clause and VAD silence has lasted `speculative_silence_ms` (180), start `respond()` early but **hold its audio**; if the endpoint confirms and the final transcript matches the speculative text (normalized: lowercase, punctuation stripped), release it; otherwise cancel (`LLM_CANCELLED`, `data.reason`) and start fresh. Log a running cancellation rate (cancelled requests are still billed for input tokens).
4. **Fillers** (review §3), three kinds, all pre-recorded clips (no model), all logged as `FILLER_PLAYED` with `kind`, clip name and duration so the benchmark reports "first answer audio" separately (ticket 14):
   - `delegate`: on a turn where the fast agent calls `delegate` (`DelegationStarted`), if the model hasn't already produced speech, play a short clip ("Let me check.").
   - `stall`: if no LLM text delta has arrived `TurnConfig.stall_filler_ms` (default 700) after `ENDPOINT`, play a **non-committal** clip ("Mm, one sec."). It never states a result or claims an action happened or will happen.
   - `notice`: if still nothing `TurnConfig.stall_notice_ms` (default 2500) after `ENDPOINT`, play one honest clip ("Sorry, the connection's slow, give me a moment."), then keep waiting.
   At most one filler per kind per turn; none once the model has produced speech; clips rotate so consecutive turns do not repeat the same one. Fillers never substitute for the answer: the real reply is always still generated, and no tool is ever skipped or faked. `scripts/make_fillers.py` renders the clips with the configured TTS (a paid call: the human runs it; tests use `FakeSynthesizer`).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| user speech 200 ms while bot speaks | no interruption; `bot_ducked` then `bot_restored`; bot keeps talking; no `barge_in_detected` |
| VAD speech while the bot is silent | no ducking (nothing to duck); normal turn |
| new bot reply starts while still ducked (speech ongoing, gate not yet fired) | the new reply starts at the ducked gain, not 0 dB |
| user speech ≥ 250 ms while bot speaks | duck at speech start, then interruption at 250 ms; 15 ms fade; bot audio stops; `barge_in_mute − barge_in_detected` < 100 ms in file mode (asserted) |
| barge-in during a filler | filler stops; pending real answer is cancelled too |
| barge-in after the LLM finished but TTS still playing | TTS cancel + flush; history records only the spoken prefix |
| speculative text ≠ final text | cancel; `LLM_CANCELLED` with `reason="text_changed"`; fresh request on final |
| user resumes speaking after a speculative start | cancel; `reason="user_resumed"` |
| speculative path disabled | identical behavior to ticket 11 (regression test) |
| filler clip missing on disk | skip filler, warn once |
| two delegations in one turn | at most one `delegate` filler per turn |
| LLM first delta arrives before `stall_filler_ms` | no stall filler |
| LLM first delta arrives between `stall_filler_ms` and `stall_notice_ms` | exactly one `stall` filler, no `notice`; real answer follows it without overlap |
| LLM silent past `stall_notice_ms` | `stall` then `notice`, then the answer whenever it arrives |
| barge-in or cancel while a stall/notice filler is pending | the pending filler is dropped |

### C — Contracts
```python
# src/voice_agent/pipeline/barge_in.py
class BargeInGate:
    def __init__(self, *, min_speech_ms: int) -> None: ...
    def on_bot_speaking(self, speaking: bool, t_mono_ns: int) -> None: ...
    def on_vad(self, speech: bool, t_mono_ns: int) -> GateAction: ...   # INTERRUPT exactly once per barge-in

class GateAction(StrEnum):
    NONE = "none"
    DUCK = "duck"            # bot speaking, VAD speech started
    RESTORE = "restore"      # speech ended before min_speech_ms
    INTERRUPT = "interrupt"  # min_speech_ms reached

# src/voice_agent/pipeline/gain.py
class OutputGain:
    def __init__(self, *, sample_rate: int) -> None: ...
    def ramp_to(self, gain_db: float, ramp_ms: float) -> None: ...   # from the current gain; gain_db=-inf → silence
    def process(self, pcm: bytes) -> bytes: ...                       # s16le mono; ramps continue across calls
    @property
    def settled(self) -> bool: ...                                    # no ramp in progress

# src/voice_agent/pipeline/speculative.py
def looks_complete(interim: str) -> bool: ...                  # ends with . ? ! or ≥ 6 words and no trailing conjunction
def same_utterance(a: str, b: str) -> bool: ...                # normalized equality
@dataclass
class SpeculativeStats:
    started: int
    confirmed: int
    cancelled: int
    @property
    def cancel_rate(self) -> float: ...

# src/voice_agent/pipeline/fillers.py
@dataclass(frozen=True)
class Filler:
    name: str
    clip: AudioClip
def load_fillers(directory: Path, sample_rate: int) -> list[Filler]: ...
class FillerPolicy:
    def choose(self, turn_id: int, already_spoke: bool, kind: str = "delegate") -> Filler | None: ...   # kind: delegate | stall | notice; rotates; max one per kind per turn

# src/voice_agent/config.py — TurnConfig gains: stall_filler_ms: int = 700; stall_notice_ms: int = 2500 (also in config/agent.toml)

# scripts/make_fillers.py  — main(argv) -> int ; --backend {fake,cartesia} --out assets/fillers
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | Pipecat's built-in interruption can be suppressed/deferred so our gate decides (custom interruption strategy or by intercepting VAD/user-started-speaking frames) | read the pinned version's interruption mechanism before coding; record the chosen hook in the log |
| A2 | Our output path can drop already-queued audio frames on interruption | verify with P2's timing assertion |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_gate_thresholds` — table: 100/200/249 ms → `DUCK` then `RESTORE`, never `INTERRUPT`; 250/400 ms → `DUCK` then `INTERRUPT` once; bot silent → `INTERRUPT` immediately at VAD start, no `DUCK` | `uv run pytest -q tests/pipeline/test_barge_in.py` |
| P2 | `test_barge_in_file_mode` — `main(--fake --input-wav barge.wav …)` where the user speaks 500 ms into the bot's reply: output WAV bot energy ends within 150 ms of `barge_in_detected` (asserted on the WAV, not just the log) and `barge_in_mute − barge_in_detected < 100 ms` | same |
| P3 | `test_cough_ducks_then_restores` — 150 ms noise burst during bot reply: output WAV RMS (10 ms frames) drops 15 ± 2 dB within 20 ms of `bot_ducked` and is back within 1 dB of the pre-burst level within 100 ms of `bot_restored`; bot keeps talking | same |
| P6 | `test_gain_ramps` — on a constant 1 kHz tone: `ramp_to(-15, 5)` reaches −15 ± 0.5 dB in 5 ms; `ramp_to(-inf, 15)` ends ≤ −60 dB at 15 ms ± 1 sample; a ramp split across 1-, 160- and 320-sample frames gives identical output; the largest sample-to-sample step during the fade is ≤ the tone's own largest step (no click) | `uv run pytest -q tests/pipeline/test_gain.py` |
| P4 | `test_speculative_confirm_and_cancel` — confirm path releases held audio; text-changed path cancels and logs; disabled path reproduces ticket 11 P3 stage order | `uv run pytest -q tests/pipeline/test_speculative.py` |
| P5 | `test_filler_only_on_delegation` — non-delegating turn with a prompt LLM reply: no `filler_played`; delegating turn: exactly one `delegate`, with duration | `uv run pytest -q tests/pipeline/test_fillers.py` |
| P7 | `test_stall_and_notice_timing` — fake LLM delayed 1.0 s: one `stall` at 700 ± 50 ms after `endpoint`, no `notice`; delayed 3.0 s: `stall` then `notice` at 2500 ± 50 ms; delayed 0.3 s: none; every case ends with the real answer and no tool skipped | same |
| P8 | `test_stall_clips_are_noncommittal` — every shipped clip name/transcript in `assets/fillers/README.md` is on an allowlist test (no digits, no "done/set/playing/okay"); consecutive turns never repeat the same clip | same |

## Unknowns
None

## Dependencies/license
None new.

## Acceptance Criteria
- [ ] All four behaviors wired, including duck/restore and the fade; `speculative_llm` defaults to false
- [ ] All Proof rows green; ticket 11 tests still green; gate suite green

## Non-Goals
- Software AEC (hardware AEC is the decision)
- Tuning thresholds on real hardware (human checkpoint 2)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/EVENT-LOG.md`

## Execution Log
