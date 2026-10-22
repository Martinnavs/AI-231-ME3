# selftest: E software aec vol60 rep1

- when: 2026-10-22 08:55:38
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=55.0'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -73.3 dBFS
- round trip (speaker -> air -> microphone): 205.5 ms
- its own voice above the room: median -10.8 dB, worst block 17.3 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 4/6 | True | 4 |
| silero_250ms | 2/6 | True | 3 |

Silero cost on this machine: 1.663 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.1624, 0.0566, 0.2689, 0.018, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -3.4 / 15.2 / 15.5 | {'energy': [], 'silero_250ms': [1.1, 1.7], 'silero_150ms': [1.0]} |
| reply-02.wav | -5.2 / 10.4 / -11.5 | {'energy': [], 'silero_250ms': [], 'silero_150ms': [4.9]} |
| reply-03.wav | -8.6 / 15.7 / 19.1 | {'energy': [], 'silero_250ms': [0.6], 'silero_150ms': [0.5]} |
| reply-04.wav | -16.7 / 5.2 / -12.3 | {'energy': [], 'silero_250ms': [], 'silero_150ms': [4.8]} |
| reply-05.wav | -12.9 / 17.3 / 10.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -17.5 / -7.9 / -12.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
