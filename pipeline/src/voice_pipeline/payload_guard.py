"""Keeps a tool's JSON from being read aloud: text outside a JSON object passes."""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


class PayloadFilter:
    """Streaming filter for the text sent to the client. A `{` followed by `"` starts a JSON
    object that is dropped up to its matching `}` (strings are not parsed: braces inside one
    would end it early, which at worst lets a fragment through). A trailing `{` or `{ ` is held
    until the next piece shows what it is; `finish` hands back what was held."""

    def __init__(self) -> None:
        self.depth = 0
        self.held = ""
        self.dropped: list[str] = []
        self._current: list[str] = []

    def feed(self, text: str) -> str:
        data, self.held, out = self.held + text, "", []
        for i, ch in enumerate(data):
            if self.depth:
                self._current.append(ch)
                self.depth += (ch == "{") - (ch == "}")
                if not self.depth:
                    self._drop()
            elif ch == "{":
                after = data[i + 1 :].lstrip()
                if not after:
                    self.held = data[i:]
                    break
                if after[0] == '"':
                    self.depth, self._current = 1, ["{"]
                else:
                    out.append(ch)
            else:
                out.append(ch)
        return "".join(out)

    def finish(self) -> list[str]:
        """What is still held when the turn ends (a lone `{`); an unfinished object is dropped."""
        held, self.held = self.held, ""
        if self.depth:
            self.depth = 0
            self._drop()
        return [held] if held else []

    def _drop(self) -> None:
        payload, self._current = "".join(self._current), []
        self.dropped.append(payload[:200])
        log.warning("JSON in the reply text was not sent to speech: %s", payload[:120])


SOURCE_OPEN = "(source"
SOURCE_LIMIT = 400  # a parenthetical this long that never closes is dropped as it stands


class SourceFilter:
    """Streaming filter that drops a "(Source: ...)" or "(Sources: ...)" aside: the front agent
    names where a fact came from, but a web address in a spoken reply is noise and was read out
    ("edu dot ph") in the owner's session (2026-11-11). It says the publisher in words instead."""

    def __init__(self) -> None:
        self.held = ""
        self.dropping = False
        self._size = 0

    def feed(self, text: str) -> str:
        data, self.held, out = self.held + text, "", []
        i = 0
        while i < len(data):
            if self.dropping:
                end = data.find(")", i)
                self._size += (len(data) if end < 0 else end) - i
                if end >= 0 or self._size > SOURCE_LIMIT:
                    self.dropping, self._size = False, 0
                i = len(data) if end < 0 else end + 1
                continue
            start = data.find("(", i)
            if start < 0:
                out.append(data[i:])
                break
            out.append(data[i:start])
            rest = data[start:].lower()
            if rest.startswith(SOURCE_OPEN):
                self.dropping = True
                i = start + len(SOURCE_OPEN)
            elif SOURCE_OPEN.startswith(rest):  # could still become "(source": wait for more
                self.held = data[start:]
                break
            else:
                out.append("(")
                i = start + 1
        return re.sub(r" {2,}", " ", "".join(out))

    def finish(self) -> list[str]:
        held, self.held, self.dropping = self.held, "", False
        return [held] if held else []


_SOURCE = re.compile(r"\s*\(\s*sources?\b[^)]*\)", re.IGNORECASE)


def strip_sources(text: str) -> str:
    """`SourceFilter` for a whole reply (the logged text)."""
    return _SOURCE.sub("", text)
