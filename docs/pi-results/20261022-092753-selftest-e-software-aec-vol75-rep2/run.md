# selftest: E software aec vol75 rep2

- when: 2026-10-22 09:29:05
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=55.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -67.8 dBFS
- round trip (speaker -> air -> microphone): 205.6 ms
- its own voice above the room: median -19.2 dB, worst block 8.0 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 0/6 | False | 0 |
| silero_250ms | 0/6 | False | 0 |

Silero cost on this machine: 1.673 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -10.3 / 8.0 / 8.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | -21.3 / -13.9 / -12.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | -21.5 / -3.8 / 1.3 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | -17.0 / -2.9 / 4.3 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-05.wav | -17.2 / 3.3 / -1.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -21.4 / -13.9 / -12.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
