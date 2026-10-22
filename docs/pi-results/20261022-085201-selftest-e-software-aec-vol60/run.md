# selftest: E software aec vol60

- when: 2026-10-22 08:53:12
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=54.0'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -57.5 dBFS
- round trip (speaker -> air -> microphone): 205.2 ms
- its own voice above the room: median -31.1 dB, worst block 3.2 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 4/6 | True | 4 |
| silero_250ms | 4/6 | True | 4 |

Silero cost on this machine: 1.646 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.1453, 0.1019, 0.0, 0.0359, 0.0, 0.036]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -21.4 / -0.3 / -21.0 | {'energy': [], 'silero_250ms': [4.0], 'silero_150ms': [3.9]} |
| reply-02.wav | -15.3 / -2.4 / -4.2 | {'energy': [], 'silero_250ms': [4.8], 'silero_150ms': [4.7]} |
| reply-03.wav | -33.4 / -9.3 / -1.3 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | -31.8 / -3.3 / -27.5 | {'energy': [], 'silero_250ms': [8.1], 'silero_150ms': [8.0]} |
| reply-05.wav | -32.5 / 3.2 / -27.1 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -30.3 / -5.4 / -16.6 | {'energy': [], 'silero_250ms': [4.5], 'silero_150ms': [4.4]} |
