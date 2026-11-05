"""Keeps a tool's JSON from being read aloud: text outside a JSON object passes."""

from __future__ import annotations

import logging

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
