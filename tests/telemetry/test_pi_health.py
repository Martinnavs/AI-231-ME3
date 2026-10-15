from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

import pytest

from voice_agent.telemetry.pi_health import (
    HealthSample,
    HealthSampler,
    decode_throttled,
    main,
)


def test_decode_throttled() -> None:
    flags_0 = decode_throttled("0x0")
    assert flags_0.raw == "0x0"
    assert not flags_0.under_voltage_now
    assert not flags_0.freq_capped_now
    assert not flags_0.throttled_now
    assert not flags_0.soft_temp_limit_now
    assert not flags_0.under_voltage_since_boot
    assert not flags_0.freq_capped_since_boot
    assert not flags_0.throttled_since_boot
    assert not flags_0.soft_temp_limit_since_boot

    flags_50005 = decode_throttled("0x50005")
    assert flags_50005.raw == "0x50005"
    assert flags_50005.under_voltage_now
    assert not flags_50005.freq_capped_now
    assert flags_50005.throttled_now
    assert not flags_50005.soft_temp_limit_now
    assert flags_50005.under_voltage_since_boot
    assert not flags_50005.freq_capped_since_boot
    assert flags_50005.throttled_since_boot
    assert not flags_50005.soft_temp_limit_since_boot

    flags_prefixed = decode_throttled("throttled=0x50005")
    assert flags_prefixed.raw == "0x50005"
    assert flags_prefixed.under_voltage_now
    assert flags_prefixed.throttled_now


def test_sample_from_fake_tree(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parent.parent / "fixtures" / "pi_health"
    fake_root = tmp_path / "fake_root"
    shutil.copytree(fixture_dir, fake_root)

    sampler = HealthSampler(
        root=fake_root,
        pid=1234,
        run_cmd=lambda cmd, timeout: "throttled=0x0",
        interval_s=1.0,
    )

    s1 = sampler.sample()
    assert s1.cpu_pct is None
    assert s1.proc is None
    assert s1.temp_c == 61.5
    assert s1.arm_mhz == 1500.0
    assert s1.mem_available_mb == 2500.0
    assert s1.throttled is not None
    assert s1.throttled.raw == "0x0"

    stat_file = fake_root / "proc/stat"
    stat_file.write_text(
        "cpu  1200 0 600 9200 0 0 0 0 0 0\ncpu0 300 0 150 2300 0 0 0 0 0 0\n",
        encoding="utf-8",
    )

    s2 = sampler.sample()
    assert s2.cpu_pct is not None
    assert abs(s2.cpu_pct - 30.0) < 0.1
    assert s2.temp_c == 61.5
    assert s2.arm_mhz == 1500.0
    assert s2.mem_available_mb == 2500.0
    assert s2.proc is not None
    assert s2.proc.pid == 1234
    assert s2.proc.rss_mb == 50.0


def test_off_pi_fields_null(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir()

    sampler = HealthSampler(
        root=empty_root,
        run_cmd=lambda cmd, timeout: None,
    )
    s = sampler.sample()
    assert s.temp_c is None
    assert s.arm_mhz is None
    assert s.throttled is None
    assert s.cpu_pct is None
    assert s.mem_available_mb is None
    assert s.proc is None
    assert isinstance(s.t_mono_ns, int)
    assert isinstance(s.t_wall, float)

    err = capsys.readouterr().err
    assert "warning: not on a Pi" in err


def test_pid_exit_gives_null_proc(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parent.parent / "fixtures" / "pi_health"
    fake_root = tmp_path / "fake_root"
    shutil.copytree(fixture_dir, fake_root)

    sampler = HealthSampler(
        root=fake_root,
        pid=1234,
        run_cmd=lambda cmd, timeout: None,
    )

    s1 = sampler.sample()
    assert s1.proc is None

    proc_stat = fake_root / "proc/1234/stat"
    proc_stat.write_text(
        "1234 (sample_proc) S 1 1234 1234 0 0 0 0 0 0 0 110 60 "
        "0 0 20 0 1 0 100000 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n",
        encoding="utf-8",
    )
    s2 = sampler.sample()
    assert s2.proc is not None
    assert s2.proc.pid == 1234

    shutil.rmtree(fake_root / "proc/1234")

    s3 = sampler.sample()
    assert s3.proc is None


def test_run_writes_jsonl_and_closes(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parent.parent / "fixtures" / "pi_health"
    fake_root = tmp_path / "fake_root"
    shutil.copytree(fixture_dir, fake_root)

    out_file = tmp_path / "health.jsonl"
    stop = threading.Event()
    sampler = HealthSampler(
        root=fake_root,
        pid=1234,
        run_cmd=lambda cmd, timeout: "throttled=0x0",
        interval_s=0.01,
    )

    def _worker() -> None:
        sampler.run(out_file, stop)

    t = threading.Thread(target=_worker)
    t.start()

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if out_file.exists():
            lines = [
                line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
            if len(lines) >= 6:
                stop.set()
                break
        time.sleep(0.005)
    stop.set()
    t.join(timeout=3.0)

    lines = [line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 7

    first = json.loads(lines[0])
    assert first["event"] == "health_opened"
    assert first["v"] == 1
    assert first["interval_s"] == 0.01
    assert first["pid"] == 1234

    for line in lines[1:-1]:
        rec = json.loads(line)
        assert rec["event"] == "health"
        assert rec["v"] == 1
        assert "temp_c" in rec
        assert "t_mono_ns" in rec

    last = json.loads(lines[-1])
    assert last["event"] == "health_closed"
    assert last["samples"] == len(lines) - 2
    assert last["samples"] >= 5


def test_main_on_dev_host(tmp_path: Path) -> None:
    out_file = tmp_path / "dev_health.jsonl"
    code = main(["--out", str(out_file), "--duration", "0.05", "--interval", "0.01"])
    assert code == 0
    assert out_file.exists()

    lines = [line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 3
    first = json.loads(lines[0])
    assert first["event"] == "health_opened"
    assert first["v"] == 1

    last = json.loads(lines[-1])
    assert last["event"] == "health_closed"
    assert last["samples"] >= 1


def test_no_pid_omits_proc_in_jsonl(tmp_path: Path) -> None:
    out_file = tmp_path / "nopid.jsonl"
    stop = threading.Event()
    sampler = HealthSampler(root=tmp_path, pid=None, interval_s=0.01)

    def _worker() -> None:
        sampler.run(out_file, stop)

    t = threading.Thread(target=_worker)
    t.start()
    time.sleep(0.03)
    stop.set()
    t.join(timeout=2.0)

    lines = [line for line in out_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    health_lines = [json.loads(line) for line in lines if json.loads(line).get("event") == "health"]
    assert len(health_lines) >= 1
    for h in health_lines:
        assert "proc" not in h


def test_vcgencmd_hang_timeout(tmp_path: Path) -> None:
    def slow_cmd(cmd: list[str], timeout: float) -> str | None:
        return None

    sampler = HealthSampler(root=tmp_path, run_cmd=slow_cmd)
    s = sampler.sample()
    assert s.throttled is None


def test_sampling_overrun(tmp_path: Path) -> None:
    out_file = tmp_path / "overrun.jsonl"
    stop = threading.Event()
    sampler = HealthSampler(root=tmp_path, interval_s=0.01)

    orig_sample = sampler.sample

    def slow_sample() -> HealthSample:
        time.sleep(0.03)
        return orig_sample()

    sampler.sample = slow_sample  # type: ignore[method-assign]

    def _worker() -> None:
        sampler.run(out_file, stop)

    t = threading.Thread(target=_worker)
    t.start()
    time.sleep(0.08)
    stop.set()
    t.join(timeout=2.0)

    assert sampler.overruns > 0


def test_main_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "Sample Raspberry Pi health metrics" in out
    assert "--out" in out
