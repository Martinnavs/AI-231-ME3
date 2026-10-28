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


async def test_ipv4_only_pins_the_address_family_and_alone_still_makes_a_session() -> None:
    import socket

    options = warm_http_options(None, ipv4_only=True)
    assert options is not None and options.aiohttp_client is not None
    connector = options.aiohttp_client.connector
    assert connector._family == socket.AF_INET
    assert connector._keepalive_timeout == 15.0  # aiohttp's default, as without a keep-alive
    await close_llm_sessions()
    dual = warm_http_options()
    assert dual is not None and dual.aiohttp_client.connector._family == socket.AF_UNSPEC
    await close_llm_sessions()
