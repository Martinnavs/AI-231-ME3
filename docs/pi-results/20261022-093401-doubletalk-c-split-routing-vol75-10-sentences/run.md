# doubletalk: C split routing vol75 (10 sentences)

- when: 2026-10-22 09:37:07
- hardware: {"platform": "Linux-6.12.75+rpt-rpi-v8-aarch64-with-glibc2.41", "python": "3.12.15", "model": "Raspberry Pi 4 Model B Rev 1.2", "temperature": "temp=53.5'C", "throttled": "throttled=0x0", "onnxruntime": "1.31.0", "sounddevice": "0.5.6"}
- devices: {"in": "2", "out": "1", "rate": 48000, "simulated_echo_db": null}

| trial | its own voice tripped (before beep) | you, seconds after the beep |
|---|---|---|
| 1 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': [1.8], 'silero_150ms': [1.7]} |
| 2 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': '-', 'silero_150ms': '-'} |
| 3 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': '-', 'silero_150ms': '-'} |
| 4 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': [1.5], 'silero_150ms': [1.5]} |
| 5 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': [1.4], 'silero_150ms': [1.3]} |
| 6 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': [2.5], 'silero_150ms': [1.6]} |
| 7 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': [1.2], 'silero_150ms': [1.1]} |
| 8 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': [2.0], 'silero_150ms': [1.3]} |
| 9 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': '-', 'silero_150ms': '-'} |
| 10 | {'energy': 0, 'silero_250ms': 0, 'silero_150ms': 0} | {'energy': '-', 'silero_250ms': '-', 'silero_150ms': [2.1]} |

Next: copy this folder to the laptop and run scripts/pi_double_talk_score.py on it.
