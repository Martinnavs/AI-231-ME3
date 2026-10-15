import numpy as np

from audio_engine import FakeSynthesizer, FakeTranscriber
from audio_engine.types import AudioClip, Transcript
from audio_engine.wav import float_to_pcm16


def _pcm(secs: float, amp: float = 0.3, rate: int = 16000) -> bytes:
    t = np.arange(int(rate * secs)) / rate
    return float_to_pcm16((amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32))


async def _drain(stream, n_expected_final: int = 1) -> list[Transcript]:  # type: ignore[no-untyped-def]
    out: list[Transcript] = []
    async for item in stream.results():
        out.append(item)
    return out


async def test_fake_stream_transcribe() -> None:
    stream = FakeTranscriber(eot_silence_ms=None).open_stream()
    await stream.start()
    await stream.send(_pcm(1.0))
    await stream.finalize()
    await stream.aclose()
    results = await _drain(stream)
    interims = [r for r in results if not r.is_final]
    finals = [r for r in results if r.is_final]
    assert len(interims) >= 9
    assert [f.text for f in finals] == ["hello"] and results[-1].is_final


async def test_fake_stream_synthesize_cancel() -> None:
    synth = FakeSynthesizer()
    it = synth.stream_synthesize("hello world")
    first = [await it.__anext__(), await it.__anext__()]
    assert all(len(c) == 640 for c in first)
    await synth.cancel()
    rest = [c async for c in it]
    assert rest == []
    fresh = [c async for c in synth.stream_synthesize("hi")]
    assert len(fresh) == 6


async def test_fake_synthesize_empty_and_batch() -> None:
    synth = FakeSynthesizer()
    assert [c async for c in synth.stream_synthesize("")] == []
    assert (await synth.synthesize("")).duration_s == 0
    clip = await synth.synthesize("abcde")
    assert abs(clip.duration_s - 0.3) < 1e-6
    level = 20 * np.log10(np.sqrt(np.mean(clip.samples**2)) * np.sqrt(2))
    assert abs(level - -12) < 0.2


async def test_fake_batch_resamples() -> None:
    clip = AudioClip(np.zeros(48000, dtype=np.float32), 48000)
    result = await FakeTranscriber(script=("a", "b")).transcribe(clip)
    assert result.text == "a" and result.is_final and abs(result.t_audio_end_s - 1.0) < 0.01  # type: ignore[operator]


async def _eot_times(parts: list[bytes], eot: int | None) -> list[float]:
    stream = FakeTranscriber(eot_silence_ms=eot).open_stream()
    for p in parts:
        await stream.send(p)
    await stream.aclose()
    return [
        r.t_audio_end_s  # type: ignore[misc]
        for r in await _drain(stream)
        if r.end_of_turn
    ]


async def test_fake_end_of_turn() -> None:
    silence = lambda s: b"\x00\x00" * int(16000 * s)  # noqa: E731
    times = await _eot_times([_pcm(0.5), silence(0.4)], 300)
    assert len(times) == 1 and abs(times[0] - 0.8) <= 0.02
    assert await _eot_times([_pcm(0.3), silence(0.2), _pcm(0.3)], 300) == []
    assert await _eot_times([_pcm(0.5), silence(0.4)], None) == []
