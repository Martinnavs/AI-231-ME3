"""Round trip through the speech MCP server against the laptop's speech server, with timings.

    .venv-mcp/bin/python scripts/speech_mcp_check.py ws://<laptop ip>:8770   (on the Pi)

Speaks a sentence into a WAV, transcribes that WAV, prints voices, both times and the heard text.
"""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
SENTENCE = "The quick brown fox jumps over the lazy dog."


async def main(url: str) -> None:
    with tempfile.TemporaryDirectory() as media:
        args = [
            "-m",
            "voice_agent.mcp_servers.speech",
            "--stt-backend",
            "lan",
            "--tts-backend",
            "lan",
        ]
        args += [
            "--lan-url",
            url,
            "--lan-token-file",
            "~/.config/lan_speech_token",
            "--media-root",
            media,
        ]
        params = StdioServerParameters(
            command=sys.executable, args=args, env={"PYTHONPATH": str(ROOT / "src")}
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            voices = await session.call_tool("get_tts_voices", {})
            t0 = time.monotonic()
            await session.call_tool(
                "synthesize_speech_to_file", {"text": SENTENCE, "path": "out.wav"}
            )
            t1 = time.monotonic()
            heard = await session.call_tool("transcribe_audio_file", {"path": "out.wav"})
            t2 = time.monotonic()
    text = json.loads(heard.content[0].text)["text"]
    print(f"voices: {len(voices.content)}  tts: {t1 - t0:.2f} s  stt: {t2 - t1:.2f} s")
    print(f"heard: {text}")
    sys.exit(0 if "lazy dog" in text.lower() else 1)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
