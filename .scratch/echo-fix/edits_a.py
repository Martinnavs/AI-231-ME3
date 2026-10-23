"""Edit lists for ticket A (echo canceller wiring): file -> [(old, new)], each old occurs once."""

EDITS: dict[str, list[tuple[str, str]]] = {
    "src/doc_chat/speech.py": [
        (
            """    def __init__(self, rate: int = 16000, block_ms: int = 100) -> None:
        self.rate = rate
        self.block_ms = block_ms
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream: Any = None
""",
            """    def __init__(self, rate: int = 16000, block_ms: int = 100, canceller: Any = None) -> None:
        self.rate = rate
        self.block_ms = block_ms
        self._canceller = canceller  # an EchoCanceller: every block goes through it, in order
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._stream: Any = None
""",
        ),
        (
            """            loop.call_soon_threadsafe(self._queue.put_nowait, bytes(indata))
""",
            """            loop.call_soon_threadsafe(self._take, bytes(indata))
""",
        ),
        (
            """    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
""",
            """    def _take(self, block: bytes) -> None:
        self._queue.put_nowait(self._canceller.process(block) if self._canceller else block)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
""",
        ),
        (
            """        self.hub: MicHub | None = None  # set to listen to the microphone across turns
""",
            """        self.hub: MicHub | None = None  # set to listen to the microphone across turns
        self.canceller: Any = None  # an EchoCanceller, handed to every remote player
""",
        ),
    ],
    "src/doc_chat/playback.py": [
        (
            """        from scipy.signal import resample_poly

        slice_n = max(int(self._rate * SLICE_S), 1)
""",
            """        from math import gcd

        from scipy.signal import resample_poly

        slice_n = max(int(self._rate * SLICE_S), 1)
""",
        ),
        (
            """    def __init__(self, speaker: RemoteSpeaker, sample_rate: int) -> None:
        from math import gcd

        self._speaker = speaker
""",
            """    def __init__(self, speaker: RemoteSpeaker, sample_rate: int, canceller: Any = None) -> None:
        from math import gcd

        self._speaker = speaker
        self._canceller = canceller  # gets what is sent, as 16 kHz audio, to cancel its echo
""",
        ),
        (
            """            data = np.clip(y, -32768, 32767).astype(np.int16)
            for i in range(0, len(data), slice_n):
""",
            """            data = np.clip(y, -32768, 32767).astype(np.int16)
            ref16 = None
            if self._canceller is not None:
                g16 = gcd(16000, self._tts_rate)
                ref16 = resample_poly(x, 16000 // g16, self._tts_rate // g16)
            for i in range(0, len(data), slice_n):
""",
        ),
        (
            """                level = float(np.sqrt(np.mean((piece.astype(np.float32) / 32768.0) ** 2)))
                self._levels.append((time.perf_counter() + REMOTE_LEAD_S, level))
""",
            """                if ref16 is not None:
                    a = round(i * 16000 / self._rate)
                    b = round((i + len(piece)) * 16000 / self._rate)
                    reference = np.clip(ref16[a:b], -32768, 32767).astype(np.int16)
                    self._canceller.reference(reference.tobytes())
                level = float(np.sqrt(np.mean((piece.astype(np.float32) / 32768.0) ** 2)))
                self._levels.append((time.perf_counter() + REMOTE_LEAD_S, level))
""",
        ),
        (
            """        with contextlib.suppress(Exception):
            self._speaker.send(json.dumps({"type": "abort"}))
""",
            """        with contextlib.suppress(Exception):
            self._speaker.send(json.dumps({"type": "abort"}))
        if self._canceller is not None:
            self._canceller.clear()
""",
        ),
        (
            """def make_player(sample_rate: int, remote_url: str | None = None) -> Player | RemotePlayer:
""",
            """def make_player(
    sample_rate: int, remote_url: str | None = None, canceller: Any = None
) -> Player | RemotePlayer:
""",
        ),
        (
            """    return RemotePlayer(_remote[remote_url], sample_rate)
""",
            """    return RemotePlayer(_remote[remote_url], sample_rate, canceller)
""",
        ),
    ],
    "src/doc_chat/__main__.py": [
        (
            """        print(f"bot:  {text}", flush=True)
        player = (
            make_player(self.client.tts_sample_rate, getattr(self.args, "play_remote", None))
            if self.args.play
            else None
        )
        self._player = player
        self._spoken.append(text)
""",
            """        print(f"bot:  {text}", flush=True)
        player = (
            make_player(
                self.client.tts_sample_rate,
                getattr(self.args, "play_remote", None),
                self.client.canceller,
            )
            if self.args.play
            else None
        )
        self._player = player
        self._spoken.append(text)
""",
        ),
        (
            """        player = make_player(self.client.tts_sample_rate, getattr(self.args, "play_remote", None))
        try:
            player.push(pcm)
""",
            """        player = make_player(
            self.client.tts_sample_rate,
            getattr(self.args, "play_remote", None),
            self.client.canceller,
        )
        try:
            player.push(pcm)
""",
        ),
        (
            """            print(f"      (recognised as: {heard.raw_text})", flush=True)
        player = (
            make_player(self.client.tts_sample_rate, getattr(self.args, "play_remote", None))
            if self.args.play
            else None
        )
""",
            """            print(f"      (recognised as: {heard.raw_text})", flush=True)
        player = (
            make_player(
                self.client.tts_sample_rate,
                getattr(self.args, "play_remote", None),
                self.client.canceller,
            )
            if self.args.play
            else None
        )
""",
        ),
        (
            """        hub = MicHub()
        hub.start()  # one microphone stream for the whole session: heard while it thinks and talks
        client.hub = hub
""",
            """        canceller = None
        if args.aec:
            from doc_chat.aec import EchoCanceller

            canceller = EchoCanceller(args.aec_lead_ms)
        hub = MicHub(canceller=canceller)
        hub.start()  # one microphone stream for the whole session: heard while it thinks and talks
        client.hub = hub
        client.canceller = canceller
""",
        ),
        (
            """    parser.add_argument("--out", help="Output directory for generated WAVs")
""",
            """    parser.add_argument(
        "--aec",
        action="store_true",
        help="cancel the remote speaker's echo in the microphone, using what is sent to it as the "
        "reference (needs --play-remote and --mic; docs/AEC-REMOTE-SPEAKER.md)",
    )
    parser.add_argument(
        "--aec-lead-ms",
        type=int,
        default=50,
        help="with --aec: ms of reference fed ahead of the microphone (default: 50; more is worse "
        "as the path delay grows)",
    )
    parser.add_argument("--out", help="Output directory for generated WAVs")
""",
        ),
        (
            """    if args.play_remote:
        args.play = True
""",
            """    if args.play_remote:
        args.play = True
    if args.aec and not (args.play_remote and args.mic and args.barge_in):
        print("--aec needs --play-remote, --mic and --barge-in", file=sys.stderr)
        return 2
""",
        ),
    ],
}
