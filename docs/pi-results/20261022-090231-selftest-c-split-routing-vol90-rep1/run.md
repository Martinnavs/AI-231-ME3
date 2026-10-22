# selftest: C split routing vol90 rep1

- when: 2026-10-22 09:03:39
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=54.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "2", "out": "1", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -48.9 dBFS
- round trip (speaker -> air -> microphone): 94.2 ms
- its own voice above the room: median 4.3 dB, worst block 12.0 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 6/6 | True | 11 |
| silero_250ms | 6/6 | True | 8 |

Silero cost on this machine: 1.679 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.5629, 0.5054, 0.5468, 0.7085, 0.1212, 0.5385]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | 5.7 / 10.1 / 9.4 | {'energy': [], 'silero_250ms': [0.7], 'silero_150ms': [0.6]} |
| reply-02.wav | 1.8 / 8.0 / 7.0 | {'energy': [], 'silero_250ms': [1.8, 2.6], 'silero_150ms': [1.2, 1.4, 1.7]} |
| reply-03.wav | 5.6 / 10.8 / 14.2 | {'energy': [], 'silero_250ms': [0.7, 1.3], 'silero_150ms': [0.4, 0.6]} |
| reply-04.wav | 4.1 / 10.2 / 11.9 | {'energy': [], 'silero_250ms': [0.5], 'silero_150ms': [0.4]} |
| reply-05.wav | 4.5 / 12.0 / 12.9 | {'energy': [], 'silero_250ms': [4.5], 'silero_150ms': [3.2, 3.4, 4.4]} |
| reply-06.wav | 3.8 / 9.2 / 8.2 | {'energy': [], 'silero_250ms': [1.3], 'silero_150ms': [1.2]} |
