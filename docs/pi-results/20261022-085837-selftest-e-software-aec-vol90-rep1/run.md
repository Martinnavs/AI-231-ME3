# selftest: E software aec vol90 rep1

- when: 2026-10-22 08:59:48
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=54.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -72.5 dBFS
- round trip (speaker -> air -> microphone): 212.2 ms
- its own voice above the room: median -14.1 dB, worst block 24.5 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 0/6 | False | 0 |
| silero_250ms | 0/6 | False | 0 |

Silero cost on this machine: 1.68 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -14.1 / 10.7 / 19.1 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | -14.5 / 2.8 / 6.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | -15.5 / 13.0 / 31.3 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | -13.6 / 8.7 / 1.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-05.wav | -14.2 / 2.6 / 5.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -8.2 / 24.5 / -3.8 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
