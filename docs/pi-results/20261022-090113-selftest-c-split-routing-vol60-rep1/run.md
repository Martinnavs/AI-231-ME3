# selftest: C split routing vol60 rep1

- when: 2026-10-22 09:02:22
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=54.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "2", "out": "1", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -48.9 dBFS
- round trip (speaker -> air -> microphone): 103.6 ms
- its own voice above the room: median 0.2 dB, worst block 2.9 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 0/6 | False | 0 |
| silero_250ms | 0/6 | False | 0 |

Silero cost on this machine: 1.715 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | 0.2 / 0.8 / 0.8 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | 0.2 / 0.5 / 0.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | 0.3 / 0.7 / 1.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | 0.2 / 0.8 / 0.9 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-05.wav | 0.1 / 1.4 / 1.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | 1.5 / 2.9 / 1.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
