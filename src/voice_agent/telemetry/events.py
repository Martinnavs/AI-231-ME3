from __future__ import annotations

import contextlib
import json
import queue
import socket
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

SCHEMA_VERSION = 1


class Stage(StrEnum):
    VAD_SPEECH_START = "vad_speech_start"
    VAD_SPEECH_STOP = "vad_speech_stop"
    ENDPOINT = "endpoint"  # turn judged complete; data: {"source": "stt" | "vad_fallback" | "vad"}
    STT_INTERIM = "stt_interim"
    STT_FINAL = "stt_final"
    LLM_REQUEST = "llm_request"
    LLM_FIRST_TOKEN = "llm_first_token"
    LLM_CANCELLED = "llm_cancelled"  # speculative request abandoned (ticket 12)
    TOOL_CALL = "tool_call"
    DELEGATION_STARTED = "delegation_started"
    DELEGATION_FINISHED = "delegation_finished"
    TTS_REQUEST = "tts_request"
    TTS_FIRST_CHUNK = "tts_first_chunk"
    # data: {"clip": str, "duration_ms": int, "kind": "delegate"|"stall"|"notice"}
    FILLER_PLAYED = "filler_played"
    AUDIO_OUT_FIRST = "audio_out_first"  # first bot frame handed to the output transport
    BARGE_IN_DETECTED = "barge_in_detected"
    BARGE_IN_MUTE = "barge_in_mute"  # bot output actually stopped/flushed
    # possible barge-in: bot gain lowered (ticket 12); data: {"gain_db": float}
    BOT_DUCKED = "bot_ducked"
    BOT_RESTORED = "bot_restored"  # blip ended before the gate fired; gain ramped back to 0 dB
    TURN_DONE = "turn_done"


@dataclass(frozen=True)
class StageEvent:
    stage: Stage
    turn_id: int
    t_mono_ns: int  # time.monotonic_ns() at emit
    t_wall: float  # time.time() at emit
    data: Mapping[str, Any]


class StageLogger:
    def __init__(
        self,
        path: Path,
        *,
        run_id: str,
        echo_stdout: bool = False,
        max_queue: int = 10_000,
    ) -> None:
        self._path = Path(path)
        self._run_id = run_id
        self._echo_stdout = echo_stdout
        self._max_queue = max_queue

        self._dropped = 0
        self._dropped_lock = threading.Lock()
        self._closed = False
        self._close_lock = threading.Lock()

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation raises FileExistsError if path already exists
        self._file = open(self._path, "x", encoding="utf-8")  # noqa: SIM115

        opened_record = {
            "v": SCHEMA_VERSION,
            "event": "log_opened",
            "run_id": self._run_id,
            "t_mono_ns": time.monotonic_ns(),
            "t_wall": time.time(),
            "host": socket.gethostname(),
        }
        self._opened_line = json.dumps(opened_record, default=str)

        self._queue: queue.Queue[str | None] = queue.Queue(maxsize=max_queue)
        self._thread = threading.Thread(
            target=self._drain_queue,
            name=f"StageLogger-{self._run_id}",
            daemon=True,
        )
        self._thread.start()

    def emit(self, stage: Stage, turn_id: int, **data: Any) -> None:
        t_mono_ns = time.monotonic_ns()
        t_wall = time.time()

        if isinstance(stage, Stage):
            stage_enum = stage
        elif isinstance(stage, str):
            try:
                stage_enum = Stage(stage)
            except ValueError as exc:
                raise ValueError(f"Unknown stage: {stage!r}") from exc
        else:
            raise ValueError(f"Unknown stage: {stage!r}")

        with self._close_lock:
            if self._closed:
                with self._dropped_lock:
                    self._dropped += 1
                return

        record = {
            "v": SCHEMA_VERSION,
            "event": "stage",
            "run_id": self._run_id,
            "stage": stage_enum.value,
            "turn_id": turn_id,
            "t_mono_ns": t_mono_ns,
            "t_wall": t_wall,
            "data": data,
        }
        line = json.dumps(record, default=str)

        try:
            self._queue.put_nowait(line)
        except queue.Full:
            with self._dropped_lock:
                self._dropped += 1

    @property
    def dropped(self) -> int:
        with self._dropped_lock:
            return self._dropped

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        self._queue.put(None)
        self._thread.join()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _drain_queue(self) -> None:
        with contextlib.suppress(Exception):
            self._write_raw(self._opened_line)

        while True:
            try:
                item = self._queue.get()
            except Exception:
                break
            if item is None:
                self._queue.task_done()
                break
            with contextlib.suppress(Exception):
                self._write_raw(item)
            self._queue.task_done()

        try:
            closing_record = {
                "event": "log_closed",
                "dropped": self.dropped,
            }
            self._write_raw(json.dumps(closing_record, default=str))
        except Exception:
            pass
        finally:
            with contextlib.suppress(Exception):
                self._file.flush()
                self._file.close()

    def _write_raw(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()
        if self._echo_stdout:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()


class NullStageLogger(StageLogger):
    """emit() is a no-op; for tests and disabled telemetry."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        run_id: str = "null",
        echo_stdout: bool = False,
        max_queue: int = 10_000,
    ) -> None:
        self._path = Path(path) if path is not None else Path("/dev/null")
        self._run_id = run_id
        self._echo_stdout = echo_stdout
        self._max_queue = max_queue

    def emit(self, stage: Stage, turn_id: int, **data: Any) -> None:
        pass

    @property
    def dropped(self) -> int:
        return 0

    def close(self) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        pass


@dataclass(frozen=True)
class ReadStats:
    events: int
    malformed: int


class UnsupportedVersion(Exception):
    pass


def read_events(path: Path) -> tuple[list[StageEvent], ReadStats]:
    path = Path(path)
    events: list[StageEvent] = []
    malformed_count = 0

    if not path.exists():
        raise FileNotFoundError(f"Event log file not found: {path}")

    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue

            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                malformed_count += 1
                continue

            if not isinstance(record, dict):
                malformed_count += 1
                continue

            v = record.get("v")
            if v is not None and isinstance(v, (int, float)) and v > SCHEMA_VERSION:
                raise UnsupportedVersion(f"Unsupported schema version: {v} > {SCHEMA_VERSION}")

            event_type = record.get("event")
            if event_type == "stage":
                try:
                    raw_stage = record["stage"]
                    stage_enum = Stage(raw_stage)
                    turn_id = int(record["turn_id"])
                    t_mono_ns = int(record["t_mono_ns"])
                    t_wall = float(record["t_wall"])
                    data = record.get("data")
                    if not isinstance(data, dict):
                        data = {}
                    events.append(
                        StageEvent(
                            stage=stage_enum,
                            turn_id=turn_id,
                            t_mono_ns=t_mono_ns,
                            t_wall=t_wall,
                            data=data,
                        )
                    )
                except (KeyError, ValueError, TypeError):
                    malformed_count += 1
                    continue
            elif event_type in ("log_opened", "log_closed"):
                continue
            else:
                malformed_count += 1
                continue

    return events, ReadStats(events=len(events), malformed=malformed_count)
