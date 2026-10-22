# selftest: C split routing

- when: 2026-10-22 08:46:19
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=53.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "2", "out": "1", "rate": 48000, "simulated_echo_db": null}
- empty-room level: -46.9 dBFS
- round trip (speaker -> air -> microphone): 104.6 ms
- its own voice above the room: median -2.0 dB, worst block 2.4 dB
- buffer underruns while playing: 0

| detector | clips where its own voice tripped it | first clip tripped | onsets in total |
|---|---|---|---|
| energy | 0/6 | False | 0 |
| silero_150ms | 0/6 | False | 0 |
| silero_250ms | 0/6 | False | 0 |

Silero cost on this machine: 1.823 ms per 32 ms window (real-time needs < 32). Speech windows during playback: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

| clip | above room (median / p95 / first second, dB) | onsets after start (s) |
|---|---|---|
| reply-01.wav | -0.6 / 2.4 / 5.2 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-02.wav | -2.0 / -1.5 / -1.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-03.wav | -2.0 / -1.6 / -1.3 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-04.wav | -1.9 / -1.5 / -1.4 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-05.wav | -2.0 / -1.5 / -1.5 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
| reply-06.wav | -2.0 / -1.6 / -1.6 | {'energy': [], 'silero_250ms': [], 'silero_150ms': []} |
