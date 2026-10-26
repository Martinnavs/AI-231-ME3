"""The Gemini clients keep idle connections warm (docs/OPTIMIZATION-RESEARCH.md)."""

from voice_pipeline import agent
from voice_pipeline.agent import KEEPALIVE_S, close_llm_sessions, warm_http_options


async def test_the_session_keeps_idle_connections_for_the_configured_time() -> None:
    options = warm_http_options()
    assert options is not None and options.aiohttp_client is not None
    assert options.aiohttp_client.connector._keepalive_timeout == KEEPALIVE_S
    assert len(agent._sessions) == 1
    await close_llm_sessions()
    assert agent._sessions == [] and options.aiohttp_client.closed


async def test_without_a_keepalive_the_sdk_default_is_used() -> None:
    assert warm_http_options(None) is None and warm_http_options(0) is None
    assert agent._sessions == []
