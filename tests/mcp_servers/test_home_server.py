from __future__ import annotations

import importlib
import json
import stat
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from voice_agent.mcp_servers import home
from voice_agent.mcp_servers.home import (
    control_lights,
    get_home_state,
    phone,
    send_message,
    set_thermostat,
    vocab_resource,
)

MODULE = "voice_agent.mcp_servers.home"

STANDARD_VOCAB = {
    "light_colors": ["blue", "green", "red", "white"],
    "light_levels": [20, 60, 100],
    "thermostat_degrees": [18, 22, 26],
    "reminder_tasks": ["buy groceries", "call dentist"],
    "alarm_times": ["07:00", "08:00"],
    "timer_presets_s": [60, 300],
    "tracks": ["track1.mp3"],
}


@pytest.fixture(autouse=True)
def reset_home_state(tmp_path: Path):
    home._state.base_url = "http://127.0.0.1:8000"
    home._state.state_dir = tmp_path
    home._state.vocab = dict(STANDARD_VOCAB)
    home._state.client = None
    yield
    home._state.client = None
    home._state.vocab.clear()


@asynccontextmanager
async def _stdio_session(
    base_url: str = "http://127.0.0.1:8000", state_dir: Path | None = None
) -> AsyncIterator[ClientSession]:
    args = ["-m", MODULE, "--base-url", base_url]
    if state_dir is not None:
        args.extend(["--state-dir", str(state_dir)])
    params = StdioServerParameters(command=sys.executable, args=args)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        yield session


# P1: test_action_routes — httpx.MockTransport records method+path+body for every tool/action
@pytest.mark.asyncio
async def test_action_routes():
    recorded_requests: list[tuple[str, str, Any]] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            body = json.loads(request.content.decode("utf-8"))
        recorded_requests.append((request.method, request.url.path, body))

        if request.url.path == "/api/state":
            return httpx.Response(200, json={"lights": {"power": "on"}, "thermostat": 22})
        return httpx.Response(
            200,
            json={
                "changed": True,
                "message": f"ok from {request.url.path}",
                "state": {"status": "ok"},
            },
        )

    home._state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_handler), base_url="http://127.0.0.1:8000"
    )

    test_cases = [
        (get_home_state, (), {}, "GET", "/api/state", None),
        (control_lights, ("on",), {}, "POST", "/api/light/power_on", None),
        (control_lights, ("off",), {}, "POST", "/api/light/power_off", None),
        (control_lights, ("brighter",), {}, "POST", "/api/light/increase", None),
        (control_lights, ("dimmer",), {}, "POST", "/api/light/decrease", None),
        (
            control_lights,
            ("set_color",),
            {"color": "red"},
            "POST",
            "/api/light/set_color",
            {"color": "red"},
        ),
        (
            control_lights,
            ("set_brightness",),
            {"level": 60},
            "POST",
            "/api/light/set_brightness",
            {"level": 60},
        ),
        (set_thermostat, (22,), {}, "POST", "/api/thermostat", {"degrees": 22}),
        (phone, ("call",), {"contact": "Alice"}, "POST", "/api/phone/call", None),
        (phone, ("hang_up",), {}, "POST", "/api/phone/hang_up", None),
        (send_message, ("Bob", "hello"), {}, "POST", "/api/phone/message", None),
    ]

    for tool_fn, args, kwargs, exp_method, exp_path, exp_body in test_cases:
        recorded_requests.clear()
        res = await tool_fn(*args, **kwargs)
        assert len(recorded_requests) == 1, f"Failed for {tool_fn.__name__} {args} {kwargs}"
        method, path, body = recorded_requests[0]
        assert method == exp_method
        assert path == exp_path
        assert body == exp_body

        if exp_path != "/api/state":
            assert res["changed"] is True
            assert f"ok from {exp_path}" in str(res["message"])
            assert "state" in res


# P2: test_vocab_validation_rejects_without_http
@pytest.mark.asyncio
async def test_vocab_validation_rejects_without_http():
    http_called = False

    def fail_if_called(request: httpx.Request) -> httpx.Response:
        nonlocal http_called
        http_called = True
        raise AssertionError(f"Unexpected HTTP call to {request.url}")

    home._state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(fail_if_called), base_url="http://127.0.0.1:8000"
    )

    # 1. Invalid color
    with pytest.raises(ValueError) as exc_info:
        await control_lights(action="set_color", color="purple")
    assert "purple" in str(exc_info.value)
    assert "allowed values" in str(exc_info.value)
    assert not http_called

    # 2. Missing color
    with pytest.raises(ValueError) as exc_info:
        await control_lights(action="set_color", color="")
    assert "missing color" in str(exc_info.value)
    assert not http_called

    # 3. Invalid brightness level
    with pytest.raises(ValueError) as exc_info:
        await control_lights(action="set_brightness", level=50)
    assert "50" in str(exc_info.value)
    assert "allowed values" in str(exc_info.value)
    assert not http_called

    # 4. Missing brightness level
    with pytest.raises(ValueError) as exc_info:
        await control_lights(action="set_brightness", level=None)
    assert "missing brightness level" in str(exc_info.value)
    assert not http_called

    # 5. Invalid thermostat degrees
    with pytest.raises(ValueError) as exc_info:
        await set_thermostat(degrees=99)
    assert "99" in str(exc_info.value)
    assert "allowed values" in str(exc_info.value)
    assert not http_called

    # 6. Call without contact -> "who should I call?"
    with pytest.raises(ValueError) as exc_info:
        await phone(action="call", contact="")
    assert "who should I call?" in str(exc_info.value)
    assert not http_called

    # 7. Send message without contact -> "who should I call?"
    with pytest.raises(ValueError) as exc_info:
        await send_message(contact="", text="hello")
    assert "who should I call?" in str(exc_info.value)
    assert not http_called

    # 8. Send message with empty text -> "what should the message say?"
    with pytest.raises(ValueError) as exc_info:
        await send_message(contact="Alice", text="")
    assert "what should the message say?" in str(exc_info.value)
    assert not http_called


# P3: test_unreachable_dashboard_is_tool_error
@pytest.mark.asyncio
async def test_unreachable_dashboard_is_tool_error():
    # 1. Connection error
    def connect_error_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    home._state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(connect_error_handler), base_url="http://127.0.0.1:8000"
    )

    with pytest.raises(ValueError) as exc:
        await get_home_state()
    assert "home dashboard unreachable at http://127.0.0.1:8000" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        await control_lights("on")
    assert "home dashboard unreachable at http://127.0.0.1:8000" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        await set_thermostat(22)
    assert "home dashboard unreachable at http://127.0.0.1:8000" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        await phone("call", contact="Alice")
    assert "home dashboard unreachable at http://127.0.0.1:8000" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        await send_message("Bob", "hello")
    assert "home dashboard unreachable at http://127.0.0.1:8000" in str(exc.value)

    # 2. Timeout error (> 2 s)
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Request timed out", request=request)

    home._state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(timeout_handler), base_url="http://127.0.0.1:8000"
    )

    with pytest.raises(ValueError) as exc:
        await get_home_state()
    assert "dashboard timed out" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        await control_lights("off")
    assert "dashboard timed out" in str(exc.value)


# P4: test_against_real_me2_app
@pytest.mark.asyncio
async def test_against_real_me2_app():
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401

        me2_path = (
            Path(__file__).resolve().parents[2]
            / ".scratch"
            / "vendor"
            / "AI-222-Machine-Exercises"
            / "ME2"
            / "src"
        )
        if not me2_path.is_dir():
            pytest.skip(f"ME2 path not found: {me2_path}")
        if str(me2_path) not in sys.path:
            sys.path.insert(0, str(me2_path))

        me2_main = importlib.import_module("app.main")
        me2_state = importlib.import_module("app.state")
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.skip(f"ME2 app or dependencies not importable: {exc}")

    app_state = me2_state.AppState()
    app = me2_main.create_app(app_state)
    transport = httpx.ASGITransport(app=app)
    home._state.client = httpx.AsyncClient(transport=transport, base_url="http://testserver")

    # Turn lights off
    res = await control_lights("off")
    assert res["changed"] is False or "OFF" in str(res["message"])
    assert app_state.lights.state == me2_state.LightState.OFF


# P5: test_list_tools_over_stdio
@pytest.mark.asyncio
async def test_list_tools_over_stdio(tmp_path: Path):
    async with _stdio_session(state_dir=tmp_path) as s:
        tools = await s.list_tools()
        tool_names = sorted(t.name for t in tools.tools)
        assert tool_names == [
            "control_lights",
            "get_home_state",
            "phone",
            "send_message",
            "set_thermostat",
        ]

        resources = await s.list_resources()
        assert [str(r.uri) for r in resources.resources] == ["home://vocab"]

        res = await s.read_resource("home://vocab")  # type: ignore[arg-type]
        assert len(res.contents) > 0
        content_text = getattr(res.contents[0], "text", "")
        assert isinstance(content_text, str)


# P6: test_phone_log
@pytest.mark.asyncio
async def test_phone_log(tmp_path: Path):
    recorded_paths: list[str] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_paths.append(request.url.path)
        return httpx.Response(
            200,
            json={"changed": True, "message": "simulated call/message", "state": {}},
        )

    home._state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_handler), base_url="http://127.0.0.1:8000"
    )
    home._state.state_dir = tmp_path

    # 1. send_message("Anna", "running late")
    res = await send_message("Anna", "running late")
    assert res["changed"] is True
    assert "/api/phone/message" in recorded_paths

    phone_log_path = tmp_path / "phone.jsonl"
    assert phone_log_path.is_file()

    lines = [
        json.loads(line)
        for line in phone_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert lines[0]["contact"] == "Anna"
    assert lines[0]["text"] == "running late"
    assert lines[0]["action"] in ("send_message", "message")
    assert "t" in lines[0]
    assert isinstance(lines[0]["t"], (int, float))

    # 2. phone("call", "Bob")
    res_call = await phone("call", "Bob")
    assert res_call["changed"] is True
    assert "/api/phone/call" in recorded_paths

    lines = [
        json.loads(line)
        for line in phone_log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 2
    assert lines[1]["contact"] == "Bob"
    assert lines[1]["action"] == "call"
    assert lines[1]["text"] == ""

    # 3. Unwritable phone.jsonl
    # Make phone_log_path read-only
    phone_log_path.chmod(stat.S_IREAD)
    try:
        res_unwritable = await send_message("Charlie", "test")
        assert res_unwritable["changed"] is True
        assert res_unwritable.get("logged") is False
    finally:
        # restore permission for cleanup
        phone_log_path.chmod(stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.asyncio
async def test_dashboard_changed_false_and_422():
    # changed: false succeeds
    def false_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"changed": False, "message": "lights already off", "state": {"lights": "OFF"}},
        )

    home._state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(false_handler), base_url="http://127.0.0.1:8000"
    )
    res = await control_lights("off")
    assert res["changed"] is False
    assert res["message"] == "lights already off"

    # 422 refreshes vocab and raises validation detail error
    vocab_fetched = False

    def handler_422(request: httpx.Request) -> httpx.Response:
        nonlocal vocab_fetched
        if request.url.path == "/api/vocab":
            vocab_fetched = True
            return httpx.Response(200, json=STANDARD_VOCAB)
        return httpx.Response(
            422,
            json={"detail": "degree out of acceptable range"},
        )

    home._state.client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler_422), base_url="http://127.0.0.1:8000"
    )
    with pytest.raises(ValueError) as exc:
        await set_thermostat(18)
    assert "degree out of acceptable range" in str(exc.value)
    assert vocab_fetched


@pytest.mark.asyncio
async def test_vocab_resource():
    res = await vocab_resource()
    assert isinstance(res, str)
    data = json.loads(res)
    assert "light_colors" in data
