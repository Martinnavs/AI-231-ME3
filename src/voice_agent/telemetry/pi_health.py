from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ThrottleFlags:
    raw: str  # e.g. "0x50005"
    under_voltage_now: bool
    freq_capped_now: bool
    throttled_now: bool
    soft_temp_limit_now: bool
    under_voltage_since_boot: bool
    freq_capped_since_boot: bool
    throttled_since_boot: bool
    soft_temp_limit_since_boot: bool


def decode_throttled(raw: str) -> ThrottleFlags:
    clean = raw.strip()
    if clean.startswith("throttled="):
        clean = clean[len("throttled=") :].strip()
    val = int(clean, 16)
    raw_str = clean if clean.lower().startswith("0x") else f"0x{val:x}"
    return ThrottleFlags(
        raw=raw_str,
        under_voltage_now=bool(val & (1 << 0)),
        freq_capped_now=bool(val & (1 << 1)),
        throttled_now=bool(val & (1 << 2)),
        soft_temp_limit_now=bool(val & (1 << 3)),
        under_voltage_since_boot=bool(val & (1 << 16)),
        freq_capped_since_boot=bool(val & (1 << 17)),
        throttled_since_boot=bool(val & (1 << 18)),
        soft_temp_limit_since_boot=bool(val & (1 << 19)),
    )


@dataclass(frozen=True)
class ProcSample:
    pid: int
    rss_mb: float
    cpu_pct: float  # % of one core since previous sample


@dataclass(frozen=True)
class HealthSample:
    t_mono_ns: int
    t_wall: float
    temp_c: float | None
    arm_mhz: float | None
    throttled: ThrottleFlags | None
    cpu_pct: float | None  # all cores, 0-100
    mem_available_mb: float | None
    proc: ProcSample | None


def _default_run_cmd(cmd: list[str], timeout_s: float) -> str | None:
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


class HealthSampler:
    def __init__(
        self,
        *,
        root: Path = Path("/"),
        pid: int | None = None,
        run_cmd: Callable[[list[str], float], str | None] = _default_run_cmd,
        interval_s: float = 1.0,
        clk_tck: int | None = None,
    ) -> None:
        self.root = root
        self.pid = pid
        self._run_cmd = run_cmd
        self.interval_s = interval_s
        self.overruns: int = 0
        self._warned_off_pi: bool = False

        if clk_tck is not None:
            self._clk_tck = clk_tck
        else:
            try:
                self._clk_tck = os.sysconf("SC_CLK_TCK")
            except (ValueError, AttributeError, OSError):
                self._clk_tck = 100

        self._prev_cpu_total: int | None = None
        self._prev_cpu_busy: int | None = None
        self._prev_proc_ticks: int | None = None
        self._prev_proc_t_mono: int | None = None

    def _read_temp(self) -> float | None:
        temp_path = self.root / "sys/class/thermal/thermal_zone0/temp"
        try:
            val_str = temp_path.read_text(encoding="utf-8").strip()
            return float(val_str) / 1000.0
        except (FileNotFoundError, OSError, ValueError):
            return None

    def _read_arm_mhz(self) -> float | None:
        freq_path = self.root / "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
        try:
            val_str = freq_path.read_text(encoding="utf-8").strip()
            return float(val_str) / 1000.0
        except (FileNotFoundError, OSError, ValueError):
            return None

    def _read_throttled(self) -> ThrottleFlags | None:
        try:
            out = self._run_cmd(["vcgencmd", "get_throttled"], 0.5)
            if out:
                return decode_throttled(out)
        except Exception:
            pass
        return None

    def _read_cpu_pct(self) -> float | None:
        stat_path = self.root / "proc/stat"
        cpu_line: str | None = None
        try:
            for line in stat_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("cpu "):
                    cpu_line = line
                    break
        except (FileNotFoundError, OSError):
            return None

        if cpu_line is None:
            return None

        try:
            fields = [int(x) for x in cpu_line.split()[1:]]
            if len(fields) < 4:
                return None
            idle_time = fields[3] + (fields[4] if len(fields) > 4 else 0)
            busy_time = (
                fields[0] + fields[1] + fields[2] + (sum(fields[5:8]) if len(fields) > 5 else 0)
            )
            total_time = idle_time + busy_time
        except (ValueError, IndexError):
            return None

        if self._prev_cpu_total is not None and self._prev_cpu_busy is not None:
            delta_total = total_time - self._prev_cpu_total
            delta_busy = busy_time - self._prev_cpu_busy
            if delta_total > 0:
                pct = (delta_busy / delta_total) * 100.0
                cpu_pct: float | None = max(0.0, min(100.0, pct))
            else:
                cpu_pct = 0.0
        else:
            cpu_pct = None

        self._prev_cpu_total = total_time
        self._prev_cpu_busy = busy_time
        return cpu_pct

    def _read_mem_available(self) -> float | None:
        mem_path = self.root / "proc/meminfo"
        try:
            for line in mem_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("MemAvailable:"):
                    parts = line.split()
                    kb = float(parts[1])
                    return kb / 1024.0
        except (FileNotFoundError, OSError, ValueError, IndexError):
            return None
        return None

    def _read_proc(self, t_mono_ns: int) -> ProcSample | None:
        if self.pid is None:
            return None

        status_path = self.root / f"proc/{self.pid}/status"
        stat_path = self.root / f"proc/{self.pid}/stat"

        try:
            status_text = status_path.read_text(encoding="utf-8")
            stat_text = stat_path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            self._prev_proc_ticks = None
            self._prev_proc_t_mono = None
            return None

        rss_mb = 0.0
        for line in status_text.splitlines():
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        rss_mb = float(parts[1]) / 1024.0
                    except ValueError:
                        rss_mb = 0.0
                break

        rparen = stat_text.rfind(")")
        if rparen == -1:
            return None

        rest = stat_text[rparen + 1 :].split()
        if len(rest) < 13:
            return None

        try:
            utime = int(rest[11])
            stime = int(rest[12])
            proc_ticks = utime + stime
        except ValueError:
            return None

        if self._prev_proc_ticks is not None and self._prev_proc_t_mono is not None:
            delta_ticks = proc_ticks - self._prev_proc_ticks
            delta_t_s = (t_mono_ns - self._prev_proc_t_mono) / 1_000_000_000.0
            if delta_t_s > 0:
                proc_cpu_s = delta_ticks / self._clk_tck
                cpu_pct = max(0.0, (proc_cpu_s / delta_t_s) * 100.0)
            else:
                cpu_pct = 0.0
            proc_sample = ProcSample(pid=self.pid, rss_mb=rss_mb, cpu_pct=cpu_pct)
        else:
            proc_sample = None

        self._prev_proc_ticks = proc_ticks
        self._prev_proc_t_mono = t_mono_ns
        return proc_sample

    def sample(self) -> HealthSample:
        t_mono_ns = time.monotonic_ns()
        t_wall = time.time()

        temp_c = self._read_temp()
        arm_mhz = self._read_arm_mhz()
        throttled = self._read_throttled()

        if not self._warned_off_pi:
            if temp_c is None and throttled is None:
                sys.stderr.write("warning: not on a Pi (no vcgencmd, no thermal zone)\n")
            self._warned_off_pi = True

        cpu_pct = self._read_cpu_pct()
        mem_available_mb = self._read_mem_available()
        proc = self._read_proc(t_mono_ns)

        return HealthSample(
            t_mono_ns=t_mono_ns,
            t_wall=t_wall,
            temp_c=temp_c,
            arm_mhz=arm_mhz,
            throttled=throttled,
            cpu_pct=cpu_pct,
            mem_available_mb=mem_available_mb,
            proc=proc,
        )

    def run(self, out: Path, stop: threading.Event) -> int:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)

        samples_written = 0
        t0 = time.monotonic()
        k = 0

        with out.open("w", encoding="utf-8") as f:
            opened_record = {
                "v": 1,
                "event": "health_opened",
                "host": socket.gethostname(),
                "pid": self.pid,
                "interval_s": self.interval_s,
            }
            f.write(json.dumps(opened_record) + "\n")
            f.flush()

            while not stop.is_set():
                sample = self.sample()
                record: dict[str, Any] = {
                    "v": 1,
                    "event": "health",
                    **asdict(sample),
                }
                if self.pid is None:
                    record.pop("proc", None)
                f.write(json.dumps(record) + "\n")
                f.flush()
                samples_written += 1

                k += 1
                target_time = t0 + k * self.interval_s
                now = time.monotonic()
                delay = target_time - now

                if delay > 0:
                    if stop.wait(delay):
                        break
                else:
                    elapsed_intervals = int((now - t0) / self.interval_s)
                    k_next = elapsed_intervals + 1
                    missed = k_next - k
                    if missed > 0:
                        self.overruns += missed
                        k = k_next
                    target_time = t0 + k * self.interval_s
                    delay = target_time - time.monotonic()
                    if delay > 0 and stop.wait(delay):
                        break

            closed_record = {
                "event": "health_closed",
                "samples": samples_written,
            }
            f.write(json.dumps(closed_record) + "\n")
            f.flush()

        return samples_written


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="voice_agent.telemetry.pi_health",
        description="Sample Raspberry Pi health metrics at regular intervals.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Path to write JSONL health samples to",
    )
    parser.add_argument(
        "--pid",
        type=int,
        default=None,
        help="Target process PID to monitor",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Sampling interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Duration in seconds to sample before stopping",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/"),
        help="Root filesystem path (default: /)",
    )

    args = parser.parse_args(argv)

    stop = threading.Event()

    def _sig_handler(sig: int, frame: Any) -> None:
        stop.set()

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, AttributeError):
        pass

    timer: threading.Timer | None = None
    if args.duration is not None:
        timer = threading.Timer(args.duration, stop.set)
        timer.daemon = True
        timer.start()

    sampler = HealthSampler(
        root=args.root,
        pid=args.pid,
        interval_s=args.interval,
    )
    try:
        sampler.run(args.out, stop)
    finally:
        if timer is not None:
            timer.cancel()

    return 0


if __name__ == "__main__":
    sys.exit(main())
