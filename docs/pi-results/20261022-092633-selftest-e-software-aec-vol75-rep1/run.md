# selftest: E software aec vol75 rep1

- when: 2026-10-22 09:27:44
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=54.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -83.4 dBFS
- round trip (speaker -> air -> microphone): 973.8 ms
- its own voice above the room: median -6.5 dB, worst block 10.0 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 0/6 | False | 0 |
| silero_250ms | 0/6 | False | 0 |

Silero cost on this machine: 1.773 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -6.8 / 0.9 / 11.0 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | -6.4 / 7.6 / -2.6 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | -7.1 / 1.2 / -3.5 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | -6.4 / 5.1 / -3.9 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-05.wav | -5.9 / 4.0 / -0.7 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -6.5 / 10.0 / 17.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
