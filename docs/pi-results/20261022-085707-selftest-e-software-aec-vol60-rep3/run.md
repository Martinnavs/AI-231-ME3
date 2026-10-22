# selftest: E software aec vol60 rep3

- when: 2026-10-22 08:58:19
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=54.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -84.6 dBFS
- round trip (speaker -> air -> microphone): 201.2 ms
- its own voice above the room: median -5.2 dB, worst block 26.4 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 1/6 | False | 1 |
| silero_250ms | 1/6 | False | 1 |

Silero cost on this machine: 1.864 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0236, 0.0449, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -6.0 / 23.6 / 26.1 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | -4.8 / 15.8 / 3.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | -1.4 / 26.4 / 30.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | -5.4 / 23.2 / 0.7 | {'energy': [], 'silero_250ms': [4.9], 'silero_150ms': [4.8]} |
| reply-05.wav | -5.1 / 4.9 / -1.1 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -5.8 / 0.7 / -1.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
