# ruff: noqa: E501  (wide report lines)
"""Read a recorded doc_chat run (--record DIR) and say how it went and where to tune.

    uv run --group laptop python scripts/session_report.py out/sessions/0521-1430
    uv run --group laptop python scripts/session_report.py DIR --init-truth   # then edit truth.tsv
    uv run --group laptop python scripts/session_report.py DIR --write        # also save DIR/report.md

Sections: the run, latency by stage (and what dominates), recognition (name repairs, accuracy if
you wrote down what you really said in DIR/truth.tsv), the turn-end model's scores (what each
--add-threshold would have asked), and a short list of what to try next.
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

STAGES = (
    ("pause_ms", "your pause before the turn was ended"),
    ("stt_server_ms", "speech server: recognise + repair"),
    ("llm_first_token_ms", "language model: first word"),
    ("tts_first_audio_ms", "speech synthesis: first audio"),
    ("speech_end_to_first_audio_ms", "TOTAL: you stop -> you hear the answer"),
)
BINS = (0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.01)


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(int(round(q * (len(s) - 1))), len(s) - 1)]


def load(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    turns_path = directory / "turns.jsonl" if directory.is_dir() else directory
    info_path = turns_path.parent / "session.json"
    info = json.loads(info_path.read_text()) if info_path.exists() else {}
    rows = [json.loads(line) for line in turns_path.read_text().splitlines() if line.strip()]
    return info, rows


def init_truth(directory: Path, rows: list[dict[str, Any]]) -> None:
    target = directory / "truth.tsv"
    if target.exists():
        print(f"{target} exists; not overwritten")
        return
    lines = [
        f"{r['turn']}\t{r.get('heard') or r.get('question', '')}" for r in rows if r.get("heard")
    ]
    target.write_text("\n".join(lines) + "\n")
    print(f"wrote {target}: correct each line to what you actually said, then run the report again")


def section_run(info: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    answered = [r for r in rows if r.get("answer_words", 0) > 0 and not r.get("error")]
    clar = [r for r in rows if r.get("clarification")]
    failed = [r for r in rows if r.get("error") or r.get("llm_error")]
    print("## The run")
    if info:
        print(
            f"- started {info.get('started')}, code {info.get('git_commit')} on {info.get('git_branch')}"
            f"{' (uncommitted changes)' if info.get('git_dirty') else ''}"
        )
        print(
            f"- model: {info.get('llm')} {info.get('llm_model') or ''}; note: {info.get('note') or '-'}"
        )
    print(
        f"- {len(rows)} turns logged: {len(answered)} answered, {len(clar)} clarifying questions, {len(failed)} with an error"
    )
    asked = [r for r in rows if r.get("asked_to_add")]
    if asked:
        added = [r for r in asked if r.get("added")]
        print(
            f"- asked 'anything to add?' {len(asked)} times; the user added something {len(added)} times"
        )
    print()


def section_latency(rows: list[dict[str, Any]]) -> dict[str, float]:
    print("## Latency by stage (ms)")
    print("| stage | n | p50 | p95 | max |")
    print("|---|---|---|---|---|")
    p50s: dict[str, float] = {}
    for key, label in STAGES:
        vals = [
            float(r[key]) for r in rows if r.get(key) is not None and r.get("answer_words", 0) > 0
        ]
        if not vals:
            continue
        p50s[key] = statistics.median(vals)
        print(
            f"| {label} | {len(vals)} | {p50s[key]:.0f} | {pct(vals, 0.95):.0f} | {max(vals):.0f} |"
        )
    parts = {k: v for k, v in p50s.items() if k != "speech_end_to_first_audio_ms"}
    if parts:
        big = max(parts, key=lambda k: parts[k])
        print(
            f"\nThe biggest single stage (p50) is **{dict(STAGES)[big]}** at {parts[big]:.0f} ms."
        )
    print()
    return p50s


def section_interrupts(rows: list[dict[str, Any]]) -> None:
    cut = [r for r in rows if r.get("interrupted") == "resumed"]
    barge = [r for r in rows if r.get("interrupted") == "barge_in"]
    negative = [r for r in rows if r.get("pause_ms") is not None and r["pause_ms"] < 0]
    print("## Interruptions")
    turns = len([r for r in rows if r.get("interrupted") is None]) + len(cut) + len(barge)
    print(
        f"- you talked over the answer: {len(barge)} times; you went on after being cut off: {len(cut)} times (of {turns} turns)"
    )
    if negative:
        print(
            f"- {len(negative)} turns were ended while you were still speaking (negative pause): "
            + ", ".join(str(r["turn"]) for r in negative[:12])
        )
    stops = [
        float(r["cancel_to_silence_ms"])
        for r in cut + barge
        if r.get("cancel_to_silence_ms") is not None
    ]
    if stops:
        print(
            f"- it went quiet {statistics.median(stops):.0f} ms (p50), {max(stops):.0f} ms (max) after it heard you; add the 0.3 s you must be speaking first"
        )
    after = [
        float(r["interrupt_after_answer_start_ms"])
        for r in barge
        if r.get("interrupt_after_answer_start_ms") is not None
    ]
    if after:
        print(f"- you cut in {statistics.median(after) / 1000:.1f} s (p50) into its answer")
    print()


def section_recognition(directory: Path, rows: list[dict[str, Any]]) -> float | None:
    print("## Recognition")
    rungs: dict[str, int] = {}
    for r in rows:
        if r.get("repair_rung"):
            rungs[r["repair_rung"]] = rungs.get(r["repair_rung"], 0) + 1
    if rungs:
        print("- name-repair rungs: " + ", ".join(f"{k} {v}" for k, v in sorted(rungs.items())))
    changed = [
        r for r in rows if r.get("raw_text") and r.get("heard") and r["raw_text"] != r["heard"]
    ]
    for r in changed[:10]:
        print(
            f"- turn {r['turn']}: recogniser said {r['raw_text']!r} -> repaired to {r['heard']!r}"
        )
    truth_path = directory / "truth.tsv"
    wer: float | None = None
    if truth_path.exists():
        import score_tries as st

        sc = st.load_compare()
        truth = {}
        for line in truth_path.read_text().splitlines():
            if "\t" in line:
                n, text = line.split("\t", 1)
                truth[int(n)] = text
        edits = raw_edits = words = 0
        bad: list[tuple[int, str, str]] = []
        for r in rows:
            if r["turn"] in truth and r.get("heard"):
                ref = sc.norm_words(truth[r["turn"]])
                e = sc.word_edits(ref, sc.norm_words(r["heard"]))
                edits += e
                raw_edits += sc.word_edits(ref, sc.norm_words(r.get("raw_text") or r["heard"]))
                words += len(ref)
                if e:
                    bad.append((r["turn"], truth[r["turn"]], r["heard"]))
        if words:
            wer = edits / words
            print(
                f"- word error vs what you said ({words} words in {len(truth)} labelled turns): **{wer:.1%}** (before name repair {raw_edits / words:.1%})"
            )
            for n, said, got in bad[:10]:
                print(f"  - turn {n}: said {said!r}, got {got!r}")
    else:
        print(
            "- no truth.tsv yet: run with --init-truth, correct it, and rerun to get a word error rate"
        )
    print()
    return wer


def section_turn_end(rows: list[dict[str, Any]]) -> None:
    scored = [r for r in rows if r.get("unfinished_score") is not None]
    if not scored:
        print("## Turn-end model\n- no scores logged (run with --ask-to-add or --log-turn-score)\n")
        return
    scores = [float(r["unfinished_score"]) for r in scored]
    print("## Turn-end model (P that you had finished; low = sounds unfinished)")
    print("| score | turns |")
    print("|---|---|")
    for lo, hi in zip(BINS, BINS[1:], strict=False):
        print(f"| {lo:.1f}-{min(hi, 1.0):.1f} | {sum(lo <= s < hi for s in scores)} |")
    print("\nWhat each threshold would ask (of the scored turns):")
    for thr in (0.2, 0.3, 0.4, 0.5):
        print(
            f"- {thr}: {sum(s < thr for s in scores)} of {len(scores)} ({sum(s < thr for s in scores) / len(scores):.0%})"
        )
    low = sorted(scored, key=lambda r: r["unfinished_score"])[:8]
    print("\nLowest scores (what it thought was unfinished):")
    for r in low:
        extra = ""
        if r.get("asked_to_add"):
            extra = (
                f", asked; you added {r['added']!r}" if r.get("added") else ", asked; nothing added"
            )
        print(
            f"- {r['unfinished_score']:.2f} turn {r['turn']}: {r.get('heard') or r.get('question')!r}{extra}"
        )
    print()


def hints(
    p50: dict[str, float], rows: list[dict[str, Any]], wer: float | None, info: dict[str, Any]
) -> None:
    print("## Where to tune")
    out: list[str] = []
    llm = p50.get("llm_first_token_ms")
    if llm and llm > 600:
        out.append(
            f"- The language model's first word takes {llm:.0f} ms (p50). It is the largest fixed cost: try a smaller/faster model (`--llm-model`), or `--llm qwen` on the class server, and check the network path."
        )
    pause = p50.get("pause_ms")
    hold = (info.get("settings") or {}).get("vad_hold")
    if pause and hold and pause > hold * 1000 * 1.5:
        out.append(
            f"- Your pause before the turn ended is {pause:.0f} ms against a hold of {hold * 1000:.0f} ms: the pause detector is firing late (noisy room? raise the speaking level or check `noise_floor` in turns.jsonl)."
        )
    elif pause and pause > 450:
        out.append(
            f"- {pause:.0f} ms of every turn is the wait for your pause; `--vad-hold` is the lever (lower = quicker but cuts you off more; the text check below is what makes lower safer)."
        )
    server = p50.get("stt_server_ms")
    if server and server > 500:
        out.append(
            f"- Speech-server time is {server:.0f} ms (p50): run `scripts/power_mode.sh max`, keep the laptop on AC, and try `--early-decode-ms 200` on the server (hard-command accuracy drops slightly)."
        )
    tts = p50.get("tts_first_audio_ms")
    if tts and tts > 400:
        out.append(
            f"- Speech synthesis needs {tts:.0f} ms for the first audio; the CPU is shared with recognition. Keep sentences short (`--max-words`)."
        )
    if wer is not None and wer > 0.08:
        out.append(
            f"- Word error is {wer:.1%}: look at the wrong turns above; add the missed names to `config/entities-docs.json` (or `python scripts/make_entities.py`), then raise `--hotword-score` a little."
        )
    cut = [r for r in rows if r.get("interrupted") == "resumed"]
    negative = [r for r in rows if r.get("pause_ms") is not None and r["pause_ms"] < 0]
    if len(cut) + len(negative) >= max(2, len(rows) // 10):
        out.append(
            f"- You were cut off or went on after the turn ended {len(cut) + len(negative)} times: you pause longer than the hold mid-sentence. Raise `--vad-hold` (try 0.9); the join after an interruption catches the rest but costs a second."
        )
    stops = [float(r["cancel_to_silence_ms"]) for r in rows if r.get("cancel_to_silence_ms")]
    if stops and statistics.median(stops) > 300:
        out.append(
            f"- It takes {statistics.median(stops):.0f} ms to go quiet once it hears you: check the audio output device latency."
        )
    asked = [r for r in rows if r.get("asked_to_add")]
    declined = [r for r in asked if not r.get("added")]
    if asked and len(declined) / len(asked) > 0.5:
        out.append(
            f"- {len(declined)} of {len(asked)} 'anything to add?' prompts got no addition: the threshold is asking too often. Lower `--add-threshold` (try 0.2)."
        )
    scored = [r for r in rows if r.get("unfinished_score") is not None]
    hanging = [
        r
        for r in scored
        if not r.get("asked_to_add")
        and r["unfinished_score"] >= 0.3
        and r.get("heard", "").rstrip(".?! ").lower().split()[-1:]
        and r["heard"].rstrip(".?! ").lower().split()[-1]
        in {"the", "a", "by", "and", "to", "of", "for", "with", "in", "on"}
    ]
    if hanging:
        out.append(
            f"- {len(hanging)} turns ended on a word like 'the'/'by'/'and' but scored as finished (e.g. turn {hanging[0]['turn']}: {hanging[0]['heard']!r}): raise `--add-threshold` (try 0.5) or note that the recogniser may have completed the phrase."
        )
    if not out:
        out.append("- Nothing stands out; compare with the next run using the same phrases.")
    print("\n".join(out))
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("path", type=Path, help="a --record folder (or a turns.jsonl)")
    ap.add_argument("--init-truth", action="store_true", help="write truth.tsv to correct by hand")
    ap.add_argument("--write", action="store_true", help="also save the report as report.md")
    args = ap.parse_args()
    info, rows = load(args.path)
    directory = args.path if args.path.is_dir() else args.path.parent
    if args.init_truth:
        init_truth(directory, rows)
        return 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        section_run(info, rows)
        p50 = section_latency(rows)
        section_interrupts(rows)
        wer = section_recognition(directory, rows)
        section_turn_end(rows)
        hints(p50, rows, wer, info)
    text = buf.getvalue()
    print(text)
    if args.write:
        (directory / "report.md").write_text(text)
        print(f"saved {directory / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
