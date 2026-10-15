from dataclasses import fields
from pathlib import Path

import pytest

from voice_agent.config import (
    AgentConfig,
    AudioConfig,
    ConfigError,
    TurnConfig,
    load_config,
)

CONFIG = Path(__file__).resolve().parent.parent / "config" / "agent.toml"


def test_load_defaults() -> None:
    cfg = load_config(CONFIG, env={})
    assert cfg.audio == AudioConfig()
    assert cfg.turn == TurnConfig()
    assert cfg.agent == AgentConfig()
    assert set(cfg.mcp_servers) == {"speech", "home", "code"}
    assert cfg.mcp_servers["speech"].args[-1] == "voice_agent.mcp_servers.speech"
    assert cfg.secrets.anthropic_api_key is None
    assert cfg.location == "Manila"
    assert {f.name for f in fields(cfg.paths)} >= {"sandbox_root", "log_dir"}


def test_unknown_key_raises(tmp_path: Path) -> None:
    bad = tmp_path / "agent.toml"
    bad.write_text("[turn]\nvad_stopp_secs = 0.2\n")
    with pytest.raises(ConfigError, match="vad_stopp_secs"):
        load_config(bad, env={})


def test_missing_file_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope.toml"
    with pytest.raises(ConfigError, match="nope.toml"):
        load_config(missing, env={})


def test_secrets_from_env_and_masked() -> None:
    cfg = load_config(CONFIG, env={"ANTHROPIC_API_KEY": "sk-secret-1", "DEEPGRAM_API_KEY": "dg-2"})
    assert cfg.secrets.anthropic_api_key == "sk-secret-1"
    assert cfg.secrets.cartesia_api_key is None
    for text in (repr(cfg), str(cfg), repr(cfg.secrets)):
        assert "sk-secret-1" not in text
        assert "dg-2" not in text
        assert "'***'" in text


def test_dotenv_overlay_and_absent(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    cfg_path = tmp_path / "config" / "agent.toml"
    cfg_path.write_text(CONFIG.read_text())
    (tmp_path / ".env").write_text("CARTESIA_API_KEY=from-dotenv\n")
    assert load_config(cfg_path).secrets.cartesia_api_key == "from-dotenv"


def test_live_marker_skipped_by_default(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.live
        def test_paid():
            raise AssertionError("must not run")
        """
    )
    pytester.makeini("[pytest]\nmarkers =\n    live: x\n    pi: y\n")
    result = pytester.runpytest("-rs")
    result.assert_outcomes(skipped=1)
    assert "set VOICE_AGENT_LIVE=1 (paid API)" in result.stdout.str()
