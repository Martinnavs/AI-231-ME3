# 04: `audio_engine` contract — Transcriber / Synthesizer ABCs, registry, fakes

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 01
**Status:** done

## Files
- `src/audio_engine/__init__.py`, `src/audio_engine/types.py`, `src/audio_engine/base.py`, `src/audio_engine/registry.py`, `src/audio_engine/fake.py`, `src/audio_engine/wav.py`
- `docs/AUDIO-ENGINE.md` (shared contract; this ticket owns it)
- `tests/audio_engine/test_contract.py`, `tests/audio_engine/test_fake.py`, `tests/audio_engine/test_wav.py`

## Action
Decision 4 of the review: one engine, two interfaces. This module holds the STT and TTS abstractions that both the Pipecat processors (ticket 11) and the speech MCP server (ticket 06) import. Model it on ME2's TTS backend interface — read `.scratch/vendor/AI-222-Machine-Exercises/ME2/docs/adding-a-tts-backend.md` in full (ABC not Protocol, float32 numpy out, no torch, heavy imports inside the class, flat registry dict) — and add what ME2 lacked: **streaming** methods for the live loop, **cancel** for barge-in, and an STT side. Ship deterministic fakes so every later ticket can test without network. Write `docs/AUDIO-ENGINE.md` explaining the contract and "how to add a backend" (mirrors the ME2 doc's shape).

Streaming audio on the wire is **PCM S16LE mono bytes** (what Pipecat frames carry); batch results are **float32 numpy** (what file tools and analysis want). `wav.py` converts between them and reads/writes WAV with `soundfile`.

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| subclass missing an abstract method | `TypeError` at instantiation (ABC) |
| unknown backend name in `create_*` | `KeyError` listing registered names |
| `stream_*` cancelled mid-stream (`task.cancel()` or `await session.cancel()`) | the async iterator stops within one chunk; no further chunks yielded; underlying connection is *kept* if the backend supports reuse |
| empty text to `synthesize` / `stream_synthesize` | returns empty audio / yields nothing; no network call |
| audio at wrong sample rate to `transcribe` | resampled to the backend's rate via `wav.resample` (polyphase) |
| stereo input | downmixed to mono by mean |
| int16 PCM with odd byte length | `ValueError` (corrupt frame) |
| WAV file not found / unreadable | `AudioFileError` with the path |

### C — Contracts
```python
# src/audio_engine/types.py
@dataclass(frozen=True)
class AudioClip:
    samples: npt.NDArray[np.float32]   # shape (n,), mono, range [-1, 1]
    sample_rate: int
    @property
    def duration_s(self) -> float: ...

@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    is_final: bool
    t_audio_end_s: float | None        # audio time the result covers up to, if the backend reports it
    end_of_turn: bool = False          # backend judged the user's turn complete (cloud turn detection; no model on the Pi)

@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str | None

class AudioFileError(Exception): ...

# src/audio_engine/base.py
class Transcriber(ABC):
    sample_rate: int                   # rate the backend wants for streaming PCM
    @abstractmethod
    async def transcribe(self, clip: AudioClip) -> Transcript: ...                 # batch, final only
    @abstractmethod
    def open_stream(self) -> "TranscriptionStream": ...
    async def aclose(self) -> None: ...                                             # default no-op

class TranscriptionStream(ABC):
    @abstractmethod
    async def start(self) -> None: ...                     # opens/reuses the connection; call at app start
    @abstractmethod
    async def send(self, pcm: bytes) -> None: ...          # S16LE mono at Transcriber.sample_rate
    @abstractmethod
    async def finalize(self) -> None: ...                  # end of user turn: flush, request final
    @abstractmethod
    def results(self) -> AsyncIterator[Transcript]: ...    # interim + final, until aclose()
    @abstractmethod
    async def aclose(self) -> None: ...

class Synthesizer(ABC):
    sample_rate: int
    @abstractmethod
    async def synthesize(self, text: str, voice: str | None = None) -> AudioClip: ...
    @abstractmethod
    def stream_synthesize(self, text: str, voice: str | None = None) -> AsyncIterator[bytes]: ...  # S16LE chunks
    @abstractmethod
    async def list_voices(self) -> list[Voice]: ...
    async def warm_up(self) -> None: ...                   # open persistent connection; default no-op
    async def cancel(self) -> None: ...                    # stop current stream, flush; default no-op
    async def aclose(self) -> None: ...

# src/audio_engine/registry.py
def register_transcriber(name: str, cls: type[Transcriber]) -> None: ...
def register_synthesizer(name: str, cls: type[Synthesizer]) -> None: ...
def create_transcriber(name: str, **kwargs: Any) -> Transcriber: ...
def create_synthesizer(name: str, **kwargs: Any) -> Synthesizer: ...
def list_backends() -> dict[str, list[str]]: ...           # {"stt": [...], "tts": [...]}
# "fake" is registered for both at import; ticket 05 adds "deepgram" and "cartesia" lazily
# (registry stores import paths for vendor backends so importing audio_engine never imports websockets).

# src/audio_engine/fake.py
class FakeTranscriber(Transcriber):
    def __init__(self, script: Sequence[str] = ("hello",), interim_every_ms: int = 100, latency_ms: int = 0) -> None: ...
    # stream: yields an interim per interim_every_ms of audio sent, the next scripted text as final after finalize()
    # end of turn: after speech (frame RMS > -40 dBFS) followed by eot_silence_ms of quieter audio, yields one
    # Transcript(end_of_turn=True, is_final=False) per utterance; eot_silence_ms=None disables it
    # extra init kwarg: eot_silence_ms: int | None = 300
class FakeSynthesizer(Synthesizer):
    def __init__(self, sample_rate: int = 16000, ms_per_char: int = 60, chunk_ms: int = 20, first_chunk_latency_ms: int = 0) -> None: ...
    # deterministic: a 220 Hz tone at -12 dBFS, len(text)*ms_per_char long, chunked

# src/audio_engine/wav.py
def read_wav(path: Path) -> AudioClip: ...
def write_wav(path: Path, clip: AudioClip) -> None: ...
def pcm16_to_float(pcm: bytes) -> npt.NDArray[np.float32]: ...
def float_to_pcm16(samples: npt.NDArray[np.float32]) -> bytes: ...   # clips to [-1, 1]
def resample(clip: AudioClip, rate: int) -> AudioClip: ...
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | `soundfile` and `scipy` have aarch64 wheels for Python 3.13 (the Pi) | `uv pip install --dry-run --python-platform aarch64-manylinux_2_28 --python-version 3.13 soundfile scipy` (uv supports cross-platform resolution) |
| A2 | `scipy.signal.resample_poly` round-trip 16k→48k→16k keeps a 1 kHz tone within 0.5 dB | P5 asserts it |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_abc_enforced` — incomplete subclasses raise `TypeError` | `uv run pytest -q tests/audio_engine` |
| P2 | `test_registry_fake_and_unknown` | same |
| P3 | `test_fake_stream_transcribe` — send 1 s of PCM, finalize, results = ≥9 interims then one final `"hello"` | same |
| P4 | `test_fake_stream_synthesize_cancel` — cancel after 2 chunks; no chunk arrives after cancel returns | same |
| P5 | `test_pcm_roundtrip_and_resample` — float→pcm→float error < 1/32768·2; resample tone level within 0.5 dB | same |
| P6 | `test_wav_read_write_stereo_downmix` | same |
| P7 | `test_importing_audio_engine_has_no_vendor_imports` — `sys.modules` has no `websockets` after `import audio_engine` | same |
| P8 | `test_fake_end_of_turn` — 500 ms tone, then 400 ms silence → one `end_of_turn` result within 300 ± 20 ms of the tone's end (audio time); a 200 ms gap inside an utterance yields none; `eot_silence_ms=None` yields none | same |

## Unknowns
None

## Dependencies/license
`numpy` (BSD-3), `soundfile` (BSD-3; libsndfile LGPL, wheels bundle it), `scipy` (BSD-3).

## Acceptance Criteria
- [ ] Contracts implemented exactly; `docs/AUDIO-ENGINE.md` written
- [ ] All Proof rows green; gate suite green

## Non-Goals
- No vendor backends (ticket 05)
- No Pipecat import here (ticket 11 adapts the engine to Pipecat)

## Security-sensitive
no

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- Producer of `docs/AUDIO-ENGINE.md`, consumed by tickets 05, 06, 11, 13

## Execution Log

### 2026-10-15 claude: DONE
- Assumptions verified: A1 `uv pip install --dry-run --python-platform aarch64-manylinux_2_28 --python-version 3.13 soundfile scipy` → resolves (soundfile 0.14.0, scipy 1.18.1); A2 asserted by P5
- Proof: P1–P8 ✅ (`uv run pytest -q tests/audio_engine` → 10 passed)
- Gate suite: ruff ✅ format ✅ mypy ✅ full pytest ✅ (16 passed)
- Files: src/audio_engine/{types,base,registry,fake,wav,__init__}.py, docs/AUDIO-ENGINE.md, tests/audio_engine/*, pyproject.toml (deps + mypy override for soundfile/scipy)
- Deviations: `register_*` also accept a `"module:Class"` string (the ticket's lazy vendor registry needs it); `FakeTranscriber` takes `eot_silence_ms` as the ticket's comment specifies
