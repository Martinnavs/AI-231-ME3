"""Answer a question from the passages of the loaded documents, with one Gemini request.

The server races this against the web lookup the router chose (agent_server.py). The small model
is never trusted: an answer counts only if its quote is copied verbatim from the passages and every
number and name in it appears in them (`check`).
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from voice_pipeline.agent import KEEPALIVE_S, MODEL, read_key, warm_http_options

DOC_ANSWER_TIMEOUT_S = 2.5
SYSTEM = (
    "You answer a spoken question ONLY from the passages, which come from the user's own "
    "documents. If the passages directly state the answer, reply in exactly two lines:\n"
    "ANSWER: <one or two short sentences>\n"
    "QUOTE: <one contiguous phrase of 3 to 12 words copied exactly from one passage; never use "
    "'...' or join parts; it states the answer>\n"
    "If the passages do not directly state the answer to THIS question, reply exactly: NONE\n"
    "Never use your own knowledge. A passage that only mentions the same name or word does not "
    "answer the question."
)
_COMMON_WORDS = (
    "the a an i it he she they his her their this that in on of and but for with from by to is "
    "was yes no not none answer"
)
COMMON = set(_COMMON_WORDS.split())
_REPLY = re.compile(r"ANSWER:\s*(.+?)\s*(?:\n|$)\s*QUOTE:\s*(.+)", re.S)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
Request = Callable[[str, str], Awaitable[str]]  # (system prompt, user content) -> reply text


def norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w' ]", " ", text.lower().replace("’", "'")).split())


def parse(reply: str) -> tuple[str, str] | None:
    """(answer, quote) of an `ANSWER:` + `QUOTE:` reply; None for NONE or anything else."""
    match = _REPLY.search(reply)
    if match is None:
        return None
    answer = match.group(1).strip()
    quote = match.group(2).strip().strip('"“” ')
    return (answer, quote) if answer and quote else None


def quote_ok(quote: str, passages: str) -> bool:
    if "..." in quote or "…" in quote:
        return False
    wanted = norm(quote)
    return bool(wanted) and f" {wanted} " in f" {norm(passages)} "


def names_numbers_ok(answer: str, passages: str) -> bool:
    """Every number and every capitalised word (not the first, not a common one) of the answer is
    in the passages."""
    seen = norm(passages)
    if any(norm(n) not in seen for n in _NUMBER.findall(answer)):
        return False
    for i, word in enumerate(answer.split()):
        core = re.sub(r"[^\w'\-]", "", word).removesuffix("'s")
        if i == 0 or not core or not core[0].isupper() or core.lower() in COMMON:
            continue
        if re.search(r"(?<![\w])" + re.escape(norm(core)) + r"(?![\w])", seen) is None:
            return False
    return True


def passages_of(result: Any) -> list[str]:
    """The passage texts of a `search_docs` result: the tool's list of {source, text}, or the text
    the MCP client hands back (the elements' JSON, one after the other). [] if there are none."""
    items: list[Any] = []
    if isinstance(result, str):
        decoder, pos = json.JSONDecoder(), 0
        while pos < len(result):
            if result[pos].isspace():
                pos += 1
                continue
            try:
                value, pos = decoder.raw_decode(result, pos)
            except ValueError:
                return []
            items.extend(value if isinstance(value, list) else [value])
    elif isinstance(result, list):
        items = result
    return [str(i["text"]) for i in items if isinstance(i, dict) and str(i.get("text", "")).strip()]


def user_content(passages: list[str], question: str) -> str:
    body = "\n\n".join(f"[{i + 1}] {p}" for i, p in enumerate(passages))
    return f"Passages:\n{body}\n\nQuestion: {question}"


@dataclass
class DocAnswer:
    status: str  # accepted | none | check_failed | error
    text: str | None = None
    rejected: dict[str, str] | None = (
        None  # check_failed: the answer, its quote and the failed check
    )


def judge(reply: str, passages: list[str]) -> DocAnswer:
    found = parse(reply)
    if found is None:
        return DocAnswer("none" if reply.strip().upper().startswith("NONE") else "error")
    answer, quote = found
    joined = "\n\n".join(passages)
    if not quote_ok(quote, joined):
        failed = "quote"
    elif not names_numbers_ok(answer, joined):
        failed = "names_numbers"
    else:
        return DocAnswer("accepted", answer)
    return DocAnswer("check_failed", rejected={"answer": answer, "quote": quote, "failed": failed})


async def answer(request: Request, passages: list[str], question: str) -> DocAnswer:
    """One request, then the checks. A failed request is `error`, never an exception."""
    if not passages:
        return DocAnswer("none")
    try:
        reply = await request(SYSTEM, user_content(passages, question))
    except Exception:
        return DocAnswer("error")
    return judge(reply, passages)


async def gemini_request(
    keepalive_s: float | None = KEEPALIVE_S, ipv4_only: bool = False
) -> Request:
    """A request function over one Gemini client that stays warm between turns. Call it once, inside
    the running event loop; the key is read here and never logged."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=read_key("google-ai-studio"), http_options=warm_http_options(keepalive_s, ipv4_only)
    )
    thinking = types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)

    async def request(system: str, content: str) -> str:
        config = types.GenerateContentConfig(system_instruction=system, thinking_config=thinking)
        reply = await client.aio.models.generate_content(
            model=MODEL, contents=content, config=config
        )
        return reply.text or ""

    return request
