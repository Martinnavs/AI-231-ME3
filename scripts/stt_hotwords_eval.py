"""Score Parakeet-TDT with and without hotwords on a recorded clip set.

Reports WER, entity recall split into entities that are in the hotword list and
entities that are not, and the wait after speech. The hotword list is a frozen
file (bench/hotwords_v1.txt); entities not in it show whether hotwords help
unseen names or hurt them.

    uv run --group laptop python scripts/stt_hotwords_eval.py \
        --items bench/stt_heldout.json --audio-dir bench/mic_heldout
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))  # run without PYTHONPATH

from lan_speech.hotwords import write_hotwords, write_vocab  # noqa: E402

MODEL = Path("models/stt-candidates/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8")


def load_compare() -> Any:
    spec = importlib.util.spec_from_file_location("stt_compare", HERE / "stt_compare.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("stt_compare", mod)
    spec.loader.exec_module(mod)
    return mod


def frac(hit: dict[bool, int], tot: dict[bool, int], k: bool) -> str:
    return f"{hit[k]}/{tot[k]} ({hit[k] / tot[k]:.0%})" if tot[k] else "-"


def recognizer(model: Path, threads: int, **extra: Any) -> Any:
    import sherpa_onnx

    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(model / "encoder.int8.onnx"),
        decoder=str(model / "decoder.int8.onnx"),
        joiner=str(model / "joiner.int8.onnx"),
        tokens=str(model / "tokens.txt"),
        num_threads=threads,
        sample_rate=16000,
        feature_dim=80,
        model_type="nemo_transducer",
        **extra,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=Path, required=True)
    ap.add_argument("--audio-dir", type=Path, required=True)
    ap.add_argument("--hotwords", type=Path, default=Path("bench/hotwords_v1.txt"))
    ap.add_argument("--scores", type=float, nargs="+", default=[0.5, 1.0, 1.5])
    ap.add_argument("--model", type=Path, default=MODEL)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--tag", default="eval")
    args = ap.parse_args()

    sc = load_compare()
    items = [i for i in json.loads(args.items.read_text(encoding="utf-8")) if i["kind"] != "noise"]
    names = [n.strip() for n in args.hotwords.read_text(encoding="utf-8").splitlines() if n.strip()]
    listed = {" ".join(sc.norm_words(n)) for n in names}
    pad = (np.zeros(8000, np.float32), np.zeros(24000, np.float32))
    audio = {}
    for it in items:
        pcm = sc.read_wav(args.audio_dir / f"{it['id']}.wav").astype(np.float32) / 32768
        audio[it["id"]] = np.concatenate([pad[0], pcm, pad[1]])

    tmp = Path(tempfile.mkdtemp(prefix="hotwords_"))
    write_vocab(args.model / "tokens.txt", tmp / "pk.vocab")

    configs: list[tuple[str, dict[str, Any]]] = [
        ("greedy", dict(decoding_method="greedy_search")),
        ("beam4", dict(decoding_method="modified_beam_search", max_active_paths=4)),
    ]
    for s in args.scores:
        hw = tmp / f"hot_{s}.txt"
        write_hotwords(names, s, hw)
        configs.append(
            (
                f"beam4+hotwords {s}",
                dict(
                    decoding_method="modified_beam_search",
                    max_active_paths=4,
                    hotwords_file=str(hw),
                    hotwords_score=s,
                    modeling_unit="bpe",
                    bpe_vocab=str(tmp / "pk.vocab"),
                ),
            )
        )

    print(f"{len(items)} clips, {len(names)} hotword names, model {args.model.name}\n")
    print("| config | WER | WER (no-entity) | in-list ents | out-of-list ents | p50 ms |")
    print("|---|---|---|---|---|---|")
    out: dict[str, Any] = {}
    for name, kw in configs:
        rec = recognizer(args.model, args.threads, **kw)
        edits = words = e_all = w_all = 0
        hit = {True: 0, False: 0}
        tot = {True: 0, False: 0}
        lat: list[float] = []
        rows = []
        for it in items:
            t0 = time.perf_counter()
            st = rec.create_stream()
            st.accept_waveform(16000, audio[it["id"]])
            rec.decode_stream(st)
            lat.append((time.perf_counter() - t0) * 1000)
            hyp = sc.norm_words(st.result.text)
            ref = sc.norm_words(it["text"])
            e = sc.word_edits(ref, hyp)
            edits += e
            words += len(ref)
            if not it["entities"]:
                e_all += e
                w_all += len(ref)
            text = " " + " ".join(hyp) + " "
            for ent in it["entities"]:
                key = " ".join(sc.norm_words(ent))
                inl = key in listed
                tot[inl] += 1
                hit[inl] += f" {key} " in text
            rows.append((it["id"], it["kind"], it["text"], st.result.text))
        out[name] = rows

        no_ent = f"{e_all / w_all:.1%}" if w_all else "-"
        print(
            f"| {name} | {edits / words:.1%} | {no_ent} | {frac(hit, tot, True)} "
            f"| {frac(hit, tot, False)} | {np.percentile(lat, 50):.0f} |",
            flush=True,
        )
    dest = Path("out/stt_compare")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"hotwords-{args.tag}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nsaved out/stt_compare/hotwords-{args.tag}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
