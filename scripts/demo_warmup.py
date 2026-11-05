"""Demo-day warm-up (R6, docs/LATENCY.md): touch every server and tool before the audience.

    LAN_SPEECH_TOKEN=... uv run --group laptop python scripts/demo_warmup.py [--keepalive 60]

What it does, in order, and prints one line each (PASS / SLOW / FAIL, with timings):
  net     TLS handshake time to Gemini, Open-Meteo (forecast, place search) and Wikipedia
  pi      TCP connect to the Pi's speaker and device-tool ports
  speech  one TTS line and one short STT clip through the speech server (loads both models)
  agent   one request per tool through the agent server, so every connection, MCP session and
          Gemini pipeline has served a real request (read-only: no volume change; a home command
          is only recorded unless the agent server was started with --live)

Exit code 1 if a required check fails. `--keepalive S` then sends a tiny request every S seconds
until Ctrl-C (keeps the Gemini connections warm; a request that arrives while one is running waits
for it, about 1.5 s at worst). Open-Meteo drops idle connections after 3-5 s, so no keep-alive can
help that one; the first weather question after a pause costs about 1.2 s more.

Nothing is printed about the token. Run it after the servers are up and before the first spoken
session; a check that fails names the thing to start.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect

from doc_chat.speech import DocChatSpeechClient

ROOT = Path(__file__).resolve().parents[1]
CLIP = ROOT / "bench" / "mic_hard" / "01.wav"
HTTPS = (
    "generativelanguage.googleapis.com",
    "api.open-meteo.com",
    "geocoding-api.open-meteo.com",
    "en.wikipedia.org",
)
# (what it touches, prompt): one real request per tool, none with a side effect
TOUR = (
    ("fast path", "Turn on the lights."),
    ("LLM + smart_home", "Hello, can you turn the lights on?"),
    ("time", "What time is it in Tokyo?"),
    ("weather", "What's the weather in Baguio?"),
    ("wikipedia", "Who was John Steinbeck?"),
    ("Pi volume", "What's the speaker volume?"),
    ("plain LLM", "Tell me a short joke."),
)
SLOW_S = 4.0


def line(status: str, name: str, detail: str) -> None:
    print(f"{status:5} {name:<22} {detail}", flush=True)


def tls_ms(host: str) -> float:
    start = time.perf_counter()
    with socket.create_connection((host, 443), timeout=5) as raw:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(raw, server_hostname=host):
            pass
    return (time.perf_counter() - start) * 1000.0


def tcp_ms(host: str, port: int) -> float:
    start = time.perf_counter()
    with socket.create_connection((host, port), timeout=3):
        pass
    return (time.perf_counter() - start) * 1000.0


async def check_net() -> bool:
    ok = True
    for host in HTTPS:
        try:
            ms = await asyncio.to_thread(tls_ms, host)
            line("PASS" if ms < 800 else "SLOW", f"net {host}", f"{ms:.0f} ms")
        except OSError as exc:
            line("FAIL", f"net {host}", f"{type(exc).__name__}: {exc}")
            ok = False
    return ok


async def check_pi(host: str, ports: tuple[int, ...]) -> bool:
    ok = True
    for port in ports:
        try:
            ms = await asyncio.to_thread(tcp_ms, host, port)
            line("PASS", f"pi {host}:{port}", f"{ms:.0f} ms")
        except OSError as exc:
            line("FAIL", f"pi {host}:{port}", f"{type(exc).__name__}: start the Pi server")
            ok = False
    return ok


async def check_speech(url: str) -> bool:
    client = DocChatSpeechClient(url)
    try:
        await client.connect()
        start = time.perf_counter()
        await client.speak_text("Ready.")
        line("PASS", "speech tts", f"{(time.perf_counter() - start) * 1000:.0f} ms")
        start = time.perf_counter()
        heard = await client.hear_wav(CLIP)
        line("PASS", "speech stt", f"{(time.perf_counter() - start) * 1000:.0f} ms: {heard.text!r}")
        return True
    except (SystemExit, Exception) as exc:  # connect_ws exits the process when it cannot connect
        line("FAIL", "speech server", f"{type(exc).__name__}: is it running on {url}?")
        return False
    finally:
        await client.close()


async def ask(url: str, prompt: str) -> tuple[float, float, str]:
    """(first text s, total s, reply) for one request to the agent server."""
    start = time.perf_counter()
    first = 0.0
    text = ""
    token = os.environ.get("LAN_SPEECH_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with connect(url, open_timeout=10, additional_headers=headers) as ws:
        await ws.send(json.dumps({"messages": [{"role": "user", "content": prompt}]}))
        while True:
            event: dict[str, Any] = json.loads(await asyncio.wait_for(ws.recv(), 30))
            if "text" in event:
                first = first or time.perf_counter() - start
                text += event["text"]
            elif event.get("done"):
                break
            elif "error" in event:
                raise RuntimeError(str(event["error"]))
    return first, time.perf_counter() - start, text.strip()


async def check_agent(url: str) -> bool:
    ok = True
    for what, prompt in TOUR:
        try:
            first, total, text = await ask(url, prompt)
        except Exception as exc:
            line("FAIL", f"agent {what}", f"{type(exc).__name__}: {exc}")
            ok = False
            continue
        status = "PASS" if total < SLOW_S else "SLOW"
        line(status, f"agent {what}", f"first {first:.2f} s, total {total:.2f} s: {text[:60]!r}")
    return ok


async def keepalive(url: str, every: float) -> None:
    print(f"keep-alive every {every:.0f} s (Ctrl-C to stop)", flush=True)
    while True:
        await asyncio.sleep(every)
        try:
            _first, total, _ = await ask(url, "Say OK.")
            line("PASS", "keep-alive", f"{total:.2f} s")
        except Exception as exc:
            line("FAIL", "keep-alive", f"{type(exc).__name__}: {exc}")


async def main_async(args: argparse.Namespace) -> int:
    results = [
        await check_net(),
        await check_pi(args.pi_host, (args.pi_speaker_port, args.pi_tools_port)),
        await check_speech(args.speech),
        await check_agent(args.agent),
    ]
    ready = all(results)
    print("READY" if ready else "NOT READY: fix the FAIL lines above and run again", flush=True)
    if ready and args.keepalive > 0:
        await keepalive(args.agent, args.keepalive)
    return 0 if ready else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--speech", default="ws://127.0.0.1:8770")
    parser.add_argument("--agent", default="ws://127.0.0.1:8781")
    parser.add_argument("--pi-host", default="169.254.230.219")
    parser.add_argument("--pi-speaker-port", type=int, default=8780)
    parser.add_argument("--pi-tools-port", type=int, default=8790)
    parser.add_argument("--keepalive", type=float, default=0.0, metavar="SECONDS")
    args = parser.parse_args()
    if "LAN_SPEECH_TOKEN" not in os.environ:
        token_file = Path.home() / ".config" / "lan_speech_token"
        if token_file.exists():
            os.environ["LAN_SPEECH_TOKEN"] = token_file.read_text().strip()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
