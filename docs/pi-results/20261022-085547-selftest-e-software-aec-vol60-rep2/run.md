# selftest: E software aec vol60 rep2

- when: 2026-10-22 08:56:58
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=55.0'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -83.2 dBFS
- round trip (speaker -> air -> microphone): 203.8 ms
- its own voice above the room: median 14.1 dB, worst block 27.0 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 3/6 | False | 3 |
| silero_250ms | 3/6 | False | 4 |

Silero cost on this machine: 1.851 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0226, 0.5105, 0.4298, 0.0, 0.4615]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -7.7 / 21.2 / 24.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | -1.7 / 16.9 / 5.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | 20.1 / 26.1 / 30.2 | {'energy': [], 'silero_250ms': [0.8, 1.4], 'silero_150ms': [0.7]} |
| reply-04.wav | 15.2 / 26.5 / 26.5 | {'energy': [], 'silero_250ms': [3.5], 'silero_150ms': [3.1]} |
| reply-05.wav | 18.6 / 27.0 / 19.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | 12.9 / 24.6 / 23.0 | {'energy': [], 'silero_250ms': [1.3], 'silero_150ms': [1.2]} |
