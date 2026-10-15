# MCP servers

Stdio MCP servers (FastMCP, `mcp>=1.2,<2`) that expose the agent's capabilities to subagents as tools. Each runs as `python -m voice_agent.mcp_servers.<name>`. Servers never write to stdout except MCP protocol; logging goes to stderr.

> Status: this design still needs the instructor's sign-off (DESIGN.md human checkpoint 1).

## Path confinement (shared by every server)

`voice_agent.mcp_servers._paths.resolve_inside(root, rel, *, must_exist)` is the only way a server turns a tool-supplied path into a filesystem path.

- `rel` must be a relative string. Rejected with `PathEscapeError`: empty, NUL byte, absolute (POSIX or Windows anchor), any `..` component.
- The joined path is resolved with symlinks; the result must be `root` or inside it (`is_relative_to` on resolved paths, so `media2` is not mistaken for `media`).
- `must_exist=True` and missing → `FileNotFoundError`.
- Servers map `PathEscapeError` to the tool error `"path outside media root"`; nothing is read or written.

## speech

Run: `python -m voice_agent.mcp_servers.speech [--stt-backend NAME] [--tts-backend NAME] [--media-root DIR] [--max-audio-s 120]`

| Flag | Default |
|---|---|
| `--stt-backend` / `--tts-backend` | `agent.stt_backend` / `agent.tts_backend` from `config/agent.toml`; `fake` in tests |
| `--media-root` | `paths.media_root` |
| `--max-audio-s` | 120 |

Backends are created on first use through the `audio_engine` registry (see `AUDIO-ENGINE.md`); a backend `SpeechBackendError` becomes a tool error and the server keeps running.

### Tools

| Tool | Returns | Errors |
|---|---|---|
| `transcribe_audio_file(path)` | `{"text", "language", "duration_s"}` | outside media root; missing or non-audio file (names the relative path); audio longer than `--max-audio-s` |
| `synthesize_speech_to_file(text, path, voice=None, overwrite=False)` | `{"path", "duration_s", "sample_rate"}` | outside media root; text over 2 000 chars; output exists without `overwrite=true`; output is a directory |
| `get_tts_voices()` | `[{"id", "name", "language"}]` | backend error |

`synthesize_speech_to_file` creates missing parent directories, only inside the media root.

### Resource

`speech://voices`: markdown list of the same voices as `get_tts_voices`.
