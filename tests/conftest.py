import os

import pytest

pytest_plugins = ["pytester"]

_GATES = {
    "live": ("VOICE_AGENT_LIVE", "set VOICE_AGENT_LIVE=1 (paid API)"),
    "pi": ("VOICE_AGENT_PI", "set VOICE_AGENT_PI=1 (needs a Raspberry Pi)"),
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, (var, reason) in _GATES.items():
        if os.environ.get(var) == "1":
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)
