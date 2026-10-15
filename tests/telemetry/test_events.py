from __future__ import annotations

import builtins
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from voice_agent.telemetry.events import (
    SCHEMA_VERSION,
    NullStageLogger,
    ReadStats,
    Stage,
    StageEvent,
    StageLogger,
    UnsupportedVersion,
    read_events,
)


def test_roundtrip(tmp_path: Path) -> None:
    """P1: emit every Stage, close, read_events returns them in order with equal fields."""
    log_path = tmp_path / "roundtrip.jsonl"
    stages = list(Stage)

    logger = StageLogger(log_path, run_id="rt_run")
    emitted_records: list[dict[str, Any]] = []

    for idx, stage in enumerate(stages):
        turn_id = idx + 1
        data = {"index": idx, "stage_str": stage.value}
        logger.emit(stage, turn_id=turn_id, **data)
        emitted_records.append({"stage": stage, "turn_id": turn_id, "data": data})

    logger.close()

    events, stats = read_events(log_path)

    assert len(events) == len(stages)
    assert stats == ReadStats(events=len(stages), malformed=0)

    for event, expected in zip(events, emitted_records, strict=True):
        assert isinstance(event, StageEvent)
        assert event.stage == expected["stage"]
        assert event.stage is expected["stage"]
        assert event.turn_id == expected["turn_id"]
        assert event.data == expected["data"]
        assert isinstance(event.t_mono_ns, int)
        assert event.t_mono_ns > 0
        assert isinstance(event.t_wall, float)
        assert event.t_wall > 0.0

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(stages) + 2  # opened + N stages + closed

    first_line = json.loads(lines[0])
    assert first_line["v"] == SCHEMA_VERSION
    assert first_line["event"] == "log_opened"
    assert first_line["run_id"] == "rt_run"
    assert "t_mono_ns" in first_line
    assert "t_wall" in first_line
    assert "host" in first_line

    last_line = json.loads(lines[-1])
    assert last_line == {"event": "log_closed", "dropped": 0}


def test_queue_full_drops_not_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """P2: max_queue=2, writer thread paused via monkeypatched file, 100 emits return immediately,

    dropped == 98, closing line reports it.
    """
    log_path = tmp_path / "queue_full.jsonl"
    write_paused = threading.Event()
    first_write_started = threading.Event()
    real_open = builtins.open

    class PausingFile:
        def __init__(self, f: Any) -> None:
            self._f = f

        def write(self, text: str) -> int:
            first_write_started.set()
            write_paused.wait()
            return self._f.write(text)

        def flush(self) -> None:
            self._f.flush()

        def close(self) -> None:
            self._f.close()

    def fake_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        f = real_open(file, mode, *args, **kwargs)
        if Path(file) == log_path and "x" in mode:
            return PausingFile(f)
        return f

    monkeypatch.setattr(builtins, "open", fake_open)

    logger = StageLogger(log_path, run_id="drop_test", max_queue=2)
    assert first_write_started.wait(timeout=2.0)

    t0 = time.perf_counter()
    for i in range(100):
        logger.emit(Stage.STT_FINAL, turn_id=1, count=i)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.5, f"100 emits took too long ({elapsed:.3f} s); caller was blocked"
    assert logger.dropped == 98

    write_paused.set()
    logger.close()

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    closing = json.loads(lines[-1])
    assert closing["event"] == "log_closed"
    assert closing["dropped"] == 98


def test_reader_skips_truncated_last_line(tmp_path: Path) -> None:
    """P3: reader skips truncated last line, counted in ReadStats.malformed."""
    log_path = tmp_path / "truncated.jsonl"

    with StageLogger(log_path, run_id="trunc_test") as logger:
        logger.emit(Stage.ENDPOINT, turn_id=1, source="stt")
        logger.emit(Stage.TURN_DONE, turn_id=1)

    # Append a truncated JSON line simulating abrupt power cut or crash
    with open(log_path, "a", encoding="utf-8") as f:
        f.write('{"v":1,"event":"stage","run_id":"trunc_test","stage":"stt_')

    events, stats = read_events(log_path)
    assert len(events) == 2
    assert stats == ReadStats(events=2, malformed=1)
    assert events[0].stage == Stage.ENDPOINT
    assert events[1].stage == Stage.TURN_DONE


def test_emit_is_cheap(tmp_path: Path) -> None:
    """P4: median of 10 000 emit calls < 50 µs."""
    log_path = tmp_path / "perf.jsonl"

    with StageLogger(log_path, run_id="perf", max_queue=20_000) as logger:
        durations_ns: list[int] = []
        for i in range(10_000):
            t0 = time.perf_counter_ns()
            logger.emit(Stage.STT_FINAL, turn_id=1, count=i)
            t1 = time.perf_counter_ns()
            durations_ns.append(t1 - t0)

    durations_ns.sort()
    median_us = (durations_ns[len(durations_ns) // 2]) / 1000.0
    assert median_us < 50.0, f"emit median was {median_us:.2f} µs (expected < 50 µs)"


def test_unknown_stage_rejected(tmp_path: Path) -> None:
    """P5 (part 1): unknown stage name passed to emit raises ValueError."""
    log_path = tmp_path / "unknown_stage.jsonl"

    with StageLogger(log_path, run_id="unknown_test") as logger:
        with pytest.raises(ValueError, match="Unknown stage"):
            logger.emit("not_a_stage", turn_id=1)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="Unknown stage"):
            logger.emit(12345, turn_id=1)  # type: ignore[arg-type]


def test_newer_version_rejected(tmp_path: Path) -> None:
    """P5 (part 2): reader meets a newer v, raises UnsupportedVersion."""
    log_path = tmp_path / "newer_version.jsonl"
    record = {
        "v": SCHEMA_VERSION + 1,
        "event": "stage",
        "run_id": "future",
        "stage": "stt_final",
        "turn_id": 1,
        "t_mono_ns": 1,
        "t_wall": 1.0,
        "data": {},
    }
    log_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(UnsupportedVersion, match="Unsupported schema version"):
        read_events(log_path)


def test_second_open_raises_file_exists(tmp_path: Path) -> None:
    """Two loggers write the same path: second raises FileExistsError."""
    log_path = tmp_path / "conflict.jsonl"

    with StageLogger(log_path, run_id="first"), pytest.raises(FileExistsError):
        StageLogger(log_path, run_id="second")


def test_non_json_values_coerced(tmp_path: Path) -> None:
    """data contains non-JSON values: coerced with default=str, never raises in hot path."""
    log_path = tmp_path / "non_json.jsonl"

    class CustomObj:
        def __str__(self) -> str:
            return "CustomObjValue"

    with StageLogger(log_path, run_id="coerce_test") as logger:
        logger.emit(
            Stage.TOOL_CALL,
            turn_id=1,
            path=Path("/tmp/example.txt"),
            custom=CustomObj(),
        )

    events, stats = read_events(log_path)
    assert len(events) == 1
    assert stats.events == 1
    assert stats.malformed == 0
    assert events[0].data["path"] == "/tmp/example.txt"
    assert events[0].data["custom"] == "CustomObjValue"


def test_null_stage_logger() -> None:
    """NullStageLogger emit is a no-op; for tests and disabled telemetry."""
    logger = NullStageLogger()
    assert logger.dropped == 0
    logger.emit(Stage.TURN_DONE, turn_id=1)
    assert logger.dropped == 0
    logger.close()

    with NullStageLogger(Path("/unused"), run_id="test") as ctx_logger:
        ctx_logger.emit(Stage.ENDPOINT, turn_id=2)
        assert ctx_logger.dropped == 0


def test_reader_skips_malformed_middle_line(tmp_path: Path) -> None:
    """Reader skips malformed middle lines and reports malformed count."""
    log_path = tmp_path / "malformed_middle.jsonl"
    lines = [
        json.dumps(
            {
                "v": 1,
                "event": "log_opened",
                "run_id": "r1",
                "t_mono_ns": 1,
                "t_wall": 1.0,
                "host": "test",
            }
        ),
        json.dumps(
            {
                "v": 1,
                "event": "stage",
                "run_id": "r1",
                "stage": "vad_speech_start",
                "turn_id": 1,
                "t_mono_ns": 2,
                "t_wall": 1.1,
                "data": {},
            }
        ),
        "{ corrupted json line",
        json.dumps(
            {
                "v": 1,
                "event": "stage",
                "run_id": "r1",
                "stage": "invalid_stage",
                "turn_id": 1,
                "t_mono_ns": 3,
                "t_wall": 1.2,
                "data": {},
            }
        ),
        json.dumps(
            {
                "v": 1,
                "event": "stage",
                "run_id": "r1",
                "stage": "turn_done",
                "turn_id": 1,
                "t_mono_ns": 4,
                "t_wall": 1.3,
                "data": {},
            }
        ),
        json.dumps({"event": "log_closed", "dropped": 0}),
    ]
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    events, stats = read_events(log_path)
    assert len(events) == 2
    assert stats == ReadStats(events=2, malformed=2)
    assert events[0].stage == Stage.VAD_SPEECH_START
    assert events[1].stage == Stage.TURN_DONE


def test_echo_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """echo_stdout=True echoes records to stdout."""
    log_path = tmp_path / "stdout.jsonl"
    with StageLogger(log_path, run_id="stdout_run", echo_stdout=True) as logger:
        logger.emit(Stage.TURN_DONE, turn_id=1, key="val")

    captured = capsys.readouterr()
    lines = [line for line in captured.out.strip().splitlines() if line]
    assert len(lines) == 3  # log_opened, stage, log_closed
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["event"] == "log_opened"
    assert parsed[1]["event"] == "stage"
    assert parsed[1]["stage"] == "turn_done"
    assert parsed[2]["event"] == "log_closed"


def test_emit_non_str_keys_dropped_not_raises(tmp_path: Path) -> None:
    """R22: dict with non-string keys in data is dropped and counted, never raises in emit."""
    log_path = tmp_path / "non_str_keys.jsonl"

    with StageLogger(log_path, run_id="non_str_keys") as logger:
        # Tuple key cannot be serialized by json.dumps even with default=str
        logger.emit(Stage.TOOL_CALL, turn_id=1, bad_map={(1, 2): "val"})
        assert logger.dropped == 1

        # Subsequent valid emit succeeds
        logger.emit(Stage.TURN_DONE, turn_id=1, ok="yes")

    events, stats = read_events(log_path)
    assert len(events) == 1
    assert events[0].stage == Stage.TURN_DONE
    assert stats.events == 1
    assert stats.malformed == 0

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    closing = json.loads(lines[-1])
    assert closing["event"] == "log_closed"
    assert closing["dropped"] == 1


def test_emit_circular_data_dropped_not_raises(tmp_path: Path) -> None:
    """R22: circular data structure in data is dropped and counted, never raises in emit."""
    log_path = tmp_path / "circular.jsonl"

    circular: dict[str, Any] = {}
    circular["self"] = circular

    with StageLogger(log_path, run_id="circular") as logger:
        logger.emit(Stage.TOOL_CALL, turn_id=1, bad_cycle=circular)
        assert logger.dropped == 1

        logger.emit(Stage.TURN_DONE, turn_id=1, ok="yes")

    events, stats = read_events(log_path)
    assert len(events) == 1
    assert events[0].stage == Stage.TURN_DONE
    assert stats.events == 1
    assert stats.malformed == 0

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    closing = json.loads(lines[-1])
    assert closing["event"] == "log_closed"
    assert closing["dropped"] == 1


def test_emit_failing_str_dropped_not_raises(tmp_path: Path) -> None:
    """R22: object whose __str__ raises is dropped and counted, never raises in emit."""
    log_path = tmp_path / "failing_str.jsonl"

    class FailingStr:
        def __str__(self) -> str:
            raise RuntimeError("str failed")

    with StageLogger(log_path, run_id="failing_str") as logger:
        logger.emit(Stage.TOOL_CALL, turn_id=1, bad_obj=FailingStr())
        assert logger.dropped == 1

        logger.emit(Stage.TURN_DONE, turn_id=1, ok="yes")

    events, stats = read_events(log_path)
    assert len(events) == 1
    assert events[0].stage == Stage.TURN_DONE


def test_close_with_full_queue_and_stalled_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R22: close() does not hang indefinitely when queue is full and writer is stalled."""
    log_path = tmp_path / "close_hang.jsonl"
    write_paused = threading.Event()
    first_write_started = threading.Event()
    real_open = builtins.open

    class PausingFile:
        def __init__(self, f: Any) -> None:
            self._f = f

        def write(self, text: str) -> int:
            first_write_started.set()
            write_paused.wait()
            return self._f.write(text)

        def flush(self) -> None:
            self._f.flush()

        def close(self) -> None:
            self._f.close()

    def fake_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        f = real_open(file, mode, *args, **kwargs)
        if Path(file) == log_path and "x" in mode:
            return PausingFile(f)
        return f

    monkeypatch.setattr(builtins, "open", fake_open)

    logger = StageLogger(log_path, run_id="close_hang_test", max_queue=2)
    assert first_write_started.wait(timeout=2.0)

    # Fill the queue (size 2) and drop subsequent emits
    for i in range(10):
        logger.emit(Stage.STT_FINAL, turn_id=1, count=i)

    # Use a small close timeout so the test runs quickly
    logger._close_timeout = 0.05

    close_thread = threading.Thread(target=logger.close)
    close_thread.start()
    close_thread.join(timeout=0.5)

    assert not close_thread.is_alive(), "close() hung while queue was full and writer was stalled"

    # Clean up paused writer thread
    write_paused.set()
    close_thread.join(timeout=2.0)
