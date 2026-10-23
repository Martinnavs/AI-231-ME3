# Pi bridge: Pi microphone and speaker, laptop speech models

`scripts/pi_bridge.py` runs on the Raspberry Pi. It listens through the Pi's microphone, sends the audio over the LAN to the laptop's `lan_speech` server (Parakeet STT), says the reply through the Pi's speaker using the laptop's TTS (Supertonic), and stops talking if the user talks over it. The Pi runs **no speech model**; the only model on it is the Silero VAD (barge-in), as the professor's rule allows.

The setup the project is moving to instead, with the laptop owning the microphone and the Pi only the loudspeaker, is in `docs/PI-REMOTE-SPEAKER.md`; this bridge keeps the Pi-microphone path working.

There is no language model: the reply is `You said: <transcript>`, so the loop measures only STT and TTS. Measured 2026-10-23, one speaker, one room, 10 turns per run.

```mermaid
flowchart LR
    subgraph Pi
      MIC[USB mic UACDemo<br/>48 kHz] -->|decimate to 16 kHz| HUB[MicHub]
      HUB -->|blocks| CLI[listen_turn<br/>pause detector]
      HUB -->|blocks while speaking| VAD[Silero VAD<br/>barge-in]
      PL[ResamplingPlayer<br/>44.1 to 48 kHz] --> SPK[USB speaker CD002<br/>48 kHz]
      VAD -->|abort| PL
    end
    subgraph Laptop
      SRV[lan_speech server<br/>Parakeet STT + Supertonic TTS]
    end
    CLI -->|16 kHz PCM, finalize| SRV
    SRV -->|transcript| CLI
    CLI -->|reply text| SRV
    SRV -->|TTS 44.1 kHz PCM| PL
```

## Run it

**Network.** The Pi and laptop are joined by an Ethernet cable (link-local `169.254.x.x`). The laptop's firewall must let the Pi reach the server port (it rejects it by default on the `public` zone); a runtime-only rule is enough: `sudo firewall-cmd --zone=trusted --change-interface=<cable interface>`.

**Laptop** (bind to the cable address; off loopback a token is mandatory):

```bash
export LAN_SPEECH_TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")   # never print it
uv run --group laptop python -W ignore -m lan_speech --host <laptop cable ip> --port 8770 \
    --stt-model parakeet --entities config/entities-docs.json
```

Put the same token on the Pi in a file only you can read (`umask 077; cat > ~/.config/lan_speech_token`).

**Pi** (inside the repository, with the venv from `docs/PI-TEST-PLAN.md`):

```bash
uv pip install --python .venv/bin/python "websockets>=17.2"          # once; not in the pi group
export LD_LIBRARY_PATH=$HOME/.local/portaudio/usr/lib/aarch64-linux-gnu   # if PortAudio is unpacked in ~/.local
export LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token)
.venv/bin/python -u scripts/pi_bridge.py --server ws://<laptop cable ip>:8770 --pause 1.2 \
    --latency-log out/pi_bridge/latency.jsonl
```

Say "goodbye" to stop. `kill -USR1 <pid>` prints where every asyncio task is waiting, and the status line every 2 s shows the phase and the microphone peak.

## Things that would otherwise cost an hour

| Symptom | Cause | What the bridge does |
|---|---|---|
| No sound, no error | The ALSA `default` device on this Pi is HDMI, not the USB speaker (pipewire-alsa is only unpacked under `~/.local`) | Plays straight to `CD002` |
| `Invalid sample rate` on the speaker | The USB speaker opens only at 48 kHz; the TTS is 44.1 kHz | `ResamplingPlayer` resamples each chunk (polyphase) |
| `No input device matching 'default'` | ALSA default has no input; the USB mic opens only at 48 kHz | `DecimatingMicHub` opens 48 kHz and decimates to 16 kHz with a filter that keeps state across blocks |
| Server unreachable, `No route to host`, ping works | Laptop `firewalld` rejects the port on the cable interface | Open the port or move the interface to `trusted` (runtime) |
| Long phrases arrive as fragments, each with its own reply | The pause detector counts dips between words as a pause: speech peaks only about 11-15 dB above the room on this mic (-26 to -41 dBFS over a -48 dBFS floor) | `--pause 1.2` (default 0.6); a proper fix is a smarter end-of-turn rule |
| 200 ms lost before every reply | A new Silero session was built per reply (slow on the Pi) | One loaded model, `SileroOnset.reset()` between replies |

## Latency

From the moment the user stops speaking (last sample above the client's loudness threshold) to the first audio handed to the speaker, plus the device's reported 32 ms buffer. 10 turns each (5 short, 5 of 13-15 words), `--pause 1.2`, one speaker, no interruptions. The sound time is an estimate: it was not measured acoustically.

| Stage | Before caching Silero, p50 | After, p50 | After, p95 |
|---|---|---|---|
| Pause hold (client waits for quiet) | 1202 ms | 1202 ms | 1203 ms |
| Server final transcript | 433 ms | 436 ms | 586 ms |
| Bridge overhead before TTS is requested | 203 ms | 2 ms | 5 ms |
| TTS first audio | 375 ms | 317 ms | 338 ms |
| **To first audio chunk** | **2242 ms** | **1953 ms** | **2122 ms** |
| To sound (adds the 32 ms buffer) | 2274 ms | 1985 ms | 2154 ms |

The server stage grows with length: 310-410 ms for 2-4 words, 460-590 ms for 13-15 words. The TTS drop of about 58 ms came with the Silero change but nothing on that path was touched; the cause is unverified.

## What this does not show

- Barge-in was not tested on purpose in these runs (the interrupt path ran in earlier exploratory runs only); no barge-in numbers.
- Latency is not measured acoustically (microphone hearing the speaker), and the "end of speech" is the client's loudness detector, which on this weak microphone can be earlier than the true end.
- Accuracy was not scored. Visible errors: the first words dropped in 3 of 10 turns in one run ("What time is it?" heard as "Is it?"), and "tomorrow" heard as "to morrow".
- One speaker, one room, one run of 10 turns per row.
- The firewall change is runtime-only and the server token was generated for the session; neither is persisted.
