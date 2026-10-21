# ruff: noqa: E501  (wide report lines)
"""Does the assistant's own voice trip the voice detector on this hardware?  (Raspberry Pi test)

Plays standard clips through a speaker and records the microphone at the same time. No speech
server, no internet, no models except the Silero VAD file. See docs/PI-TEST-PLAN.md for the runbook.

    python scripts/pi_echo_check.py devices
    python scripts/pi_echo_check.py selftest   --label "speakerphone A, volume 60%" --device "Speakerphone"
    python scripts/pi_echo_check.py doubletalk --label "speakerphone A, volume 60%" --device "Speakerphone"
    python scripts/pi_echo_check.py compare out/pi/*          # one table across runs
    python scripts/pi_echo_check.py selftest --simulate-echo-db -18      # no hardware: a fake sound card

Be quiet during `selftest`: the room must be empty of speech. Every run is written to
out/pi/<time>-<label>/ (run.json, run.md and the recordings).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from doc_chat import pi_check as pc  # noqa: E402

FIXTURES = Path("tests/fixtures/pi_echo")


def list_devices() -> int:
    import sounddevice as sd

    print(sd.query_devices())
    default = sd.default.device
    print(f"\ndefault (input, output): {default}")
    print("Pick the USB speakerphone by a piece of its name: --device Speakerphone")
    return 0


def resolve(spec: str | None, kind: str) -> Any:
    """A sounddevice index or name fragment -> index (None = the system default)."""
    if spec is None:
        return None
    if spec.isdigit():
        return int(spec)
    import sounddevice as sd

    key = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, d in enumerate(sd.query_devices()):
        if spec.lower() in d["name"].lower() and d[key] > 0:
            return i
    sys.exit(f"no {kind} device matching {spec!r}; run: python scripts/pi_echo_check.py devices")


def make_duplex(args: argparse.Namespace) -> pc.Duplex:
    if args.simulate_echo_db is not None:
        factory = pc.simulated_stream_factory(echo_db=args.simulate_echo_db)
        return pc.Duplex(48000, None, None, 2, factory)
    import sounddevice as sd

    in_dev = resolve(args.in_device or args.device, "input")
    out_dev = resolve(args.out_device or args.device, "output")
    info = sd.query_devices(out_dev, "output")
    rate = args.rate or int(info["default_samplerate"])
    return pc.Duplex(rate, in_dev, out_dev, min(2, int(info["max_output_channels"])))


def run_dir(args: argparse.Namespace, kind: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", args.label.lower()).strip("-") or "run"
    d = Path(args.out) / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def silero_path(args: argparse.Namespace) -> str | None:
    p = Path(args.silero)
    if not p.exists():
        print(
            f"NOTE: {p} not found, so only the loudness detector runs. "
            "Fetch it with: python scripts/fetch_silero_vad.py"
        )
        return None
    return str(p)


def render(run: dict[str, Any]) -> str:
    r = run["results"]
    lines = [
        f"# {run['kind']}: {run['label']}",
        "",
        f"- when: {run['when']}",
        f"- hardware: {json.dumps(run['hardware'])}",
    ]
    lines.append(f"- devices: {json.dumps(run['devices'])}")
    if run["kind"] == "selftest":
        lines += [
            f"- empty-room level: {r['floor_dbfs']} dBFS",
            f"- round trip (speaker -> air -> microphone): {r['round_trip_delay_ms']} ms",
            f"- its own voice above the room: median {r['echo_above_floor_db_median']} dB, worst block {r['echo_above_floor_db_worst']} dB",
            f"- buffer underruns while playing: {r['underruns_total']}",
            "",
            "| detector | clips where its own voice tripped it | first clip tripped | onsets in total |",
            "|---|---|---|---|",
        ]
        for d, s in r["summary"].items():
            lines.append(
                f"| {d} | {s['clips_with_a_false_onset']} | {s['first_clip_false_onset']} | {s['false_onsets_total']} |"
            )
        sil = [c["silero"].get("silero_250ms") for c in r["clips"] if c.get("silero")]
        if sil:
            lines += [
                "",
                f"Silero cost on this machine: {sil[0]['ms_per_window']} ms per 32 ms window "
                f"(real-time needs < 32). Speech windows during playback: "
                f"{[s['speech_windows'] for s in sil]}",
            ]
        lines += [
            "",
            "| clip | above room (median / p95 / first second, dB) | onsets after start (s) |",
            "|---|---|---|",
        ]
        for c in r["clips"]:
            lines.append(
                f"| {c['clip']} | {c['above_floor_db_median']} / {c['above_floor_db_p95']} / {c['above_floor_db_first_second']} | {c['onsets_after_playback_start_s']} |"
            )
    else:
        lines += [
            "",
            "| trial | its own voice tripped (before beep) | you, seconds after the beep |",
            "|---|---|---|",
        ]
        for t in r["trials"]:
            lines.append(
                f"| {t['trial']} | { {d: len(v) for d, v in t['before_beep'].items()} } | { {d: (v[:1] or '-') for d, v in t['after_beep'].items()} } |"
            )
        lines += [
            "",
            "Next: copy this folder to the laptop and run scripts/pi_double_talk_score.py on it.",
        ]
    return "\n".join(lines) + "\n"


def save(run: dict[str, Any], directory: Path) -> None:
    (directory / "run.json").write_text(json.dumps(run, indent=1))
    (directory / "run.md").write_text(render(run))
    print("\n" + render(run))
    print(f"saved to {directory}")


def base_run(
    args: argparse.Namespace, kind: str, duplex: pc.Duplex, results: dict[str, Any]
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": args.label,
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "hardware": pc.hardware_info(),
        "devices": {
            "in": str(duplex.in_device),
            "out": str(duplex.out_device),
            "rate": duplex.rate,
            "simulated_echo_db": args.simulate_echo_db,
        },
        "results": results,
    }


def selftest(args: argparse.Namespace) -> int:
    duplex = make_duplex(args)
    clips = sorted(Path(args.clips).glob("reply-*.wav"))
    if not clips:
        sys.exit(f"no reply-*.wav in {args.clips}")
    print("Stay quiet. Playing 6 clips through the speaker (about 2 minutes)...")
    d = run_dir(args, "selftest")
    res = pc.self_trigger_check(clips, duplex, d, silero_path(args), save=not args.no_save)
    save(base_run(args, "selftest", duplex, res), d)
    return 0


def doubletalk(args: argparse.Namespace) -> int:
    duplex = make_duplex(args)
    clips = sorted(Path(args.clips).glob("reply-*.wav"))
    prompts = json.loads((Path(args.clips) / "prompts.json").read_text())["prompts"]
    d = run_dir(args, "doubletalk")
    print("First, 4 seconds of quiet to measure the room. Do not speak.")
    floor_rec, _ = duplex.play_and_record(__import__("numpy").zeros(1, dtype="float32"), 4.0)
    floor = pc.rms(floor_rec[pc.SR // 2 :])
    pc.write_wav(d / "mic-floor.wav", floor_rec)
    res = pc.double_talk_trials(clips, prompts[: args.trials], duplex, d, silero_path(args), floor)
    res["floor_dbfs"] = round(pc.db(floor), 1)
    save(base_run(args, "doubletalk", duplex, res), d)
    return 0


def compare(args: argparse.Namespace) -> int:
    rows = []
    for p in args.runs:
        f = Path(p) / "run.json"
        if not f.exists():
            continue
        run = json.loads(f.read_text())
        if run["kind"] != "selftest":
            continue
        r = run["results"]
        s = r["summary"]
        rows.append(
            (
                run["label"],
                r["floor_dbfs"],
                r["echo_above_floor_db_median"],
                r["echo_above_floor_db_worst"],
                r["round_trip_delay_ms"],
                r["underruns_total"],
                s.get("silero_150ms", {}).get("clips_with_a_false_onset", "-"),
                s.get("silero_250ms", {}).get("clips_with_a_false_onset", "-"),
                s.get("energy", {}).get("clips_with_a_false_onset", "-"),
            )
        )
    print(
        "| condition | room dBFS | echo above room, median dB | worst dB | round trip ms | underruns | silero 150 ms tripped | silero 250 ms tripped | loudness tripped |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for row in rows:
        print("| " + " | ".join(str(x) for x in row) + " |")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("devices")
    for name in ("selftest", "doubletalk"):
        p = sub.add_parser(name)
        p.add_argument(
            "--label",
            default="run",
            help="what is being tested, e.g. 'speakerphone A, vol 60%%, hw AEC'",
        )
        p.add_argument(
            "--device",
            help="speaker AND microphone: an index or part of the name (use the same device for both!)",
        )
        p.add_argument("--in-device")
        p.add_argument("--out-device")
        p.add_argument("--rate", type=int, help="sample rate of the device (default: its own)")
        p.add_argument("--clips", default=str(FIXTURES))
        p.add_argument("--silero", default=pc.SILERO_DEFAULT)
        p.add_argument("--out", default="out/pi")
        p.add_argument("--no-save", action="store_true", help="do not keep the recordings")
        p.add_argument(
            "--simulate-echo-db",
            type=float,
            help="no hardware: a fake sound card with this echo level",
        )
        if name == "doubletalk":
            p.add_argument("--trials", type=int, default=6)
    c = sub.add_parser("compare")
    c.add_argument("runs", nargs="+")
    args = ap.parse_args()
    return {
        "devices": lambda a: list_devices(),
        "selftest": selftest,
        "doubletalk": doubletalk,
        "compare": compare,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
