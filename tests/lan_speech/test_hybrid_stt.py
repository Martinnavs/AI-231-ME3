"""Hybrid STT: Kroko-style streaming stream plus an offline transcriber and entity repair."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from lan_speech.engines import STTDecodeResult
from lan_speech.hotwords import load_entities, write_hotwords, write_vocab
from lan_speech.hybrid_stt import HybridSTTStream, pad_with_zeros, trim_silence
from lan_speech.transcript_repair import Entity, build_matchers

SR = 16000


class FakeBase:
    """Stands in for the streaming (Kroko) stream."""

    def __init__(self, results: list[STTDecodeResult | None], final_text: str = "") -> None:
        self.results = list(results)
        self.final_text = final_text
        self.chunks: list[bytes] = []
        self.was_reset = False

    def accept_waveform(self, sample_rate: int, pcm_bytes: bytes) -> None:
        self.chunks.append(pcm_bytes)

    def decode(self) -> STTDecodeResult | None:
        return self.results.pop(0) if self.results else None

    def finalize(self) -> str:
        return self.final_text

    def reset(self) -> None:
        self.was_reset = True


class Transcriber:
    def __init__(self, text: str | Exception) -> None:
        self.text = text
        self.calls: list[tuple[int, int]] = []
        self.arrays: list[np.ndarray] = []

    def __call__(self, samples: np.ndarray, sample_rate: int) -> str:
        self.calls.append((len(samples), sample_rate))
        self.arrays.append(samples)
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


def speech(seconds: float, lead: float = 0.0, tail: float = 0.0) -> bytes:
    t = np.arange(int(seconds * SR)) / SR
    tone = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
    z = lambda s: np.zeros(int(s * SR), np.int16)  # noqa: E731
    return np.concatenate([z(lead), tone, z(tail)]).tobytes()


MATCHERS = build_matchers(
    [Entity("Opalite", "music"), Entity("Taylor Swift", "music"), Entity("Angelo", "contact")]
)
FINAL = STTDecodeResult("kroko text", True, True)


def stream(base: FakeBase, tr: Transcriber) -> HybridSTTStream:
    return HybridSTTStream(base, tr, MATCHERS)


def test_interim_results_pass_through_without_running_parakeet() -> None:
    interim = STTDecodeResult("play opa", False, False)
    tr = Transcriber("never used")
    s = stream(FakeBase([interim]), tr)
    s.accept_waveform(SR, speech(0.5))
    assert s.decode() == interim
    assert tr.calls == []


def test_audio_is_forwarded_and_buffered() -> None:
    base = FakeBase([])
    s = stream(base, Transcriber("x"))
    s.accept_waveform(SR, b"\x01\x00" * 100)
    s.accept_waveform(SR, b"\x02\x00" * 100)
    assert len(base.chunks) == 2


def test_the_end_of_a_turn_is_transcribed_by_parakeet_and_repaired() -> None:
    tr = Transcriber("Play opalate by Taylor Swift.")
    s = stream(FakeBase([FINAL]), tr)
    s.accept_waveform(SR, speech(1.0, lead=0.5, tail=1.2))
    res = s.decode()
    assert res is not None and res.is_final and res.end_of_turn
    assert res.raw_text == "Play opalate by Taylor Swift."
    assert res.text == "Play Opalite by Taylor Swift."
    assert res.repair is not None
    assert res.repair["rung"] == "silent" and res.repair["slot"] == "music"
    # silence around the speech was trimmed, then 0.5 s + 1.0 s of zeros were put back
    n = tr.calls[0][0]
    assert n < (1.0 + 0.3 + 0.4 + 0.5 + 1.0) * SR + 800
    assert n > (1.0 + 0.5 + 1.0) * SR


def test_an_empty_streaming_turn_does_not_wake_parakeet() -> None:
    tr = Transcriber("never used")
    s = stream(FakeBase([STTDecodeResult("", True, True)]), tr)
    s.accept_waveform(SR, speech(0.2))
    res = s.decode()
    assert res is not None and res.text == "" and res.repair is None
    assert tr.calls == []


def test_the_buffer_starts_fresh_for_the_next_turn() -> None:
    tr = Transcriber("hello")
    s = stream(FakeBase([FINAL, FINAL]), tr)
    s.accept_waveform(SR, speech(2.0))
    s.decode()
    s.accept_waveform(SR, speech(0.5))
    s.decode()
    assert tr.calls[1][0] < tr.calls[0][0]


@pytest.mark.parametrize("failure", [RuntimeError("boom"), ""])
def test_a_parakeet_failure_falls_back_to_the_streaming_text(failure: str | Exception) -> None:
    s = stream(FakeBase([FINAL]), Transcriber(failure))
    s.accept_waveform(SR, speech(0.5))
    res = s.decode()
    assert res is not None and res.text == "kroko text" and res.repair is None


def test_the_clients_slot_and_risk_override_the_guess() -> None:
    s = stream(FakeBase([FINAL]), Transcriber("play angeilo"))
    s.set_context("contact", "high")
    s.accept_waveform(SR, speech(0.5))
    res = s.decode()
    assert res is not None and res.repair is not None
    assert res.repair["slot"] == "contact" and res.repair["risk"] == "high"


def test_without_a_matching_list_the_text_is_returned_untouched() -> None:
    s = stream(FakeBase([FINAL]), Transcriber("who won the nobel prize"))
    s.accept_waveform(SR, speech(0.5))
    res = s.decode()
    assert res is not None
    assert res.text == res.raw_text == "who won the nobel prize" and res.repair is None


def test_finalize_runs_the_same_path() -> None:
    s = stream(FakeBase([], final_text="kroko"), Transcriber("play opalate"))
    s.accept_waveform(SR, speech(0.5))
    res = s.finalize_result()
    assert res.text == "play Opalite" and res.repair is not None
    s.accept_waveform(SR, speech(0.5))
    assert s.finalize() == "play Opalite"


def test_reset_clears_the_buffer_and_the_streaming_stream() -> None:
    base = FakeBase([FINAL])
    tr = Transcriber("hello")
    s = stream(base, tr)
    s.accept_waveform(SR, speech(1.0))
    s.reset()
    assert base.was_reset
    s.accept_waveform(SR, speech(0.2))
    s.decode()
    assert tr.calls[0][0] < (0.2 + 0.3 + 0.4 + 0.5 + 1.0) * SR + 800


def test_parakeet_gets_digital_silence_around_the_speech() -> None:
    tr = Transcriber("hello")
    s = stream(FakeBase([FINAL]), tr)
    s.accept_waveform(SR, speech(1.0, lead=0.6, tail=0.6))
    s.decode()
    got = tr.arrays[0]
    assert not got[: int(0.5 * SR)].any() and not got[-int(1.0 * SR) :].any()
    assert got[int(0.5 * SR) : -int(1.0 * SR)].any()


def test_padding_can_be_set_per_stream() -> None:
    tr = Transcriber("hello")
    s = HybridSTTStream(FakeBase([FINAL]), tr, MATCHERS, pad_lead_s=0.0, pad_tail_s=0.0)
    s.accept_waveform(SR, speech(1.0))
    s.decode()
    assert tr.calls[0][0] <= (1.0 + 0.3 + 0.4) * SR + 800


def test_pad_with_zeros_adds_exactly_the_requested_silence() -> None:
    x = np.ones(SR, np.float32)
    out = pad_with_zeros(x, SR, 0.5, 1.0)
    assert len(out) == SR + SR // 2 + SR
    assert not out[: SR // 2].any() and not out[-SR:].any() and out[SR // 2 : -SR].all()


def test_the_audio_buffer_is_capped() -> None:
    s = HybridSTTStream(
        FakeBase([FINAL]),
        tr := Transcriber("x"),
        MATCHERS,
        max_seconds=2.0,
        pad_lead_s=0.0,
        pad_tail_s=0.0,
    )
    for _ in range(10):
        s.accept_waveform(SR, speech(1.0))
    s.decode()
    assert tr.calls[0][0] <= 2.0 * SR


def test_trim_keeps_a_margin_around_the_speech() -> None:
    pcm = np.frombuffer(speech(1.0, lead=2.0, tail=2.0), np.int16).astype(np.float32) / 32768
    out = trim_silence(pcm, SR, lead_s=0.3, tail_s=0.4)
    assert 1.0 + 0.3 + 0.4 - 0.05 <= len(out) / SR <= 1.0 + 0.3 + 0.4 + 0.05


def test_trim_leaves_pure_silence_alone() -> None:
    silence = np.zeros(SR, np.float32)
    assert len(trim_silence(silence, SR, 0.3, 0.4)) == SR


def test_the_hotword_files_have_both_spellings_and_a_score(tmp_path: Path) -> None:
    out = tmp_path / "hot.txt"
    write_hotwords(["Taylor Swift", "BINI"], 1.0, out)
    lines = out.read_text().splitlines()
    assert "taylor swift :1.0" in lines and "Taylor Swift :1.0" in lines
    assert "bini :1.0" in lines and "Bini :1.0" in lines


def test_the_vocab_file_is_derived_from_tokens(tmp_path: Path) -> None:
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("<blk> 0\n▁the 1\ns 2\n", encoding="utf-8")
    vocab = tmp_path / "v"
    write_vocab(tokens, vocab)
    rows = [line.split("\t") for line in vocab.read_text(encoding="utf-8").splitlines()]
    assert [r[0] for r in rows] == ["<blk>", "▁the", "s"]
    assert [float(r[1]) for r in rows] == [0.0, -0.01, -0.02]


def test_entities_load_from_a_mapping_or_a_list(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"Taylor Swift": "music", "Angelo": "contact"}))
    assert load_entities(a) == [Entity("Taylor Swift", "music"), Entity("Angelo", "contact")]
    b = tmp_path / "b.json"
    b.write_text(json.dumps(["Taylor Swift"]))
    assert load_entities(b) == [Entity("Taylor Swift")]


def test_the_shipped_names_file_loads_and_has_every_slot() -> None:
    ents = load_entities(Path("config/entities.json"))
    assert len(ents) > 100
    assert {"music", "contact", "place", "brand"} <= {e.category for e in ents}
