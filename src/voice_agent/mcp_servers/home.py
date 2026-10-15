from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP

from voice_agent.config import ConfigError, PathsConfig, load_config

log = logging.getLogger("voice_agent.mcp.home")
mcp = FastMCP("home")

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TIMEOUT_S = 2.0


@dataclass
class _State:
    base_url: str = DEFAULT_BASE_URL
    state_dir: Path = field(default_factory=lambda: PathsConfig().state_dir)
    vocab: dict[str, Any] = field(default_factory=dict)
    client: httpx.AsyncClient | None = None


_state = _State()


def _format_error(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
        return response.text
    except Exception:
        return response.text


@contextlib.asynccontextmanager
async def _get_client() -> AsyncIterator[httpx.AsyncClient]:
    if _state.client is not None:
        yield _state.client
    else:
        async with httpx.AsyncClient(base_url=_state.base_url, timeout=TIMEOUT_S) as client:
            yield client


async def _fetch_vocab(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        res = await client.get("/api/vocab")
        res.raise_for_status()
        data = res.json()
        if isinstance(data, dict):
            _state.vocab = data
            return data
    except httpx.TimeoutException:
        raise ValueError("dashboard timed out") from None
    except (httpx.ConnectError, httpx.RequestError):
        raise ValueError(f"home dashboard unreachable at {_state.base_url}") from None
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"failed to fetch vocab: {exc}") from exc
    return {}


async def _get_vocab() -> dict[str, Any]:
    if _state.vocab:
        return _state.vocab
    async with _get_client() as client:
        return await _fetch_vocab(client)


async def _post_dashboard(path: str, body: dict[str, Any] | None = None) -> dict[str, object]:
    async with _get_client() as client:
        try:
            res = (
                await client.post(path, json=body) if body is not None else await client.post(path)
            )
        except httpx.TimeoutException:
            raise ValueError("dashboard timed out") from None
        except (httpx.ConnectError, httpx.RequestError):
            raise ValueError(f"home dashboard unreachable at {_state.base_url}") from None

        if res.status_code == 422:
            with contextlib.suppress(Exception):
                await _fetch_vocab(client)
            detail = _format_error(res)
            raise ValueError(f"dashboard validation error: {detail}")
        if res.status_code >= 400:
            if res.status_code < 500:
                with contextlib.suppress(Exception):
                    await _fetch_vocab(client)
            raise ValueError(f"dashboard error ({res.status_code}): {res.text}")

        data = res.json()
        assert isinstance(data, dict)
        return data


def _append_phone_log(action: str, contact: str, text: str) -> bool:
    try:
        path = _state.state_dir / "phone.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "t": time.time(),
            "action": action,
            "contact": contact,
            "text": text,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return True
    except OSError:
        return False


@mcp.tool()
async def get_home_state() -> dict[str, object]:
    """Get the full dashboard state snapshot."""
    async with _get_client() as client:
        try:
            res = await client.get("/api/state")
        except httpx.TimeoutException:
            raise ValueError("dashboard timed out") from None
        except (httpx.ConnectError, httpx.RequestError):
            raise ValueError(f"home dashboard unreachable at {_state.base_url}") from None

        if res.status_code >= 400:
            raise ValueError(f"dashboard error ({res.status_code}): {res.text}")

        data = res.json()
        assert isinstance(data, dict)
        return data


@mcp.tool()
async def control_lights(
    action: Literal["on", "off", "brighter", "dimmer", "set_color", "set_brightness"],
    color: str | None = None,
    level: int | None = None,
) -> dict[str, object]:
    """Control the lights (power, brightness, color)."""
    if action == "on":
        return await _post_dashboard("/api/light/power_on")
    if action == "off":
        return await _post_dashboard("/api/light/power_off")
    if action == "brighter":
        return await _post_dashboard("/api/light/increase")
    if action == "dimmer":
        return await _post_dashboard("/api/light/decrease")

    if action == "set_color":
        vocab = await _get_vocab()
        allowed_colors = vocab.get("light_colors", ["blue", "green", "red", "white"])
        if not color or not color.strip():
            raise ValueError(f"missing color; allowed values: {sorted(allowed_colors)}")
        if color not in allowed_colors:
            raise ValueError(f"invalid color {color!r}; allowed values: {sorted(allowed_colors)}")
        return await _post_dashboard("/api/light/set_color", {"color": color})

    if action == "set_brightness":
        vocab = await _get_vocab()
        allowed_levels = vocab.get("light_levels", [20, 60, 100])
        if level is None:
            raise ValueError(f"missing brightness level; allowed values: {sorted(allowed_levels)}")
        if level not in allowed_levels:
            raise ValueError(
                f"invalid brightness level {level!r}; allowed values: {sorted(allowed_levels)}"
            )
        return await _post_dashboard("/api/light/set_brightness", {"level": level})

    raise ValueError(f"unsupported action: {action}")


@mcp.tool()
async def set_thermostat(degrees: int) -> dict[str, object]:
    """Set the thermostat target temperature in degrees."""
    vocab = await _get_vocab()
    allowed_degrees = vocab.get("thermostat_degrees", [18, 22, 26])
    if degrees not in allowed_degrees:
        raise ValueError(
            f"invalid thermostat degrees {degrees!r}; allowed values: {sorted(allowed_degrees)}"
        )
    return await _post_dashboard("/api/thermostat", {"degrees": degrees})


@mcp.tool()
async def phone(
    action: Literal["call", "hang_up"],
    contact: str | None = None,
) -> dict[str, object]:
    """Simulate phone call actions (call a contact or hang up)."""
    if action == "call":
        if not contact or not contact.strip():
            raise ValueError("who should I call?")
        res = await _post_dashboard("/api/phone/call")
        logged = _append_phone_log(action="call", contact=contact.strip(), text="")
        if not logged:
            res["logged"] = False
        return res

    if action == "hang_up":
        return await _post_dashboard("/api/phone/hang_up")

    raise ValueError(f"unsupported action: {action}")


@mcp.tool()
async def send_message(
    contact: str,
    text: str,
) -> dict[str, object]:
    """Simulate sending a text message to a contact."""
    if not contact or not contact.strip():
        raise ValueError("who should I call?")
    if not text or not text.strip():
        raise ValueError("what should the message say?")

    res = await _post_dashboard("/api/phone/message")
    logged = _append_phone_log(action="send_message", contact=contact.strip(), text=text.strip())
    if not logged:
        res["logged"] = False
    return res


@mcp.resource("home://vocab")
async def vocab_resource() -> str:
    """Return the cached or freshly fetched home dashboard vocabulary."""
    if not _state.vocab:
        with contextlib.suppress(Exception):
            await _get_vocab()
    return json.dumps(_state.vocab, indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, force=True)
    parser = argparse.ArgumentParser(prog="voice_agent.mcp_servers.home")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--state-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        cfg = load_config()
        dashboard_url, state_dir = cfg.home_dashboard_url, cfg.paths.state_dir
    except ConfigError:
        dashboard_url, state_dir = DEFAULT_BASE_URL, PathsConfig().state_dir

    _state.base_url = (args.base_url or dashboard_url).rstrip("/")
    _state.state_dir = (args.state_dir or state_dir).expanduser()
    _state.client = None

    async def _try_startup_vocab() -> None:
        async with _get_client() as client:
            await _fetch_vocab(client)

    try:
        asyncio.run(_try_startup_vocab())
    except Exception as exc:
        log.warning("home dashboard unreachable at startup (%s): %s", _state.base_url, exc)

    log.info("home server: base_url=%s, state_dir=%s", _state.base_url, _state.state_dir)
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
