# selftest: C split routing vol90

- when: 2026-10-22 08:50:05
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=53.0'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "2", "out": "1", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -48.8 dBFS
- round trip (speaker -> air -> microphone): 102.8 ms
- its own voice above the room: median 4.3 dB, worst block 12.0 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 6/6 | True | 10 |
| silero_250ms | 6/6 | True | 10 |

Silero cost on this machine: 1.803 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.5188, 0.5238, 0.5468, 0.7136, 0.2511, 0.5298]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | 5.8 / 10.2 / 9.3 | {'energy': [], 'silero_250ms': [1.5, 3.0], 'silero_150ms': [1.0, 1.4]} |
| reply-02.wav | 1.5 / 8.2 / 7.2 | {'energy': [], 'silero_250ms': [1.8, 2.6, 3.9], 'silero_150ms': [1.1, 1.7]} |
| reply-03.wav | 5.4 / 10.8 / 14.2 | {'energy': [], 'silero_250ms': [0.7, 1.3], 'silero_150ms': [0.4, 0.6]} |
| reply-04.wav | 4.2 / 10.1 / 12.0 | {'energy': [], 'silero_250ms': [0.5], 'silero_150ms': [0.4]} |
| reply-05.wav | 4.5 / 12.0 / 12.9 | {'energy': [], 'silero_250ms': [3.0], 'silero_150ms': [2.1, 2.9]} |
| reply-06.wav | 3.7 / 9.0 / 8.1 | {'energy': [], 'silero_250ms': [1.1], 'silero_150ms': [1.0]} |
