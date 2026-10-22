# selftest: E software aec vol90 rep2

- when: 2026-10-22 09:01:08
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=55.0'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "1", "out": "2", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -77.6 dBFS
- round trip (speaker -> air -> microphone): 208.7 ms
- its own voice above the room: median -7.4 dB, worst block 23.9 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 0/6 | False | 0 |
| silero_250ms | 0/6 | False | 0 |

Silero cost on this machine: 1.663 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0189, 0.0, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -8.6 / 23.9 / 31.5 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | -8.7 / 9.9 / 2.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | -7.4 / 20.8 / 31.9 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | -7.4 / 17.5 / 24.3 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-05.wav | -5.8 / 9.3 / 5.1 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -6.5 / 18.5 / 24.6 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
