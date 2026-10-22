# selftest: E software aec vol90

- when: 2026-10-22 08:51:58
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=54.0'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "3", "out": "4", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -84.9 dBFS
- round trip (speaker -> air -> microphone): 948.4 ms
- its own voice above the room: median 0.1 dB, worst block 26.5 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 0/6 | False | 0 |
| silero_250ms | 0/6 | False | 0 |

Silero cost on this machine: 1.744 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -1.0 / 15.5 / 7.6 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | 1.4 / 19.4 / 18.1 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | 0.2 / 20.4 / 29.6 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | 0.0 / 23.0 / 25.8 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-05.wav | -1.0 / 19.0 / 14.1 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | 2.2 / 26.5 / 22.9 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
