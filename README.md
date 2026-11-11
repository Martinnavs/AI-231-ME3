# AI-231 ME3: a voice assistant that runs on a laptop CPU and a Raspberry Pi

A spoken assistant for a home and a few "big tasks". The microphone is on the laptop; the answer plays from a USB loudspeaker on a Raspberry Pi 4 over one Ethernet cable. Everything that thinks runs on the laptop CPU: speech to text (Kroko streaming, Parakeet final), a 350M fine-tuned router that answers simple turns itself, text to speech (Kitten), and the agent server. The cloud handles the turns the router will not take (Gemini 3.1 flash-lite), web answers (Gemini with Google Search, then Tavily, then Wikipedia), and big tasks (a background "delegate" job on Claude Haiku 5.5 through OpenRouter, in a bubblewrap sandbox). The Pi runs no model.

Read first: `docs/HANDOFF.md` (the map), `docs/ARCHITECTURE.md` (how a turn flows), `docs/PROGRESS-2026-11-09.md` (latest work), `docs/ISSUES-2026-11-07.md` (what is open, section H is the newest). Full document list: `docs/INDEX.md`.

## Layout

| Folder | What is in it |
|---|---|
| `src/doc_chat/` | the live client: microphone, turn handling, barge-in, echo guard, addressee gate, playback |
| `src/lan_speech/` | the speech server (STT + TTS) |
| `src/voice_agent/` | the agent's MCP servers (docs, web, delegate, code sandbox, music) and the delegate sub-agent |
| `pipeline/` | the Pipecat project (a separate `uv` project and venv: agent server, router client, tools, chain state, scratchpad) |
| `config/` | settings, entity lists, the delegate config, `config/eval/` evaluation scenarios |
| `scripts/` | stack start-up (`scripts/stack/`), replays, doctors, `chain_eval.py` |
| `tests/`, `pipeline/tests/` | unit tests (root venv, pipeline venv) |
| `finetune/` | the router fine-tuning project (start at `finetune/README.md`) |
| `fd-benchmark/` | the class Full-Duplex-Bench harness (git-ignored clone; method in `docs/FDBENCH-FIRST-PASS.md`) |

## Setup

```
uv sync                # root environment (.venv)
make pipeline-sync     # pipeline/ environment (pipeline/.venv)
```

Secrets live under `~/.secrets/` (Gemini, OpenRouter, Tavily, Spotify) and are read by the code at run time. Never print or paste them. The Pi is reached by SSH key only.

## Make recipes

`make help` lists them all. Free means no network spend. "Needs you" means a microphone or a person.

| Recipe | What it does | Cost |
|---|---|---|
| `make check` / `make test` | ruff, format check and mypy; the root test suite | free |
| `make pipeline-check` / `make pipeline-test` | the same for `pipeline/` | free |
| `make check-all` / `make test-all` | both projects | free |
| `make test-chain` | only the tests behind the addressee gate, delegate doctor, chain state, scratchpad and chain evaluation (about 1 minute) | free |
| `make addressee-replay` (`DECL=1` adds the opt-in statement rule) | replays the addressee gate over the logged turns and the benchmark texts | free |
| `make doctor-tools` | what the delegate sandbox can do with git, GitHub and Hugging Face, and (optional) whether your profile is in place (add `--offline` by running the script directly to skip the two network checks) | free |
| `make chain-eval-list` | the 8 chain scenarios and the cost estimate of one pass | free |
| `make chain-eval-dry` | the chain evaluation against fakes: proves the harness, no network | free |
| `make chain-eval YES=1` (`SEED=n`, `SEEDS=k`, `ONLY=id,id`, `MAX_USD=0.19`) | the live chain evaluation: Gemini front agent, Tavily, the Haiku delegate | about $0.01 per scenario, about $0.08 for all 8; refuses to run without `YES=1` |
| `make agent-dryrun` (`LIMIT=n`) | the older stress dry run of the agent over `pipeline/config/eval/agent_prompts.jsonl` | cents |
| `make pi-detect`, `make up`, `make talk`, `make down`, `make status` | find the Pi, start the whole stack, talk to it, stop it, read-only status | live audio, needs you |
| `make up-laptop`, `make up-pi`, `make restart-agent` | start only part of the stack | needs you |
| `make calibrate` (`HEADSET=1`) | room calibration and the six baseline phrases | needs you at the mic |
| `make pi-check` (run on the Pi), `pi-env`, `pi-clips`, `pi-firewall`, `pi-tune`, `pi-netem MODE=...` | Pi set-up and a poor-link simulation | touches the Pi, ask first |
| `make spotify-login` | one-time Spotify login in the browser | needs you |

Do not run `uv run` or `uv sync` from a copied or symlinked tree; use the real repository.

## Test it yourself, in this order

1. **Everything is wired (free, about 3 minutes).** `make check-all` then `make test-all`. Expected: clean checks; tests pass except one known case, `tests/doc_chat/test_flow.py::test_llm_echo_is_accepted_without_documents` (it needs a speech server on port 8765). Ruff may also flag `scripts/pi_synth_report.py` (known, not ours).
2. **The newest work only (free).** `make test-chain`. Expected: both lines end in `passed` (about 441 and 45 tests).
3. **The addressee gate (free).** `make addressee-replay`. Expected: about 0.2 % of 1,479 logged real turns ignored with the default rules; run it with `DECL=1` to see the opt-in rule (about 2 %). The benchmark-clip block rates are printed below it (side talk 9 of 10; background and TV/radio 0 of 10 by default). The gate is off in the live client until you pass `--addressee log` (records its verdict, still answers) or `--addressee on` (drops turns judged side talk).
4. **The delegate sandbox (free).** `make doctor-tools`. Expected: OK for sandbox, git, local commit, GitHub read and Hugging Face reachable; MISSING (warnings only) for `gh`, `hf` and the credential folders; the last two lines say what the voice agent can and cannot do. It cannot push, by design. Two more optional lines report the owner profile (`config/profile.md` filled in; the delegate can read it): `docs/PROFILE.md`.
5. **The chain evaluation harness (free).** `make chain-eval-list` then `make chain-eval-dry`. Expected: 8 scenarios, an estimate of about $0.16 (the measured cost is about half), and 8 `yes` rows against the fakes with `total spend $0.000`.
6. **A live chain pass (spends).** Check the keys exist in `~/.secrets/`, then `make chain-eval YES=1 SEED=21` (or one scenario: `make chain-eval YES=1 ONLY=product_table`). It prints the estimate before any call, stops at 90 % of `MAX_USD`, and writes traces and the scratch workspaces to `out/chain_eval/<run>/`. Open `<run>/<scenario>-0/workspace/` to read the generated pages, `SOURCES.json` and `scratch/NOTES.md`. Strict pass rates on the last two passes were 2 of 8 and 3 of 8 (before the final fixes); a scenario can fail a check because of the harness, so read the trace before judging. The shared spend ledger is `out/chain_eval/ledger.json`; move it aside to start a fresh cap.
7. **Live voice (needs you).** `make pi-detect`, `make up`, `make talk` (`make down` to stop). To record the addressee gate's verdicts without changing behaviour: `make talk TALK_ARGS="--addressee log"` (`on` drops turns judged side talk). Compare against `docs/FDBENCH-FIRST-PASS.md` before and after.

## Where things are not finished

The GitHub Pages push is not built (it waits on the owner's account, a single-repository token held outside the sandbox, and a spoken yes). The addressee gate has not been measured live, and TV/radio needs a voice signal. Nothing from the last day went through an independent test or review pass. Details and the prune-later list: `docs/ISSUES-2026-11-07.md` sections H1 to H8.

## Rules for working here

Add files to git by explicit name, never `-A` or a whole folder. Do not push without asking. Never read or print anything under `~/.secrets/`. Do not touch the Pi or start servers on it without the owner's go. Details: `docs/HANDOFF.md` section 8.
