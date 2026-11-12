"""Y1: the harness' endpoints come from flags and the environment, not constants."""

from pathlib import Path

import pytest

from voice_pipeline import agent_server, agent_tools


@pytest.fixture(autouse=True)
def restore(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "APPS_ROOT",
        "APPS_HOST",
        "DELEGATE_STATE_DIR",
        "DELEGATE_CONFIG",
        "MUSIC_RESOLVER_URL",
    ):
        monkeypatch.setattr(agent_tools, name, getattr(agent_tools, name))


def test_configure_overrides_the_environment_values(tmp_path: Path) -> None:
    agent_tools.configure(
        apps_root=str(tmp_path / "apps"),
        apps_host="169.254.7.7",
        delegate_state_dir=str(tmp_path / "state"),
        delegate_config=str(tmp_path / "d.toml"),
    )
    assert tmp_path / "apps" == agent_tools.APPS_ROOT and agent_tools.APPS_HOST == "169.254.7.7"
    cmd = agent_tools.delegate_command()
    assert cmd[-4:] == [
        "--state-dir",
        str(tmp_path / "state"),
        "--config",
        str(tmp_path / "d.toml"),
    ]


def test_the_delegate_command_has_no_options_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools, "DELEGATE_STATE_DIR", None)
    monkeypatch.setattr(agent_tools, "DELEGATE_CONFIG", None)
    assert agent_tools.delegate_command() == [
        *agent_tools.ROOT_MODULE,
        "voice_agent.mcp_servers.delegate",
    ]


def test_the_mcp_python_replaces_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    monkeypatch.setenv("MCP_PYTHON", "/home/pi/repo/.venv/bin/python")
    reloaded = importlib.reload(agent_tools)
    try:
        assert reloaded.ROOT_MODULE == ["/home/pi/repo/.venv/bin/python", "-m"]
        assert reloaded.delegate_command()[:3] == [
            "/home/pi/repo/.venv/bin/python",
            "-m",
            "voice_agent.mcp_servers.delegate",
        ]
    finally:
        monkeypatch.delenv("MCP_PYTHON")
        importlib.reload(agent_tools)


def test_the_music_server_is_started_only_with_a_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MUSIC_SOURCE", "youtube")  # Spotify needs no resolver (test_music_wiring)
    monkeypatch.setattr(agent_tools, "MUSIC_RESOLVER_URL", None)
    assert agent_tools.music_command() is None
    monkeypatch.setattr(agent_tools, "MUSIC_RESOLVER_URL", "http://169.254.1.1:8796")
    cmd = agent_tools.music_command()
    assert cmd is not None and cmd[-2:] == ["--resolver-url", "http://169.254.1.1:8796"]
    required, optional = agent_tools.standard_servers(None)
    assert len(optional) == 2  # delegate and music; no Pi url


def test_pi_url_local_makes_the_device_tools_a_stdio_child() -> None:
    required, optional = agent_tools.standard_servers("local")
    assert len(optional) >= 2  # delegate and the local device server


def test_listening_beyond_loopback_needs_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAN_SPEECH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        agent_server.main(["--host", "169.254.7.7"])
    with pytest.raises(SystemExit):  # --no-token only on loopback
        agent_server.main(["--host", "169.254.7.7", "--no-token"])


def test_host_and_router_defaults_come_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run(*a: object, **k: object) -> int:
        captured["args"] = a
        return 0

    monkeypatch.setenv("LAN_SPEECH_TOKEN", "t")
    monkeypatch.setenv("AGENT_HOST", "169.254.7.7")
    monkeypatch.setenv("ROUTER_URL", "http://169.254.1.1:8089")
    monkeypatch.setattr(agent_server, "run", fake_run)
    assert agent_server.main(["--port", "8799", "--pi-url", "local"]) == 0
    args = captured["args"]
    assert isinstance(args, tuple)
    assert "http://169.254.1.1:8089" in args and "local" in args and args[-1] == "169.254.7.7"


def test_stdio_children_get_the_repo_src_on_their_path_when_nothing_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voice_pipeline import agent_tools

    monkeypatch.setattr(agent_tools, "MCP_PYTHON", "/pi/.venv-mcp/bin/python")
    client = agent_tools.stdio_server(["/pi/.venv-mcp/bin/python", "-m", "x"])
    env = client._server_params.env  # type: ignore[attr-defined]
    assert env is not None and env["PYTHONPATH"].endswith("/src")
    monkeypatch.setattr(agent_tools, "MCP_PYTHON", None)
    client = agent_tools.stdio_server(["uv", "run"])
    assert client._server_params.env is None  # type: ignore[attr-defined]
