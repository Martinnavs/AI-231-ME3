"""One spoken answer from Gemini with Google Search grounding (the first choice of `web_answer`).

Gemini 3.1 flash-lite with the `google_search` tool answers a factual question in one request and
searches only when the answer could have changed (office holders, results, prices, "latest"):
settled facts come back in about 0.8 s, searched ones in 1.6-2.0 s, against 1.3 s for Tavily on
every question (25 questions, Manila, 2026-11-03: median 0.79 s against 1.33 s). Told today's date
and to search for anything that changes, it searched 5 of 8 changeable questions and none of the
settled ones; without that, "last NBA championship" and "next solar eclipse" came back stale.

Cost: Google bills each search query ($14 per 1,000 beyond 5,000 free a month, shared by Gemini 3
models, paid projects only). Every call is appended to a ledger and, when the month's searches
reach `MONTHLY_SEARCH_CAP`, the caller is told to use another backend. The key is read from
$GEMINI_API_KEY or ~/.secrets/google-ai-studio/api-key.txt, sent only as a header, never logged.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent"
)
KEY_FILE = Path(
    os.environ.get("GEMINI_KEY_FILE", Path.home() / ".secrets/google-ai-studio/api-key.txt")
)
LEDGER = Path(
    os.environ.get(
        "WEB_ANSWER_LEDGER", Path.home() / ".local/state/voice_agent/web_answer_ledger.jsonl"
    )
)
TIMEZONE = "Asia/Manila"
MONTHLY_SEARCH_CAP = 4500  # of the 5,000 free; a few hundred are left for other Gemini 3 uses
SYSTEM = (
    "You are a voice assistant. Today is {today}. Your answer is spoken aloud: at most two short "
    "sentences, plain words, no lists, no markdown. Search the web before answering whenever the "
    "answer could have changed since your training: office holders, sports results, prices and "
    "rates, records, rankings, events described as latest, last, next, current or new. Do not "
    "search for settled facts."
)
SYSTEM_RESEARCH = (
    "You are researching for a voice assistant. Today is {today}. Search the web, then answer in "
    "at most six short sentences of plain words, no lists, no markdown, naming the sources "
    "(site or publication) the facts come from. Say plainly what you could not find."
)


@dataclass
class Grounded:
    text: str
    searches: int  # Google Search queries Gemini ran (the billed unit)
    titles: list[str]  # titles of the pages it used, for checking the answer
    tokens: int | None
    seconds: float


def load_key() -> str | None:
    """The API key from the environment or the key file, or None (never printed or logged)."""
    key = os.environ.get("GEMINI_API_KEY")
    if key and key.strip():
        return key.strip()
    try:
        return "".join(KEY_FILE.read_text().split()) or None
    except OSError:
        return None


def plain(text: str) -> str:
    """Spoken text from a model reply: markdown marks and line breaks gone."""
    return " ".join(re.sub(r"[*_`#]+", "", text).split())


def parse(data: dict[str, Any]) -> tuple[str, int, list[str], int | None]:
    """(text, searches, page titles, tokens) of a generateContent response."""
    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text = plain("".join(str(p.get("text", "")) for p in parts))
    grounding = candidate.get("groundingMetadata") or {}
    titles = [
        str(c.get("web", {}).get("title", "")) for c in grounding.get("groundingChunks") or []
    ]
    tokens = (data.get("usageMetadata") or {}).get("totalTokenCount")
    return text, len(grounding.get("webSearchQueries") or []), titles, tokens


async def ask(
    http: httpx.AsyncClient,
    query: str,
    key: str,
    now: Callable[[], datetime] = lambda: datetime.now(ZoneInfo(TIMEZONE)),
    research: bool = False,
) -> Grounded | None:
    """Gemini's grounded answer to `query`, or None when it gave no text. Network and HTTP
    errors are raised (httpx.HTTPError) for the caller to fall back on."""
    body = {
        "systemInstruction": {
            "parts": [
                {
                    "text": (SYSTEM_RESEARCH if research else SYSTEM).format(
                        today=f"{now():%A, %B %-d, %Y}"
                    )
                }
            ]
        },
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "minimal"}},
    }
    started = time.perf_counter()
    response = await http.post(GEMINI_URL, json=body, headers={"x-goog-api-key": key})
    response.raise_for_status()
    text, searches, titles, tokens = parse(response.json())
    if not text:
        return None
    return Grounded(text, searches, titles, tokens, time.perf_counter() - started)


class Ledger:
    """What the grounded calls cost: one JSON line per call, and the month's search count."""

    def __init__(self, path: Path | None, cap: int = MONTHLY_SEARCH_CAP, month: str = "") -> None:
        self.path, self.cap = path, cap
        self.month = month or time.strftime("%Y-%m")
        self.searches = self._read()

    def _read(self) -> int:
        if self.path is None:
            return 0
        try:
            rows = [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]
        except (OSError, ValueError):
            return 0
        return sum(int(r.get("searches", 0)) for r in rows if str(r.get("month")) == self.month)

    @property
    def exhausted(self) -> bool:
        return self.searches >= self.cap

    def add(self, grounded: Grounded) -> None:
        self.searches += grounded.searches
        self._write(grounded.searches, grounded.tokens or 0, grounded.seconds)

    def add_failure(self, seconds: float, kind: str) -> None:
        """A grounded call that timed out or failed (`kind`): a row with no cost, so the
        latency tail can be counted."""
        self._write(0, 0, seconds, kind)

    def _write(self, searches: int, tokens: int, seconds: float, failed: str = "") -> None:
        if self.path is None:
            return
        row = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "month": self.month,
               "searches": searches, "tokens": tokens,
               "seconds": round(seconds, 2)}  # fmt: skip
        if failed:
            row["failed"] = failed  # type: ignore[assignment]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as sink:
                sink.write(json.dumps(row) + "\n")
        except OSError:
            pass  # a ledger that cannot be written must not lose the answer
