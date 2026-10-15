from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


class ConfigError(Exception):
    pass


@dataclass(frozen=True, repr=False)
class Secrets:
    anthropic_api_key: str | None = None
    deepgram_api_key: str | None = None
    cartesia_api_key: str | None = None

    def __repr__(self) -> str:
        parts = ", ".join(
            f"{f.name}={'***' if getattr(self, f.name) is not None else None!r}"
            for f in fields(self)
        )
        return f"Secrets({parts})"


@dataclass(frozen=True)
class AudioConfig:
    sample_rate_in: int = 16000
    sample_rate_out: int = 16000
    input_device: str | None = None
    output_device: str | None = None


@dataclass(frozen=True)
class TurnConfig:
    vad_stop_secs: float = 0.2
    endpointing: str = "stt"
    vad_fallback_stop_secs: float = 0.8
    vad_turn_stop_secs: float = 0.5
    min_barge_in_ms: int = 250
    barge_in_duck_db: float = -15.0
    barge_in_fade_ms: int = 15
    speculative_llm: bool = False
    speculative_silence_ms: int = 180


@dataclass(frozen=True)
class AgentConfig:
    fast_model: str = "claude-haiku-4-5"
    slow_model: str = "claude-sonnet-5-5"
    history_turns: int = 6
    stt_backend: str = "deepgram"
    tts_backend: str = "cartesia"
    tts_voice: str | None = None
    fast_tool_prefixes: tuple[str, ...] = ("home__", "media__", "assistant__")
    hidden_tools: tuple[str, ...] = ("media__duck", "assistant__due_events")


@dataclass(frozen=True)
class PathsConfig:
    sandbox_root: Path = Path("~/agent_sandbox").expanduser()
    media_root: Path = Path("~/agent_sandbox/media").expanduser()
    music_root: Path = Path("~/Music").expanduser()
    sites_root: Path = Path("~/agent_sites").expanduser()
    state_dir: Path = Path("~/.local/state/voice_agent").expanduser()
    log_dir: Path = Path("./out/logs")


@dataclass(frozen=True)
class McpServerSpec:
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    audio: AudioConfig
    turn: TurnConfig
    agent: AgentConfig
    paths: PathsConfig
    mcp_servers: dict[str, McpServerSpec]
    home_dashboard_url: str = "http://127.0.0.1:8000"
    sites_host: str = "raspberrypi.local"
    sites_port: int = 8080
    location: str = "Manila"
    timezone: str = "Asia/Manila"
    secrets: Secrets = field(default_factory=Secrets)


_SECRET_ENV = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "deepgram_api_key": "DEEPGRAM_API_KEY",
    "cartesia_api_key": "CARTESIA_API_KEY",
}
_TOP_SCALARS = ("home_dashboard_url", "sites_host", "sites_port", "location", "timezone")


def _build[T](cls: type[T], table: Mapping[str, Any], where: str) -> T:
    known = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    for key in table:
        if key not in known:
            raise ConfigError(f"unknown key {where}{key!r}")
    kwargs: dict[str, Any] = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name not in table:
            continue
        value = table[f.name]
        if "Path" in str(f.type):
            value = Path(str(value)).expanduser()
        elif "tuple" in str(f.type):
            value = tuple(value)
        kwargs[f.name] = value
    return cls(**kwargs)


def _mcp_servers(raw: Mapping[str, Any]) -> dict[str, McpServerSpec]:
    servers: dict[str, McpServerSpec] = {}
    for name, table in raw.items():
        if not isinstance(table, Mapping):
            raise ConfigError(f"mcp_servers.{name} must be a table")
        servers[name] = _build(McpServerSpec, table, f"mcp_servers.{name}.")
    return servers


def load_config(
    path: Path = Path("config/agent.toml"), env: Mapping[str, str] | None = None
) -> AppConfig:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    with path.open("rb") as fh:
        try:
            raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc

    sections = {"audio", "turn", "agent", "paths", "mcp_servers", *_TOP_SCALARS}
    for key in raw:
        if key not in sections:
            raise ConfigError(f"unknown key {key!r}")

    if env is None:
        dotenv_path = path.resolve().parent.parent / ".env"
        merged: dict[str, str] = {}
        if dotenv_path.is_file():
            merged.update({k: v for k, v in dotenv_values(dotenv_path).items() if v is not None})
        merged.update(os.environ)
        env = merged

    secrets = Secrets(**{name: env.get(var) or None for name, var in _SECRET_ENV.items()})
    top = {k: raw[k] for k in _TOP_SCALARS if k in raw}
    return AppConfig(
        audio=_build(AudioConfig, raw.get("audio", {}), "audio."),
        turn=_build(TurnConfig, raw.get("turn", {}), "turn."),
        agent=_build(AgentConfig, raw.get("agent", {}), "agent."),
        paths=_build(PathsConfig, raw.get("paths", {}), "paths."),
        mcp_servers=_mcp_servers(raw.get("mcp_servers", {})),
        secrets=secrets,
        **top,
    )
