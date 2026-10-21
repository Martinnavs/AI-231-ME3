"""Gemini backend: where the key comes from, and that it never reaches output."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from doc_chat import gemini  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_API_KEY", "GEMINI_MODEL", "GOOGLE_AI_SECRETS_DIR"):
        monkeypatch.delenv(name, raising=False)


def test_not_configured_exits_without_printing_anything_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GOOGLE_AI_SECRETS_DIR", str(tmp_path))

    with pytest.raises(SystemExit) as exc:
        gemini.build_gemini_chat_model()

    assert exc.value.code == 2
    assert capsys.readouterr().err.strip() == "gemini backend not configured"


@pytest.mark.parametrize("filename", ["api_key.txt", "api-key.txt"])
def test_key_file_is_read_with_either_name_and_whitespace_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    (tmp_path / filename).write_text("  abc \n123\r\n")
    monkeypatch.setenv("GOOGLE_AI_SECRETS_DIR", str(tmp_path))

    assert gemini._read_key() == "abc123"


def test_file_wins_over_env_and_env_is_the_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_make(api_key: str, model_id: str, **kwargs: Any) -> str:
        captured.update(key=api_key, model=model_id, **kwargs)
        return "llm"

    monkeypatch.setattr(gemini, "make_chat_model", fake_make)
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    monkeypatch.setenv("GOOGLE_AI_SECRETS_DIR", str(tmp_path))
    assert gemini.build_gemini_chat_model() == "llm"
    assert captured["key"] == "env-key"  # no file yet

    (tmp_path / "api-key.txt").write_text("file-key\n")
    gemini.build_gemini_chat_model()
    assert captured["key"] == "file-key"


def test_model_precedence_argument_then_env_then_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(gemini, "make_chat_model", lambda key, model, **kw: seen.append(model))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_AI_SECRETS_DIR", str(tmp_path))

    gemini.build_gemini_chat_model()
    monkeypatch.setenv("GEMINI_MODEL", "from-env")
    gemini.build_gemini_chat_model()
    gemini.build_gemini_chat_model(model="from-arg")

    assert seen == [gemini.DEFAULT_MODEL, "from-env", "from-arg"]


def test_client_points_at_google_and_does_not_leak_the_key() -> None:
    llm = gemini.make_chat_model("secret-value", "m")

    assert "generativelanguage.googleapis.com" in str(llm.openai_api_base)  # type: ignore[attr-defined]
    assert "secret-value" not in repr(llm)


def test_gemini_is_accepted_by_the_cli_flags(capsys: pytest.CaptureFixture[str]) -> None:
    from doc_chat.__main__ import main

    assert main(["--text", "hi", "--no-docs"]) == 2  # still refused without any LLM
    assert "need --llm qwen or --llm gemini" in capsys.readouterr().err
