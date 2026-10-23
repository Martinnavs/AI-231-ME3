# 19: Media MCP server — real music playback from a local library, ducked around speech

**Repo:** /mnt/jfs_hpc/home/anthony.martin.navarez/ME3
**Blocked by:** 16, 11, 28
**Status:** ready-for-agent

## Amendment 2026-10-23 (direction change: local speech, laptop mic + Pi loudspeaker, `doc_chat` findings)
Playback path changes before this is built. With the Pi as loudspeaker only, `mpv` running on the Pi would be invisible to the echo guard (it only knows the level of audio it sent), so music would look like a barge-in. Decode and mix on the **laptop** and send the audio through `RemoteSpeaker`, so music counts as played level. Ducking then happens in the laptop mixer (`OutputGain`, ticket 12), not by `mpv` IPC. The Pi-side `dmix`/`dsnoop` setup (item 5) is dropped. Tools, search and the library (items 1 and 3) are unchanged.

## Files
- `src/voice_agent/media/__init__.py`, `src/voice_agent/media/library.py`, `src/voice_agent/media/player.py`
- `src/voice_agent/mcp_servers/media.py`
- `src/voice_agent/pipeline/music_ducker.py`; wire it in `src/voice_agent/pipeline/app.py` (add `tools: ToolRegistry | None = None` to `PipelineDeps`)
- `src/voice_agent/telemetry/events.py` + `docs/EVENT-LOG.md` (add `MUSIC_DUCKED`, `MUSIC_RESTORED`)
- `src/voice_agent/config.py`, `config/agent.toml` (`[media]` table, `[mcp_servers.media]`)
- `src/voice_agent/preflight.py` (checks `mpv`, `music_library`, `shared_output`)
- `deploy/asound.conf.example`, `docs/PI-SETUP.md` (music + shared-device section), `docs/MCP-SERVERS.md` (media section)
- `tests/media/test_library.py`, `tests/media/test_player.py`, `tests/media/test_mpv_real.py` (skips without `mpv`), `tests/mcp_servers/test_media_server.py`, `tests/pipeline/test_music_ducker.py`, `tests/test_preflight.py` (extend)

## Action
The professor's example (2026-10-15): "play a Taylor Swift song" → it plays; mid-song "no, I don't want that, I want Opalite" → it stops and plays Opalite; "stop" → it stops. He suggested a local music catalogue on the Pi so the demo doesn't depend on the internet, and "if you want Spotify, build a Spotify MCP server". So: a media MCP server over a folder of audio files, with real playback, and a pipeline hook that turns the music down whenever someone is talking.

1. **Library** (`paths.music_root`, default `~/Music`): scan `.mp3 .flac .ogg .opus .m4a .wav` recursively at server start (and on `rescan`). Read title/artist/album/genre/duration with `tinytag`; when tags are missing, parse `Artist - Title.ext`, else use the file stem as title. Track id = first 12 hex chars of sha1 of the path relative to `music_root`. Search with `rapidfuzz` over `"{artist} {title} {album} {genre}"`; an artist-only match (query ≈ an artist name) returns that artist's tracks.
2. **Player:** `mpv` as a separate process (`--no-video --idle=yes --input-ipc-server=<runtime sock> --audio-device=alsa/<device>`), controlled over its JSON IPC socket from asyncio. Started lazily on first play, restarted once if it died. `FakePlayer` implements the same protocol for tests and `--fake`. Effective volume = user volume × (duck factor when ducked); user volume persists across ducking.
3. **Tools** (contract below). `play` always **replaces** what's playing (that's how a correction works). An artist query queues that artist's tracks, shuffled with a seed. No match above the score threshold → tool error listing up to 3 nearest titles, so the model can ask "did you mean…?".
4. **Ducking around speech** (`music_ducker.py`): music must not drown out the user or the bot. On `vad_speech_start` (user) or bot speech start, call the hidden tool `media__duck(on=True)` (once; idempotent). When neither has spoken for `media.restore_after_s` (1.0 s), call `duck(on=False)`. Emit `MUSIC_DUCKED` / `MUSIC_RESTORED`. No calls when the media server isn't configured or nothing is playing.
5. **One output device for everything.** The bot (PortAudio) and mpv must play through the same speakerphone so its hardware echo canceller removes music as well as speech. Two processes can't open a raw `hw`/`plughw` ALSA device at once, so the runbook sets up an ALSA `dmix` (playback) + `dsnoop` (capture) pair for the speakerphone (`deploy/asound.conf.example`) and points both `audio.output_device` and `media.audio_device` at it. Preflight warns when media is enabled and the output device is a raw `hw:`/`plughw:` name.
6. **Docs:** `docs/MCP-SERVERS.md` media section (tools, search rules, hidden `duck`); `docs/PI-SETUP.md`: `sudo apt install mpv`, filling `~/Music` (any legally owned files; name them `Artist - Title`), the dmix/dsnoop setup and how to test it (`aplay` and `mpv` playing at the same time).

## RECAP

### E — Edges
| Edge | Behavior |
|---|---|
| `music_root` missing or empty | server starts; `search_music`/`play` return error "the music library is empty"; preflight warns |
| file with unreadable tags | filename fallback; scan never aborts on one file |
| two tracks with the same title (different artists) | both returned by search; `play(query)` picks the higher score, ties → the one whose artist also matches the query, else the first by artist name |
| `play` while playing | current track and queue replaced; `now_playing` changes within one IPC round trip |
| no match ≥ threshold (default 60) | tool error with up to 3 nearest titles |
| `mpv` not installed | server starts; playback tools return error "music player not installed (sudo apt install mpv)"; preflight fails `mpv` |
| `mpv` dies mid-session | next tool call restarts it once and retries; a second failure → tool error |
| IPC socket path too long / stale socket file | socket under `$XDG_RUNTIME_DIR` (or `/tmp`) with a short name; stale file removed before start |
| `duck(True)` twice, then `set_volume(80)` while ducked, then `duck(False)` | effective volume ducked once; user volume becomes 80; after restore effective = 80 |
| `control("next")` at end of queue | stops; `now_playing` → `{playing: false}` |
| user speaks while no music is playing | ducker makes no tool call |
| media server not configured | ducker disabled; pipeline unchanged (ticket 11/12 tests stay green) |

### C — Contracts
```python
# src/voice_agent/media/library.py
@dataclass(frozen=True)
class Track:
    id: str
    path: Path
    title: str
    artist: str | None
    album: str | None
    genre: str | None
    duration_s: float | None

class Library:
    def __init__(self, root: Path) -> None: ...
    def scan(self) -> int: ...                                    # returns track count
    def search(self, query: str, limit: int = 5) -> list[tuple[Track, float]]: ...   # (track, score 0-100), best first
    def artist_tracks(self, query: str) -> list[Track] | None: ... # None when query isn't an artist
    def get(self, track_id: str) -> Track | None: ...

# src/voice_agent/media/player.py
class Player(Protocol):
    async def play(self, tracks: Sequence[Track]) -> None: ...    # replaces playback and queue
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def stop(self) -> None: ...
    async def next(self) -> bool: ...                             # False at end of queue
    async def set_volume(self, effective_pct: int) -> None: ...
    async def status(self) -> dict[str, object]: ...              # {"playing","paused","track_id","position_s"}
    async def aclose(self) -> None: ...
class MpvPlayer: ...      # implements Player over mpv JSON IPC
class FakePlayer: ...     # implements Player in memory; records calls

# src/voice_agent/mcp_servers/media.py   (run: python -m voice_agent.mcp_servers.media [--music-root DIR] [--fake-player])
mcp = FastMCP("media")
@mcp.tool()
async def search_music(query: str, limit: int = 5) -> list[dict[str, object]]: ...
@mcp.tool()
async def play(query: str | None = None, track_id: str | None = None) -> dict[str, object]: ...   # exactly one; {"now_playing": {...}, "queued": int}
@mcp.tool()
async def control(action: Literal["pause", "resume", "stop", "next"]) -> dict[str, object]: ...
@mcp.tool()
async def set_volume(level: int | None = None, change: Literal["up", "down"] | None = None) -> dict[str, object]: ...  # level 0-100; change ±15
@mcp.tool()
async def now_playing() -> dict[str, object]: ...
@mcp.tool()
async def rescan() -> dict[str, object]: ...
@mcp.tool()
async def duck(on: bool) -> dict[str, object]: ...   # hidden (AgentConfig.hidden_tools); pipeline only

# config additions
@dataclass(frozen=True)
class MediaConfig:
    audio_device: str | None = None      # mpv ALSA device; None → audio.output_device
    duck_volume_pct: int = 20            # effective volume while ducked, as % of user volume
    restore_after_s: float = 1.0
    match_threshold: int = 60

# src/voice_agent/pipeline/music_ducker.py
class MusicDucker:
    def __init__(self, *, tools: ToolRegistry, restore_after_s: float, stage_logger: StageLogger | None = None) -> None: ...
    def on_user_speech(self, active: bool, t_mono_ns: int) -> None: ...
    def on_bot_speaking(self, active: bool, t_mono_ns: int) -> None: ...
    async def run(self) -> None: ...     # issues duck/restore calls; cancelled at shutdown
```

### A — Assumptions
| # | Assumption | Verify when / how |
|---|---|---|
| A1 | mpv's JSON IPC accepts `loadfile`, `set_property` (`pause`, `volume`), `playlist-next`, `stop`, and reports `idle-active` | mpv manual "JSON IPC" (WebFetch); P4's fake socket server uses the quoted shapes |
| A2 | `tinytag` and `rapidfuzz` install on aarch64 / Python 3.13 | `uv pip install --dry-run --python-platform aarch64-manylinux_2_28 --python-version 3.13 tinytag rapidfuzz` |
| A3 | ALSA `dmix`/`dsnoop` lets PortAudio and mpv share one USB speakerphone | ALSA docs; confirmed on the Pi by the human (runbook test), not here |
| A4 | The speakerphone's hardware AEC cancels any audio it plays, including music from a second process | human checkpoint (AEC device); if false, the ducker's restore delay and duck level are the mitigation |

### P — Proof
| # | Test | Command |
|---|---|---|
| P1 | `test_scan_and_search` — tmp library of generated 0.5 s WAVs named `Taylor Swift - Opalite.wav`, `Taylor Swift - Shake It Off.wav`, `Norah Jones - Sunrise.wav`, `untitled.wav`: 4 tracks; "opalite" → Opalite first with score ≥ 90; "taylor swift" → `artist_tracks` has 2; "opalyte" (typo) still finds Opalite; "beethoven" → nothing ≥ 60 | `uv run pytest -q tests/media/test_library.py` |
| P2 | `test_tools_with_fake_player` — play "taylor swift" → queue of 2; play "Opalite" → replaced, `now_playing.title == "Opalite"`; pause/resume/next/stop; no match → error listing nearest titles | `uv run pytest -q tests/mcp_servers/test_media_server.py` |
| P3 | `test_duck_volume_math` — the Edge-table sequence gives effective volumes 100 → 20 → 16 (user 80 while ducked) → 80 | same |
| P4 | `test_mpv_ipc_commands` — `MpvPlayer` against a fake unix-socket server: play/pause/volume/next send the exact JSON commands from A1; a dead socket triggers one restart | `uv run pytest -q tests/media/test_player.py` |
| P5 | `test_mpv_real` — skipped unless `mpv` is on PATH: play a generated 1 s WAV with `--ao=null`, observe playing then idle | `uv run pytest -q tests/media/test_mpv_real.py` |
| P6 | `test_ducker_in_file_mode` — `main(["--fake", "--with-mcp", ...])` with the media server on `--fake-player` and a track playing: `music_ducked` within 100 ms of `vad_speech_start`, `music_restored` 1.0 ± 0.1 s after the bot's reply ends; no ducker calls in a run without music | `uv run pytest -q tests/pipeline/test_music_ducker.py` |
| P7 | `test_list_tools_and_hidden` — stdio client lists the 7 tools; the fast agent's subset excludes `media__duck` | `uv run pytest -q tests/mcp_servers/test_media_server.py` |
| P8 | `test_preflight_media_checks` — no `mpv` → fail with fix; empty `music_root` → warn; `audio.output_device = "plughw:CARD=X,DEV=0"` with media enabled → warn "use the dmix device" | `uv run pytest -q tests/test_preflight.py` |

## Unknowns
- Whether the AEC cancels music well enough for STT during loud playback — human checkpoint; the ducker limits the damage.

## Dependencies/license
`tinytag` (MIT), `rapidfuzz` (MIT). `mpv` is a system package run as a separate process (GPL-2.0+, not linked); installed by the human on the Pi.

## Acceptance Criteria
- [ ] Media server, ducker and preflight checks implemented; runbook section written
- [ ] P1–P4, P6–P8 green; P5 green or skipped with reason; tickets 11/12 tests still green; gate suite green

## Non-Goals
- Spotify or any streaming service; downloading music; playlists beyond the per-play queue
- Mirroring playback onto the ME2 dashboard's music widget

## Security-sensitive
no (reads one configured folder; spawns a fixed local binary with fixed arguments; file paths come from the scan, never from the model)

## Shared Context
- `.scratch/project2-voice-agent/RUNNER.md`
- `docs/MCP-SERVERS.md`, `docs/EVENT-LOG.md`

## Execution Log
