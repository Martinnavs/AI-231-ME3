"""The laptop music player (Y3): ducking and event handling, with a fake decoder and socket."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from doc_chat.aec import EchoCanceller
from doc_chat.music import (
    TTS_REFERENCE_LUFS,
    AutoGain,
    MusicPlayer,
    loudness_of,
)

RATE = 16000


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def recv(self, timeout: float = 0) -> str:
        return json.dumps({"type": "hello", "rate": RATE})

    def send(self, data: Any) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


class FakeProc:
    def __init__(self, seconds: float) -> None:
        pcm = (np.ones(int(RATE * seconds)) * 1000).astype(np.int16).tobytes()
        self.stdout = io.BytesIO(pcm)

    def kill(self) -> None:
        pass


def wait(cond: Any, timeout: float = 3.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def messages(ws: FakeWs) -> list[dict[str, Any]]:
    return [json.loads(m) for m in ws.sent if isinstance(m, str)]


def make(seconds: float = 0.4, **kw: Any) -> tuple[MusicPlayer, FakeWs]:
    ws = FakeWs()
    player = MusicPlayer(lambda: ws, decoder=lambda url, rate: FakeProc(seconds), **kw)
    return player, ws


def test_play_opens_the_music_role_sets_the_level_and_streams_audio() -> None:
    player, ws = make()
    player.handle({"event": "music", "action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: not player.playing)
    kinds = [m["type"] for m in messages(ws)]
    assert kinds[0] == "music"
    gain = next(m for m in messages(ws) if m["type"] == "music_gain")
    assert gain["db"] == -6.0  # the offset from the voice-equal level
    audio = sum(len(m) for m in ws.sent if isinstance(m, bytes))
    assert audio == int(RATE * 0.4) * 2


def test_stop_flushes_and_ends_the_stream_early() -> None:
    player, ws = make(seconds=30)
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    player.handle({"action": "stop"})
    assert not player.playing
    assert messages(ws)[-1]["type"] == "music_flush"
    n = len(ws.sent)
    time.sleep(0.2)
    assert len(ws.sent) <= n + 1


def test_pause_flushes_and_resume_continues() -> None:
    player, ws = make(seconds=30)
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    player.handle({"action": "pause"})
    time.sleep(0.2)
    n = len(ws.sent)
    time.sleep(0.3)
    assert len(ws.sent) == n
    player.handle({"action": "resume"})
    assert wait(lambda: len(ws.sent) > n)
    player.close()


def test_volume_sends_the_new_level() -> None:
    player, ws = make(seconds=30)
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    player.handle({"action": "volume", "db": -9.0})
    assert messages(ws)[-1] == {"type": "music_gain", "db": -9.0} or any(
        m.get("db") == -9.0 for m in messages(ws)
    )
    player.close()


def test_the_music_reference_reaches_the_canceller() -> None:
    canceller = EchoCanceller()
    player, ws = make(seconds=1.0, canceller=canceller)
    player.handle({"action": "play", "url": "http://x/a", "db": -6.0})
    assert wait(lambda: len(canceller._music_frames) > 0)
    player.close()


# ---- loudness: music at the voice's level


def voice(seconds: float = 6.0, rate: int = 24000) -> np.ndarray:
    """Speech-like: a 120 Hz pulse train through three formants, in syllables with gaps."""
    from scipy.signal import lfilter

    n = int(rate * seconds)
    t = np.arange(n) / rate
    src = (np.sin(2 * np.pi * 120 * t) > 0.98).astype(float)
    out = np.zeros(n)
    for f, bw in ((700, 130), (1220, 70), (2600, 160)):
        r = np.exp(-np.pi * bw / rate)
        out += lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * f / rate), r * r], src)
    syllable = (np.sin(2 * np.pi * 3.5 * t) > -0.3) * (0.5 + 0.5 * np.sin(2 * np.pi * 3.5 * t))
    return out * syllable / np.max(np.abs(out))


def music(seconds: float, db: float, rate: int = RATE, seed: int = 1) -> bytes:
    """Broadband, no voice: pink-ish noise with a bass line and chords, at about `db` dBFS RMS."""
    from scipy.signal import lfilter

    rng = np.random.default_rng(seed)
    n = int(rate * seconds)
    t = np.arange(n) / rate
    noise = lfilter([1.0], [1.0, -0.9], rng.standard_normal(n))
    tones = sum(np.sin(2 * np.pi * f * t) for f in (55, 110, 220, 330, 440, 660, 1320))
    x = noise / np.std(noise) * 0.5 + tones / np.std(tones)
    x *= 10 ** (db / 20) / np.sqrt(np.mean(x**2))
    return (np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes()


def test_a_full_scale_997_hz_sine_is_minus_3_lufs() -> None:
    t = np.arange(RATE * 4) / RATE
    assert abs(loudness_of(np.sin(2 * np.pi * 997 * t), RATE) - (-3.01)) < 0.15


def test_the_reference_is_the_loudness_of_matched_tts() -> None:
    from lan_speech.engines import KITTEN_TARGET_RMS_DBFS, match_level

    wav = match_level(voice() * 0.3)
    active = wav[np.abs(wav) > 0.01]
    assert abs(20 * np.log10(np.sqrt(np.mean(active**2))) - KITTEN_TARGET_RMS_DBFS) < 0.5
    assert abs(loudness_of(wav, 24000) - TTS_REFERENCE_LUFS) < 3.0  # a synthetic stand-in


def slices(pcm: bytes) -> list[bytes]:
    n = int(RATE * 0.05) * 2
    return [pcm[i : i + n] for i in range(0, len(pcm) - n + 1, n)]


@pytest.mark.parametrize("source_db", [-8.0, -20.0, -38.0])
def test_the_auto_gain_brings_music_to_the_voice_within_2_lu(source_db: float) -> None:
    from lan_speech.engines import match_level

    reference = loudness_of(match_level(voice() * 0.3), 24000)
    gain = AutoGain(RATE, reference=reference)
    track = music(40.0, source_db)
    history = [gain.update(piece, 0.05) for piece in slices(track)]
    x = np.frombuffer(track, dtype=np.int16).astype(np.float64) / 32768
    tail = x[-RATE * 5 :] * 10 ** (history[-1] / 20)
    assert abs(loudness_of(tail, RATE) - reference) <= 2.0
    steps = np.abs(np.diff(history))
    assert steps.max() <= 1.0 * 0.05 + 1e-9  # at most 1 dB per second


def test_the_auto_gain_is_clamped() -> None:
    gain = AutoGain(RATE, reference=-24.0, limits=(-6.0, 6.0))
    for piece in slices(music(30.0, -50.0)):
        gain.update(piece, 0.05)
    assert gain.db == 6.0


def test_the_gain_sent_to_the_pi_includes_the_auto_gain() -> None:
    player, ws = make(seconds=1.0)
    player.handle({"action": "play", "url": "http://x/a", "db": -3.0})
    assert wait(lambda: not player.playing, 10.0)
    gains = [m["db"] for m in messages(ws) if m["type"] == "music_gain"]
    assert gains[0] == -3.0 and len(gains) >= 1


# ---- the hold: no music under a reply


def test_no_music_is_sent_while_a_reply_plays_and_a_flush_comes_first() -> None:
    speaking = {"v": False}
    ws = FakeWs()
    player = MusicPlayer(
        lambda: ws,
        decoder=lambda url, rate: FakeProc(30),
        tts_playing=lambda: speaking["v"],
    )
    player.handle({"action": "play", "url": "http://x/a", "db": 0.0})
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    speaking["v"] = True
    assert wait(lambda: messages(ws)[-1]["type"] == "music_flush")
    time.sleep(0.1)
    n = len(ws.sent)
    time.sleep(0.4)
    assert len(ws.sent) == n  # nothing, not even a gain, while the reply plays
    speaking["v"] = False
    assert wait(lambda: len(ws.sent) > n)
    player.close()


def test_music_started_during_its_announcement_waits_for_it() -> None:
    ws = FakeWs()
    player = MusicPlayer(
        lambda: ws, decoder=lambda url, rate: FakeProc(30), tts_playing=lambda: True
    )
    player.handle({"action": "play", "url": "http://x/a", "db": 0.0})
    time.sleep(0.4)
    assert not any(isinstance(m, bytes) for m in ws.sent)
    player._tts = lambda: False
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    player.close()


def test_the_spotify_app_is_paused_for_a_reply_and_resumed_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doc_chat import music as music_module

    commands: list[str] = []
    monkeypatch.setattr(
        music_module, "spotify_local", lambda command, uri="", **kw: commands.append(command)
    )
    speaking = {"v": False}
    ws = FakeWs()
    player = MusicPlayer(
        lambda: ws,
        decoder=lambda url, rate: FakeProc(30),
        pipe_decoder=lambda path, rate: FakeProc(30),
        tts_playing=lambda: speaking["v"],
    )
    monkeypatch.setattr(player, "_route", lambda: None)
    player.handle({"action": "play", "source": "spotify", "db": 0.0})
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    speaking["v"] = True
    assert wait(lambda: commands[-1:] == ["pause"])
    speaking["v"] = False
    assert wait(lambda: commands[-1:] == ["play"])
    player.close()


def test_a_user_pause_stays_paused_when_the_reply_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    from doc_chat import music as music_module

    commands: list[str] = []
    monkeypatch.setattr(
        music_module, "spotify_local", lambda command, uri="", **kw: commands.append(command)
    )
    speaking = {"v": True}
    player, _ = make(seconds=30, tts_playing=lambda: speaking["v"])
    player._spotify = True
    player.handle({"action": "pause"})
    speaking["v"] = False
    time.sleep(0.3)
    assert "play" not in commands
    player.close()


def test_louder_at_the_default_level_does_not_raise_it() -> None:
    import json as json_module
    from pathlib import Path

    from voice_agent.mcp_servers.music import MusicControl

    config = json_module.loads((Path(__file__).parents[2] / "config" / "music.json").read_text())
    assert config["level_db"] == 0.0 and config["max_db"] == 0.0
    control = MusicControl(lambda q: [], {**config, "source": "youtube"})
    said = control._volume("volume_up")
    assert "as loud as the music goes" in str(said)
    assert control.level_db == 0.0
    assert "Quieter" in str(control._volume("volume_down")) and control.level_db == -3.0


def test_a_remote_player_is_done_after_close_or_abort() -> None:
    from doc_chat.playback import RemotePlayer

    class Speaker:
        rate = 16000

        def send(self, data: Any) -> None:
            pass

    done = RemotePlayer(Speaker(), 16000)  # type: ignore[arg-type]
    assert not done.done
    done.close()
    assert done.done
    aborted = RemotePlayer(Speaker(), 16000)  # type: ignore[arg-type]
    aborted.abort()
    assert aborted.done
    aborted.close()


# ---- the user's voice pauses the music (Task 2b)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "music_pause"
MODEL = Path(__file__).parents[2] / "assets" / "silero_vad.onnx"
BLOCK = RATE // 10
needs_model = pytest.mark.skipif(not MODEL.exists(), reason="assets/silero_vad.onnx missing")


def phrase(name: str) -> np.ndarray:
    import wave

    with wave.open(str(FIXTURES / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)


def residue(seconds: float, db: float = -41.0, seed: int = 3) -> np.ndarray:
    """What the music leaves after the echo canceller: low-level, broadband, tonal."""
    return np.frombuffer(music(seconds, db, seed=seed), dtype=np.int16).astype(float)


class StubPlayer:
    def __init__(self) -> None:
        self.audible = True
        self.paused_at: list[int] = []
        self.block = 0

    def user_pause(self) -> None:
        self.paused_at.append(self.block)
        self.audible = False


def run_voice(signal: np.ndarray, **kw: Any) -> StubPlayer:
    from doc_chat.music import MusicVoice

    player = StubPlayer()
    voice = MusicVoice(player, str(MODEL), **kw)  # type: ignore[arg-type]
    pcm = np.clip(signal, -32768, 32767).astype(np.int16)
    for i in range(0, len(pcm) - BLOCK + 1, BLOCK):
        player.block = i // BLOCK
        voice.feed(pcm[i : i + BLOCK].tobytes())
        if not player.audible:
            break
    return player


@needs_model
def test_speech_over_music_pauses_it_within_0_4_s_on_90_percent_of_phrases() -> None:
    names = sorted(p.stem for p in FIXTURES.glob("*.wav"))
    assert len(names) >= 8
    onset_block, caught, total = 20, 0, 0
    for name in names:
        ph = phrase(name)
        active = ph[np.abs(ph) > 330]
        for level in (-26.0, -21.0, -16.0):
            bed = residue(len(ph) / RATE + 4.0, seed=total)
            mix = bed.copy()
            start = onset_block * BLOCK
            mix[start : start + len(ph)] += (
                ph * 10 ** (level / 20) * 32768 / np.sqrt(np.mean(active**2))
            )
            player = run_voice(mix)
            total += 1
            if (
                player.paused_at
                and 0 <= (player.paused_at[0] + 1 - onset_block) * 0.1 <= 0.4 + 1e-9
            ):
                caught += 1
    assert caught / total >= 0.90, (caught, total)


@needs_model
def test_music_alone_is_not_paused_in_a_synthetic_residue_stretch() -> None:
    wrong = sum(bool(run_voice(residue(60.0, seed=s)).paused_at) for s in range(3))
    assert wrong == 0  # three 60 s stretches, no pause


@needs_model
def test_recorded_music_residue_is_wrongly_paused_at_most_2_percent_of_the_time() -> None:
    import wave

    base = Path(__file__).parents[2] / "out" / "sessions" / "stack-1106-1736" / "audio"
    files = sorted(base.glob("turn*.wav"))
    if not files:
        pytest.skip("no recorded session on this machine")
    lost = total = 0.0
    for f in files:
        with wave.open(str(f)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(float)
        n = len(x) // BLOCK
        level = np.array(
            [
                20
                * np.log10(np.sqrt(np.mean((x[i * BLOCK : (i + 1) * BLOCK] / 32768) ** 2)) + 1e-9)
                for i in range(n)
            ]
        )
        start = None
        for i, q in enumerate(list(level < -33) + [False]):
            if q and start is None:
                start = i
            elif not q and start is not None:
                if (i - start) >= 20:  # 2 s of quiet, 0.2 s trimmed each side
                    run = x[(start + 2) * BLOCK : (i - 2) * BLOCK]
                    player = run_voice(run)
                    total += len(run) / RATE
                    if player.paused_at:
                        lost += len(run) / RATE - (player.paused_at[0] + 1) * 0.1
                start = None
    assert total > 60.0
    assert lost / total <= 0.02, (lost, total)


def test_the_voice_detector_only_runs_while_the_music_is_audible() -> None:
    from doc_chat.music import MusicVoice

    class Vad:
        def __init__(self) -> None:
            self.fed = 0
            self.resets = 0

        def feed(self, block: bytes) -> bool:
            self.fed += 1
            return self.fed == 3

        def reset(self) -> None:
            self.resets += 1

    player, vad = StubPlayer(), Vad()
    voice = MusicVoice(player, "", vad=vad)  # type: ignore[arg-type]
    player.audible = False
    voice.feed(b"\0" * 3200)
    assert vad.fed == 0
    player.audible = True
    for _ in range(5):
        voice.feed(b"\0" * 3200)
        if not player.audible:
            break
    assert vad.fed == 3 and player.paused_at == [0] and vad.resets == 1


def test_a_user_pause_is_sticky_flushes_and_only_a_command_resumes_it() -> None:
    player, ws = make(seconds=30)
    player.handle({"action": "play", "url": "http://x/a", "db": 0.0})
    assert wait(lambda: any(isinstance(m, bytes) for m in ws.sent))
    assert player.audible
    player.user_pause()
    assert player.auto_paused and not player.audible
    assert messages(ws)[-1]["type"] == "music_flush"
    time.sleep(0.2)
    n = len(ws.sent)
    player.handle({"action": "volume", "db": -3.0})  # volume while paused: still paused
    time.sleep(0.4)
    assert [m for m in ws.sent[n:] if isinstance(m, bytes)] == [] and player.auto_paused
    player.handle({"action": "resume"})
    assert not player.auto_paused and wait(lambda: any(isinstance(m, bytes) for m in ws.sent[n:]))
    player.close()


def test_a_user_pause_with_no_music_does_nothing() -> None:
    player, ws = make()
    player.user_pause()
    assert not player.auto_paused and ws.sent == []


def test_a_barge_in_pause_during_a_reply_stays_after_the_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from doc_chat import music as music_module

    commands: list[str] = []
    monkeypatch.setattr(
        music_module, "spotify_local", lambda command, uri="", **kw: commands.append(command)
    )
    speaking = {"v": True}
    player, ws = make(seconds=30, tts_playing=lambda: speaking["v"])
    player._spotify = True
    player.handle({"action": "play", "url": "http://x/a", "db": 0.0, "source": "spotify"})
    time.sleep(0.2)
    player.user_pause()  # the barge-in
    speaking["v"] = False
    time.sleep(0.4)
    assert "play" not in commands and not any(isinstance(m, bytes) for m in ws.sent)
    player.close()


def test_the_mic_tap_sees_every_block_after_the_canceller() -> None:
    import asyncio

    from doc_chat.speech import MicHub

    class Canceller:
        def process(self, block: bytes) -> bytes:
            return block[::-1]

    async def go() -> list[bytes]:
        hub = MicHub(canceller=Canceller())
        seen: list[bytes] = []
        hub.tap = seen.append
        hub._take(b"abcd")
        hub.tap = lambda b: 1 / 0  # a broken listener never breaks the mic
        hub._take(b"wxyz")
        assert await hub._queue.get() == b"dcba" and await hub._queue.get() == b"zyxw"
        return seen

    assert asyncio.run(go()) == [b"dcba"]


def test_the_flag_off_attaches_no_detector_and_on_attaches_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from doc_chat import music as music_module
    from doc_chat.__main__ import Session

    made: list[str] = []

    class Voice:
        def __init__(self, player: Any, model: str, gate_db: float | None = None) -> None:
            made.append(model)

        def feed(self, block: bytes) -> None:
            pass

    monkeypatch.setattr(music_module, "MusicVoice", Voice)
    for flag in (False, True):
        hub = SimpleNamespace(tap=None)
        session = SimpleNamespace(
            args=SimpleNamespace(music_pause_vad=flag, vad_model="m.onnx"),
            client=SimpleNamespace(hub=hub),
            _music=object(),
        )
        Session._attach_music_voice(session)  # type: ignore[arg-type]
        assert (hub.tap is not None) is flag
    assert made == ["m.onnx"]


def test_the_music_state_rides_on_the_agent_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from doc_chat import agent_client

    sent: list[dict[str, Any]] = []

    class Ws:
        def __enter__(self) -> Ws:
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def send(self, data: str) -> None:
            sent.append(json.loads(data))

        def recv(self, timeout: float = 0) -> str:
            return json.dumps({"done": True})

    monkeypatch.setattr(agent_client, "connect", lambda *a, **k: Ws())
    from langchain_core.messages import HumanMessage

    model = agent_client.AgentChatModel(url="ws://x", token="t", music_state=lambda: "paused")
    list(model._stream([HumanMessage(content="play")]))
    quiet = agent_client.AgentChatModel(url="ws://x", token="t", music_state=lambda: None)
    list(quiet._stream([HumanMessage(content="play")]))
    list(agent_client.AgentChatModel(url="ws://x", token="t")._stream([HumanMessage(content="x")]))
    assert sent[0]["music"] == "paused" and "music" not in sent[1] and "music" not in sent[2]


def test_no_music_is_audible_alongside_a_reply_end_to_end() -> None:
    """Real MusicPlayer to a real Mixer through a fake socket; the reply's channel and the hold
    flag are driven like a session: every slice that has a reply is exactly the reply."""
    from doc_chat.mixer import Mixer

    mixer = Mixer(RATE, lambda rate: (lambda data: None, lambda: None))

    class MixerWs(FakeWs):
        def send(self, data: Any) -> None:
            super().send(data)
            if isinstance(data, bytes):
                mixer.music_push(data)
            elif json.loads(data)["type"] == "music_flush":
                mixer.music_flush()

    ws = MixerWs()
    speaking = {"v": False}
    player = MusicPlayer(
        lambda: ws, decoder=lambda url, rate: FakeProc(30), tts_playing=lambda: speaking["v"]
    )
    player.handle({"action": "play", "url": "http://x/a", "db": 0.0})
    assert wait(lambda: len(mixer._music) > 0)
    speaking["v"] = True
    channel = mixer.tts_channel()  # the reply starts: the client's flush is racing the Pi's ring
    channel.push((np.full(RATE // 20 * 10, 5000, dtype=np.int16)).tobytes())
    both, replies = 0, 0
    for _ in range(10):
        out = np.frombuffer(mixer.step() or b"", dtype=np.int16)
        if out.size:
            replies += 1
            both += int(set(out.tolist()) != {5000})
        time.sleep(0.01)
    speaking["v"] = False
    player.close()
    assert replies == 10 and both == 0


def _control():  # type: ignore[no-untyped-def]
    from voice_agent.mcp_servers.music import MusicControl

    config = {
        "source": "youtube",
        "default_query": "mix",
        "level_db": 0.0,
        "step_db": 3.0,
        "min_db": -30.0,
        "max_db": 0.0,
    }
    hit = {"stream_url": "http://x/a", "title": "Song", "duration": 200}
    control = MusicControl(lambda q: [hit], config)
    control.act("play", "something")
    control.act("pause")  # what the synced request does after an automatic pause
    return control


def test_after_an_automatic_pause_a_bare_play_resumes() -> None:
    control = _control()
    got = control.act("play", "")
    assert got["event"]["action"] == "resume" and control.state == "playing"


def test_after_an_automatic_pause_any_other_reply_leaves_the_music_paused() -> None:
    control = _control()
    assert control.act("volume_down")["event"]["action"] == "volume"
    assert control.act("pause")["sentence"] == "It is already paused."
    assert control.state == "paused"
