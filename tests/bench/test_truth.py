from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from audio_engine import AudioClip, FakeSynthesizer, Synthesizer
from audio_engine.wav import read_wav
from bench.signal import speech_bounds
from bench.truth import Trial, build_truth_set, load_truth, main

SPEC = {
    "version": 1,
    "turns": [
        {"kind": "turn", "text": "hello there"},
        {"kind": "turn", "text": "Cuál es la capital?"},
        {"kind": "barge_in", "text": "tell me a story", "event_text": "stop"},
        {"kind": "noise", "text": "describe the sea"},
        {"kind": "pause", "parts": ["my birthday is", "january first"], "pause_s": 0.7},
        {"kind": "turn", "text": "bye"},
    ],
}


@pytest.fixture
def spec(tmp_path: Path) -> Path:
    p = tmp_path / "spec.json"
    p.write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    return p


def _write_spec(tmp_path: Path, turns: list[dict[str, object]]) -> Path:
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"version": 1, "turns": turns}), encoding="utf-8")
    return p


def test_build_fake_deterministic(spec: Path, tmp_path: Path) -> None:
    a = build_truth_set(spec, FakeSynthesizer(), tmp_path / "a", seed=0)
    b = build_truth_set(spec, FakeSynthesizer(), tmp_path / "b", seed=0)
    assert a.stimulus_sha256 == b.stimulus_sha256
    assert (tmp_path / "a" / "stimulus.wav").read_bytes() == (
        tmp_path / "b" / "stimulus.wav"
    ).read_bytes()
    assert len(a.trials) == len(SPEC["turns"])
    prev_end = 1.5
    for t in a.trials:
        assert t.t_start_s >= prev_end - 1e-9
        if t.kind == "pause":
            assert t.t_start_s < t.t_event_s < t.t_speech_end_s <= t.window_end_s
        else:
            assert t.t_start_s < t.t_speech_end_s
            assert t.t_speech_end_s < (t.t_event_s or t.window_end_s) <= t.window_end_s
        prev_end = t.window_end_s
    noise_seeded = build_truth_set(spec, FakeSynthesizer(), tmp_path / "c", seed=1)
    assert noise_seeded.stimulus_sha256 != a.stimulus_sha256


def test_layout_t0_matches_clip(spec: Path, tmp_path: Path) -> None:
    truth = build_truth_set(spec, FakeSynthesizer(), tmp_path, seed=0)
    clip = read_wav(tmp_path / "stimulus.wav")
    sr = clip.sample_rate
    for t in truth.trials:
        upper = t.window_end_s if t.kind == "pause" else (t.t_event_s or t.window_end_s)
        seg = clip.samples[round(t.t_start_s * sr) : round(upper * sr)]
        _, end = speech_bounds(seg, sr)
        assert abs(t.t_start_s + end - t.t_speech_end_s) <= 0.010, t.id


def test_pause_trial_layout(spec: Path, tmp_path: Path) -> None:
    truth = build_truth_set(spec, FakeSynthesizer(), tmp_path, seed=0)
    clip = read_wav(tmp_path / "stimulus.wav")
    sr = clip.sample_rate
    t = next(t for t in truth.trials if t.kind == "pause")
    assert t.t_event_s is not None and t.pause_s is not None
    seg = clip.samples[round(t.t_start_s * sr) : round(t.window_end_s * sr)]
    mid = round((t.t_event_s - t.t_start_s) * sr)
    _, end1 = speech_bounds(seg[: mid + 100], sr)
    assert abs(t.t_start_s + end1 - t.t_event_s) <= 0.010
    gap_samples = np.flatnonzero(np.abs(seg[mid - 50 : mid + int(1.0 * sr)]) > 1e-3)
    first_after = [i for i in gap_samples if i > 100][0] + mid - 50
    assert abs((first_after - mid) / sr - t.pause_s) <= 0.010
    assert t.text == "my birthday is january first"


def test_truth_roundtrip_json(spec: Path, tmp_path: Path) -> None:
    truth = build_truth_set(spec, FakeSynthesizer(), tmp_path, seed=3)
    assert load_truth(tmp_path / "truth.json") == truth
    raw = (tmp_path / "truth.json").read_text(encoding="utf-8")
    assert "Cuál" in raw
    assert isinstance(load_truth(tmp_path / "truth.json").trials[0], Trial)


class _Wrap(Synthesizer):
    def __init__(self, inner: FakeSynthesizer, transform: str) -> None:
        self.sample_rate = inner.sample_rate
        self.inner = inner
        self.transform = transform

    async def synthesize(self, text: str, voice: str | None = None) -> AudioClip:
        clip = await self.inner.synthesize(text, voice)
        x = clip.samples
        if self.transform == "padded":
            z = np.zeros(8000, dtype=np.float32)
            x = np.concatenate([z, x, z])
        elif self.transform == "loud":
            x = x * np.float32(3.9)
        elif self.transform == "gap":
            z = np.zeros(self.sample_rate * 10, dtype=np.float32)
            x = np.concatenate([x, z, x])
        return AudioClip(x.astype(np.float32), clip.sample_rate)

    def stream_synthesize(self, text: str, voice: str | None = None):  # type: ignore[no-untyped-def]
        return self.inner.stream_synthesize(text, voice)

    async def list_voices(self):  # type: ignore[no-untyped-def]
        return await self.inner.list_voices()


def test_padded_clips_trimmed(spec: Path, tmp_path: Path) -> None:
    plain = build_truth_set(spec, FakeSynthesizer(), tmp_path / "p")
    padded = build_truth_set(spec, _Wrap(FakeSynthesizer(), "padded"), tmp_path / "q")
    assert plain.stimulus_sha256 == padded.stimulus_sha256


def test_loud_clip_normalized(tmp_path: Path) -> None:
    quiet = _write_spec(tmp_path, [{"kind": "turn", "text": "a loud one here"}])
    build_truth_set(quiet, _Wrap(FakeSynthesizer(), "loud"), tmp_path)
    clip = read_wav(tmp_path / "stimulus.wav")
    peak = float(np.max(np.abs(clip.samples)))
    assert abs(20 * np.log10(peak) + 3.0) < 0.1


def test_resampled_clip(spec: Path, tmp_path: Path) -> None:
    truth = build_truth_set(spec, FakeSynthesizer(sample_rate=24000), tmp_path)
    assert truth.sample_rate == 16000
    assert read_wav(tmp_path / "stimulus.wav").sample_rate == 16000


def test_internal_pause_longer_than_window(spec: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="t001"):
        build_truth_set(spec, _Wrap(FakeSynthesizer(), "gap"), tmp_path)


def test_unknown_kind(tmp_path: Path) -> None:
    bad = _write_spec(tmp_path, [{"kind": "shout", "text": "x"}])
    with pytest.raises(ValueError, match="unknown trial kind"):
        build_truth_set(bad, FakeSynthesizer(), tmp_path / "o")


@pytest.mark.parametrize(
    "trial",
    [
        {"kind": "pause", "parts": ["only one"], "pause_s": 0.7},
        {"kind": "pause", "parts": ["a", "b", "c"], "pause_s": 0.7},
        {"kind": "pause", "parts": ["a", "b"], "pause_s": 0.1},
        {"kind": "pause", "parts": ["a", "b"], "pause_s": 2.0},
    ],
)
def test_bad_pause_trial(tmp_path: Path, trial: dict[str, object]) -> None:
    bad = _write_spec(tmp_path, [{"kind": "turn", "text": "ok"}, trial])
    with pytest.raises(ValueError, match="t002"):
        build_truth_set(bad, FakeSynthesizer(), tmp_path / "o")


def test_cli_fake(spec: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out"
    assert main(["--spec", str(spec), "--backend", "fake", "--out", str(out)]) == 0
    assert (out / "stimulus.wav").exists() and (out / "truth.json").exists()
    assert "6 trials" in capsys.readouterr().out


def test_shipped_spec_counts() -> None:
    turns = json.loads(Path("bench/turns/v1.json").read_text(encoding="utf-8"))["turns"]
    kinds = [t["kind"] for t in turns]
    assert kinds.count("turn") >= 40
    assert kinds.count("barge_in") == kinds.count("noise") == kinds.count("pause") == 10
