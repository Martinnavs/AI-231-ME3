# ruff: noqa: E501
"""Live barge-in pilot on the Pi: you cue with a key, the main design's gate runs on the real room.

    python scripts/pi_live_pilot.py calibrate --in-device UACDemo --out-device CD002 --label calib
    python scripts/pi_live_pilot.py live      --in-device UACDemo --out-device CD002 --label "C vol75" --minutes 5
    python scripts/pi_live_pilot.py report out/pi/<live-folder> --calib out/pi/<calibrate-folder>
    python scripts/pi_live_pilot.py live --simulate --minutes 1      # no hardware or keyboard

Hold the hold key (default Right-Ctrl) from the moment you start talking until you stop; press the
noise key (default N) when you cough or knock. `calibrate` plays nothing and measures how long after
the key you actually start talking. The keyboard is grabbed while it runs (its keys do not type).
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from doc_chat import pi_check as pc  # noqa: E402
from doc_chat import pi_pilot as pp  # noqa: E402

FIXTURES = Path("tests/fixtures/pi_echo")


def key_code(spec: str) -> int:
    return int(spec) if spec.isdigit() else pp.KEY_CODES[spec.upper().removeprefix("KEY_")]


def resolve(spec: str | None, kind: str) -> Any:
    if spec is None or spec.isdigit():
        return None if spec is None else int(spec)
    import sounddevice as sd

    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if spec.lower() in d["name"].lower() and d[key] > 0:
            return i
    sys.exit(f"no {kind} device matching {spec!r}")


def volume() -> str | None:
    try:
        return (
            subprocess.run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout.strip()
            or None
        )
    except (OSError, subprocess.SubprocessError):
        return None


def sim_voice(rate: int, starts: list[float]) -> Any:
    """A 'person' for the dry run: a standard clip spoken at each start time."""
    speech = pc.resample(pc.read_wav(sorted(FIXTURES.glob("reply-*.wav"))[1]), pc.SR, rate)

    def talker(t: float) -> Any:
        out = np.zeros(rate // 50, np.float32)
        for s in starts:
            lo = int((t - s) * rate)
            if 0 <= lo < len(speech):
                chunk = speech[lo : lo + len(out)]
                out[: len(chunk)] += chunk
        return out

    return talker, len(speech) / rate


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("calibrate", "live"):
        p = sub.add_parser(name)
        p.add_argument("--label", default=name)
        p.add_argument("--in-device")
        p.add_argument("--out-device")
        p.add_argument("--rate", type=int)
        p.add_argument("--minutes", type=float, default=2.0 if name == "calibrate" else 5.0)
        p.add_argument("--hold-key", default="RIGHTCTRL")
        p.add_argument("--noise-key", default="N")
        p.add_argument("--kbd", help="/dev/input/event* (default: the first *-event-kbd in by-id)")
        p.add_argument("--no-grab", action="store_true")
        p.add_argument(
            "--no-keys", action="store_true", help="no keyboard (for a silent false-trigger soak)"
        )
        p.add_argument("--clips", default=str(FIXTURES))
        p.add_argument("--silero", default=pc.SILERO_DEFAULT)
        p.add_argument("--out", default="out/pi")
        p.add_argument("--seed", type=int, default=0)
        p.add_argument(
            "--confidence",
            type=float,
            default=0.7,
            help="Silero confidence the gate needs (spec: 0.7)",
        )
        p.add_argument(
            "--gate-s",
            type=float,
            default=0.25,
            help="seconds of speech before it interrupts (spec: 0.25)",
        )
        p.add_argument(
            "--rule",
            choices=("strict", "kofn"),
            default="strict",
            help="strict: --gate-s of unbroken speech; kofn: --k of the last --n windows",
        )
        p.add_argument("--k", type=int, default=7)
        p.add_argument("--n", type=int, default=10)
        p.add_argument(
            "--simulate", action="store_true", help="no hardware: fake sound card and scripted keys"
        )
    r = sub.add_parser("report")
    r.add_argument("folder", type=Path)
    r.add_argument("--calib", type=Path, help="a calibrate run: use its median key-to-voice offset")
    r.add_argument(
        "--offset", type=float, help="key-to-voice offset in seconds (overrides --calib)"
    )
    args = ap.parse_args()

    if args.cmd == "report":
        offset = args.offset
        if offset is None and args.calib:
            cal = pp.key_to_voice_offsets(args.calib)
            print("calibration:", json.dumps({k: v for k, v in cal.items() if k != "offsets_s"}))
            offset = cal.get("median_s", 0.0)
        res = pp.analyze(args.folder, offset or 0.0)
        res.pop("per_hold")
        print(json.dumps(res, indent=1))
        return 0

    seconds = args.minutes * 60
    live = args.cmd == "live"
    hold, noise = key_code(args.hold_key), key_code(args.noise_key)
    slug = re.sub(r"[^a-z0-9]+", "-", args.label.lower()).strip("-") or "run"
    folder = Path(args.out) / f"{time.strftime('%Y%m%d-%H%M%S')}-pilot-{args.cmd}-{slug}"

    if args.simulate:
        rate = 48000
        starts = [8.0 + 9.0 * i for i in range(max(int(seconds // 9) - 1, 1))]
        talker, dur = sim_voice(rate, starts)
        keys: pp.KeySource = pp.ScriptedKeys(
            [
                e
                for s in starts
                for e in (pp.KeyEvent(s - 0.1, hold, 1), pp.KeyEvent(s + dur, hold, 0))
            ]
        )
        factory = pc.simulated_stream_factory(echo_db=-200.0, talker=talker)
        in_dev = out_dev = None
        channels = 2
    else:
        import sounddevice as sd

        in_dev, out_dev = resolve(args.in_device, "input"), resolve(args.out_device, "output")
        info = sd.query_devices(out_dev, "output")
        rate = args.rate or int(info["default_samplerate"])
        channels = min(2, int(info["max_output_channels"]))
        if args.no_keys:
            keys = pp.ScriptedKeys([])
        else:
            kbd = args.kbd or next(iter(sorted(glob.glob("/dev/input/by-id/*-event-kbd"))), None)
            if not kbd:
                sys.exit(
                    "no keyboard found in /dev/input/by-id; pass --kbd /dev/input/eventN or --no-keys"
                )
            keys = pp.EvdevKeys(kbd, grab=not args.no_grab)
        factory = None

    cfg = pp.GateConfig(
        confidence=args.confidence, gate_s=args.gate_s, rule=args.rule, k=args.k, n=args.n
    )
    assistant = None
    if live:
        clips = {p.name: pc.read_wav(p) for p in sorted(Path(args.clips).glob("reply-*.wav"))}
        assistant = pp.Assistant(clips, rate, cfg=cfg, seed=args.seed)
    pilot = pp.Pilot(
        rate,
        in_dev,
        out_dev,
        channels,
        keys,
        args.silero,
        assistant,
        hold,
        noise,
        cfg=cfg,
        stream_factory=factory,
        threaded=not args.simulate,
    )
    print(
        f"{args.cmd}: {args.minutes:g} min. Hold {args.hold_key} while you talk, press {args.noise_key} for a cough or knock."
    )
    pilot.run(seconds)
    pilot.save(
        folder,
        {
            "label": args.label,
            "kind": args.cmd,
            "rate": rate,
            "seed": args.seed,
            "volume": volume(),
            "simulated": args.simulate,
            "minutes": args.minutes,
        },
    )
    print(f"saved to {folder}")
    if not live:
        cal = pp.key_to_voice_offsets(folder)
        print(
            "key-to-voice offset:", json.dumps({k: v for k, v in cal.items() if k != "offsets_s"})
        )
    else:
        res = pp.analyze(folder)
        res.pop("per_hold")
        print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
