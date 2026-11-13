# ME3: a voice assistant on a laptop CPU and a Raspberry Pi

**BLUF.** You talk to a laptop microphone. Speech recognition, speech synthesis and a 350M tool router run on the laptop CPU; a Raspberry Pi 4 runs the agent and its tools and plays the answer on a USB loudspeaker; Gemini and web search are in the cloud. It does home commands (shown on a panel), music (Spotify), questions with web search, and "big tasks" (builds a page, publishes it to GitHub Pages). End of speech to first sound: **1.69 s mean, 2.48 s p95** on the class benchmark. Weak spots: the first model token is slow (p95 1.92 s), it still answers side talk (5 of 6 clips), and every number below is one run on 12 clips per group.

```
laptop mic -> VAD -> Parakeet ASR -> [router 350M: one tool call, or escalate] -> Pi: agent (Pipecat) -> Gemini + tools (MCP)
                                                                                    |-> home dashboard   |-> Spotify   |-> web, docs, delegate
laptop Kitten TTS -> Pi mixer -> USB loudspeaker              (barge-in: speech detected -> stop playback and the model)
```

| Where | What |
|---|---|
| Laptop (CPU only) | Silero VAD, Parakeet speech recognition, Kitten TTS, the router (`llama-server`), the live client `doc_chat` (barge-in, echo guard, loudness and addressee gates) |
| Raspberry Pi 4 | the agent server and its MCP servers (docs, web, search, delegate, music, time, speech), the home dashboard, the apps server, the loudspeaker mixer |
| Cloud | Gemini (the agent's model), Tavily and Google search, Claude Haiku (the delegate that builds pages) |
| Link | one Ethernet cable laptop to Pi (link-local, about 1.5 ms); the Pi also needs internet |

## Results (final build, Pi, one run, class benchmark `fdbench`, seed 922546)

| Measure | Result | Target |
|---|---|---|
| End of speech to first audio, mean / p95 (30 phrases) | **1.69 / 2.48 s** | < 4 s |
| ASR final / LLM first token / TTS first audio, mean | 0.33 / 1.12 / 0.23 s | < 0.8 / 1.5 / 0.6 s |
| Barge-in, playback stopped, mean / p95 | 0.65 / 0.89 s | < 0.8 s |
| Turn-taking (12 clips) / interruption v1 (11) | 12/12 / 11/11 answered | |
| Interruption v1.5, stopped and answered | 11/12 | |
| False turn-taking on TV, background, side talk, noise (24 clips), lower is better | **7/24** (TV 0, noise 0, background 2/6, side talk 5/6); 19/24 without the gates | |
| Pause take-over / backchannel resumed | 17 % and 25 % / 83 % | |

Not measured on the final build: word error rate (the phrase draw broke it), a spoken session with hedging on, a noisy real room. Method and every table: `docs/PROGRESS-2026-11-11.md` sections 21 to 29.

## Prerequisites (set up before the quickstart)

| Need | Why | How |
|---|---|---|
| Laptop: Linux x64, 4+ cores, microphone, [`uv`](https://docs.astral.sh/uv/), `git`, `curl`, `rsync`, PipeWire/PulseAudio (`pactl`) | the live stack | tested on Arch, HP EliteBook 840 G8 |
| Raspberry Pi 4 (4 GB), Raspberry Pi OS 64-bit, USB loudspeaker, Ethernet cable to the laptop, ssh key login | the harness and the speaker | on the Pi: `sudo apt install python3-venv python3-dev build-essential libsndfile1 libportaudio2 rsync` |
| The Pi has internet (Wi-Fi) | Gemini, search | a Wi-Fi profile with its password stored system-wide (`sudo nmcli device wifi connect ...`), or a keyring-less headless login fails |
| Gemini API key | the agent's model | <https://aistudio.google.com/apikey> to `~/.secrets/google-ai-studio/api-key.txt` |
| Tavily key (free, 1,000 credits a month) | web research | <https://tavily.com> to `~/.secrets/tavily/api-key.txt` |
| OpenRouter key | the delegate (Claude Haiku) and the benchmark judge | `~/.secrets/openrouter/api-key.txt` |
| **Spotify**: a Premium account, the Spotify desktop app logged in on the laptop, a developer app | music (searched by the Web API, played by the app into a private sink that the Pi speaker replays) | <https://developer.spotify.com/dashboard>: create an app, redirect URI `http://127.0.0.1:8888/callback`, save the client id to `~/.secrets/spotify/client-id.txt` and the secret to `~/.secrets/spotify/api-key.txt`, then `make spotify-login` once |
| A shared session token | every laptop/Pi connection | `umask 077; python3 -c "import secrets;print(secrets.token_urlsafe(24))" > ~/.config/lan_speech_token`, the same file on the Pi |
| Keys on the Pi | the agent, search and the delegate run there | `scp` the key files for Gemini, Tavily, OpenRouter (and Google Maps) to the same paths under the Pi's `~/.secrets/` (`chmod 700 ~/.secrets`, `chmod 600` the files); the Spotify files stay on the laptop |
| Optional: Google Maps key (Places API New) | nearby-places tools | `~/.secrets/google-maps/api-key.txt` |
| Optional: GitHub token and a public demo repository | "publish it" to GitHub Pages | `config/publish.toml` on the Pi only (`repo = "https://github.com/<you>/<repo>"`), token in `~/.secrets/github/pat` on the Pi (see `docs/PUBLISH-PAGES.md`) |
| Optional: your profile | "make me a page about me" | `cp config/profile.example.md config/profile.md`, fill it in (git-ignored; `docs/PROFILE.md`) |

Keys are read by the code at run time and never printed; `~/.secrets/` is never committed.

## Quickstart

```bash
git clone https://github.com/Martinnavs/AI-231-ME3 && cd AI-231-ME3
uv sync --group laptop && make pipeline-sync          # the two Python environments
uv run python scripts/fetch_laptop_models.py          # speech models into models/ (checksummed)
make setup-router                                     # the router GGUF (Hugging Face) + a prebuilt llama.cpp, ~250 MB
make setup-kitten                                     # the Kitten TTS voice (not a project dependency)
ssh <pi> 'mkdir -p ~/Documents/AI-231-ME3'            # where the repo lives on the Pi
PI_USER=<pi user> PI_KEY=~/.ssh/<key> make pi-detect  # finds the Pi on the cable, writes config/pi.env
PI_RESTART=none make pi-sync && make pi-env           # this repo's code (a git ref, reproducible) and pi.env to the Pi
ssh <pi> 'cd ~/Documents/AI-231-ME3 && deploy/install-pi.sh --venvs'   # Pi environments and systemd units
make pi-firewall && make pi-tune                      # once: open the cable ports (sudo), Pi performance settings
make calibrate                                        # your room and voice, about 4 minutes; `make talk` reads the result
make up                                               # router, speech, Pi services; prints the home dashboard address
make talk                                             # speak
```

Say "turn the lights purple", "set a timer for an hour and a half", "who is the current mayor of Cebu City", "play something by Eraserheads", "make me a one-page site about sourdough", "publish it". Open the dashboard address that `make up` prints in a laptop browser to see the home commands land. `make down` stops everything; `make status` is read-only. Day-to-day: `make pi-sync` after a commit, `make pi-mode MODE=demo|benchmark` for the search budget.

## Reproduce the numbers

| Step | How |
|---|---|
| Unit tests (free, 3 minutes) | `make check-all && make test-all` (one known failure that needs a running speech server) |
| Latency and conversation benchmark | the class harness `fd-benchmark/` (provided by the course, git-ignored) over two virtual audio cables: `docs/FDBENCH-FIRST-PASS.md` has the method and commands; this build's runs are in `docs/PROGRESS-2026-11-11.md` section 29. Use `make calibrate` first, `make pi-mode MODE=benchmark` (no Tavily credits), `--rounds 12 --seed 922546`. About 90 minutes, about $0.30 for the judge |
| The loudness gate, offline and free | `scripts/voice_gate_replay.py --gap 10` |
| The live test script (music, talking background) | `scripts/live_test_report.py --print`, speak it, `scripts/live_test_report.py` scores the session |

## The router model

A Granite 4.0 H 350M with a LoRA adapter, distilled from a 1B teacher on template-generated data; it writes one tool call per request or `escalate`. `make setup-router` downloads the ready GGUF: <https://huggingface.co/martinnavs/granite-4.0-h-350m-router-kd-web4-s0-GGUF> (the adapter: <https://huggingface.co/martinnavs/granite-4.0-h-350m-router-kd-web4-s0>). To retrain it and follow the methodology (data generator, teacher, distillation, evaluation, model card): <https://github.com/Martinnavs/ME3-LoRA-training>, then `scripts/setup_router_gguf.sh` with `ROUTER_REPO=<your adapter>` turns your adapter into the GGUF `make up` serves.

## Where everything else is

| Need | Read |
|---|---|
| Every make recipe, test order, layout | [`docs/USAGE.md`](docs/USAGE.md) |
| How a turn flows, ports, servers | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| The home dashboard (open values, API) | [`docs/HOME-DASHBOARD.md`](docs/HOME-DASHBOARD.md) |
| The Pi: commands, Wi-Fi, systemd | [`deploy/PI-HARNESS-COMMANDS.md`](deploy/PI-HARNESS-COMMANDS.md), `make pi-sync` |
| Latest measurements and every decision | [`docs/PROGRESS-2026-11-11.md`](docs/PROGRESS-2026-11-11.md) |
| Industry comparison, latency work | [`docs/INDUSTRY-COMPARISON.md`](docs/INDUSTRY-COMPARISON.md), [`docs/LATENCY.md`](docs/LATENCY.md) |
| Publishing pages, the owner profile | [`docs/PUBLISH-PAGES.md`](docs/PUBLISH-PAGES.md), [`docs/PROFILE.md`](docs/PROFILE.md) |
| The map for a new maintainer | [`docs/HANDOFF.md`](docs/HANDOFF.md) |

`main` holds only what runs the assistant; research, experiments and history are on the `agy/interrupts` branch (`tools/promote/README.md` explains the split).
