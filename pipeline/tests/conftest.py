"""Shared test setup: no test may depend on the apps that happen to exist on this machine."""

import pytest

from voice_pipeline import call_policy


@pytest.fixture(autouse=True)
def _no_real_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    """known_apps reads ~/agent_sandbox/workspace/apps; a live session adds apps such as about_me
    there, which then changed unrelated verdicts. A test that needs apps sets its own."""
    monkeypatch.setattr(call_policy, "known_apps", lambda: [])
