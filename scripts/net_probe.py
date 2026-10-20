# ruff: noqa: E501  (wide table output lines)
"""Network probe for the voice-agent demo network (stdlib only: runs on the Pi, Termux, laptop).

  cloud   TCP connect and TLS handshake time to the vendor endpoints we might use
  lan     round trips and a 256 kbps audio-like stream between two of our own machines
          (answers: can the Pi reach the laptop over the phone hotspot, and how steady is it?)

Cloud (no audio is sent, only handshakes):
    python3 net_probe.py cloud [--count 30]

LAN: start the echo server on the laptop, then probe from the Pi (or the other way round):
    python3 net_probe.py serve --port 9099
    python3 net_probe.py lan --host 192.168.43.22 --port 9099

Add --json FILE to keep the numbers. Run it where the demo will run (same hotspot, same room).
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import statistics
import struct
import subprocess
import threading
import time
from collections.abc import Sequence

TARGETS = (
    "api.deepgram.com",
    "api.groq.com",
    "api.cartesia.ai",
    "api.anthropic.com",
    "ec2.ap-southeast-1.amazonaws.com",  # Singapore region
    "one.one.one.one",
)


def stats(values: list[float], failed: int = 0) -> dict[str, float]:
    if not values:
        return {"n": 0, "failed": failed}
    v = sorted(values)
    jitter = (
        statistics.mean(abs(a - b) for a, b in zip(values, values[1:], strict=False))
        if len(values) > 1
        else 0.0
    )
    return {
        "n": len(v),
        "failed": failed,
        "p50": v[len(v) // 2],
        "p95": v[min(len(v) - 1, int(len(v) * 0.95))],
        "max": v[-1],
        "jitter": jitter,
    }


def fmt(s: dict[str, float]) -> str:
    if not s.get("n"):
        return f"all {int(s['failed'])} attempts failed"
    return (
        f"p50 {s['p50']:.0f} / p95 {s['p95']:.0f} / max {s['max']:.0f} ms, jitter {s['jitter']:.1f} ms"
        f", failed {int(s['failed'])}"
    )


def probe_cloud(host: str, count: int) -> dict[str, dict[str, float]]:
    ctx = ssl.create_default_context()
    tcp: list[float] = []
    tls: list[float] = []
    failed = 0
    for _ in range(count):
        try:
            t0 = time.perf_counter()
            sock = socket.create_connection((host, 443), timeout=5)
            t1 = time.perf_counter()
            with ctx.wrap_socket(sock, server_hostname=host):
                t2 = time.perf_counter()
            tcp.append((t1 - t0) * 1000)
            tls.append((t2 - t1) * 1000)
        except OSError:
            failed += 1
        time.sleep(0.2)
    return {"tcp_connect": stats(tcp, failed), "tls_handshake": stats(tls, failed)}


def wifi_power_save() -> str:
    try:
        out = subprocess.run(["iw", "dev"], capture_output=True, text=True, timeout=3).stdout
        ifaces = [
            line.split()[1] for line in out.splitlines() if line.strip().startswith("Interface")
        ]
        res = []
        for i in ifaces:
            ps = subprocess.run(
                ["iw", "dev", i, "get", "power_save"], capture_output=True, text=True, timeout=3
            ).stdout.strip()
            res.append(f"{i}: {ps}")
        return "; ".join(res) or "no wifi interface found"
    except (OSError, subprocess.SubprocessError):
        return "iw not available"


def serve(port: int) -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen()
    print(f"echo server on 0.0.0.0:{port}; Ctrl-C to stop", flush=True)

    def handle(conn: socket.socket) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with conn:
            while data := conn.recv(4096):
                conn.sendall(data)

    while True:
        c, addr = srv.accept()
        print("client", addr[0], flush=True)
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def probe_lan(host: str, port: int, pings: int, seconds: float) -> dict[str, dict[str, float]]:
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as exc:
        print(f"UNREACHABLE: {host}:{port} ({exc}). If both devices are on the phone hotspot,")
        print("client isolation may be on; try a cable between them or a travel router.")
        return {"reachable": {"n": 0, "failed": 1}}
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(2)
    rtts: list[float] = []
    lost = 0
    for i in range(pings):
        t0 = time.perf_counter()
        try:
            sock.sendall(struct.pack("!Id", i, t0).ljust(32, b"x"))
            got = b""
            while len(got) < 32:
                got += sock.recv(32 - len(got))
            rtts.append((time.perf_counter() - t0) * 1000)
        except OSError:
            lost += 1
        time.sleep(0.02)
    # audio-like stream: 20 ms frames of 640 bytes (16 kHz, 16-bit) = 256 kbps, echoed back
    frame = 640
    delays: list[float] = []
    lost_frames = 0
    sock.settimeout(1)
    end = time.perf_counter() + seconds
    nxt = time.perf_counter()
    seq = 0
    while time.perf_counter() < end:
        nxt += 0.02
        sent = time.perf_counter()
        try:
            sock.sendall(struct.pack("!Id", seq, sent).ljust(frame, b"x"))
            got = b""
            while len(got) < frame:
                got += sock.recv(frame - len(got))
            delays.append((time.perf_counter() - sent) * 1000)
        except OSError:
            lost_frames += 1
        seq += 1
        time.sleep(max(0.0, nxt - time.perf_counter()))
    sock.close()
    return {"ping_rtt": stats(rtts, lost), "stream_256kbps_rtt": stats(delays, lost_frames)}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cloud")
    c.add_argument("--count", type=int, default=30)
    c.add_argument("--hosts", nargs="+", default=list(TARGETS))
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=9099)
    lan = sub.add_parser("lan")
    lan.add_argument("--host", required=True)
    lan.add_argument("--port", type=int, default=9099)
    lan.add_argument("--pings", type=int, default=200)
    lan.add_argument("--seconds", type=float, default=15)
    for sp in (c, lan):
        sp.add_argument("--json", help="write the numbers here")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        serve(args.port)
        return 0
    result: dict[str, object] = {
        "wifi_power_save": wifi_power_save(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print("wifi power save:", result["wifi_power_save"])
    if args.cmd == "cloud":
        for h in args.hosts:
            r = probe_cloud(h, args.count)
            result[h] = r
            print(
                f"{h:38} connect {fmt(r['tcp_connect'])}\n{'':38} tls     {fmt(r['tls_handshake'])}",
                flush=True,
            )
    else:
        r = probe_lan(args.host, args.port, args.pings, args.seconds)
        result["lan"] = r
        for k, v in r.items():
            print(f"{k:22} {fmt(v)}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(result, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
