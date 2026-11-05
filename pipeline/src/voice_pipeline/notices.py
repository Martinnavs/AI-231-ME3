"""Spoken notices of delegated jobs that ended, read from the delegate server's notices.jsonl."""

from __future__ import annotations

import json
from pathlib import Path


class NoticeQueue:
    """The complete new lines of a JSON-lines file since the last `pop`.

    It starts at the end of an existing file, so notices from an earlier run are not repeated."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = path.stat().st_size if path.exists() else 0

    def pop_items(self) -> list[dict[str, str]]:
        """The new rows ({"job", "text"})."""
        if not self.path.exists():
            return []
        with self.path.open("rb") as source:
            source.seek(self.offset)
            data = source.read()
        complete = data[: data.rfind(b"\n") + 1]
        self.offset += len(complete)
        return [json.loads(line) for line in complete.splitlines() if line]

    def pop(self) -> list[str]:
        return [item["text"] for item in self.pop_items()]
