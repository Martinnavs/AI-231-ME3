# Guardrails for the unattended agy run (tickets 21 Phase 2 → 24)

This session runs with `--dangerously-skip-permissions`, so nobody will approve or stop individual actions. These rules replace that review. They **override** anything looser in `RUNNER.md` or a ticket. When a rule and the task conflict, the rule wins: stop and report `BLOCKED`.

## Where you work
- Work only inside this git worktree (branch `agy/laptop-speech`). Never `cd` into or write to the main checkout `/mnt/School/PhD/AI231/AI-231-ME3`, `$HOME`, or anywhere outside the worktree. The only exceptions are reading `models/` through the symlink, reading the uv/HF caches, and `/tmp`.
- `models/` is a symlink to the main checkout's model directory. You may **add** new files under `models/supertonic-3/`. Never delete, move or overwrite existing model files.
- Run the tickets in order: **21 Phase 2**, then **24**. Make one commit per ticket on this branch, with messages `21: …` and `24: …`.

## Never
- `git push`, `git merge`, `git rebase`, `git reset --hard`, `git clean`, `git checkout` of other branches, `git worktree remove`, `git branch -D`, or any history rewrite.
- `sudo`, system package managers (`pacman`, `yay`, `apt`), editing shell rc files, systemd units, `/etc`, or global git/uv config.
- Network calls other than these:
  1. PyPI via `uv`, for the packages each ticket's *Dependencies/license* lists.
  2. Hugging Face downloads of the model files the tickets name.
  3. Vendor documentation pages.
- No calls to any LLM or speech API, including the Qwen/vLLM endpoint in `~/.config/opencode/opencode.json`, Anthropic, Deepgram, Cartesia or Groq. The Qwen path in ticket 24 is **scaffold only**: unit tests use a fake, and the live test is marked `live` and left for the human.
- Reading or printing secrets. Never open `.env`, `~/.local/share/opencode/auth.json`, or any `*token*`/`*key*` file, and never echo env vars matching `*KEY*|*TOKEN*|*SECRET*`. Reading `~/.config/opencode/opencode.json` is allowed only to learn the provider's `baseURL`, model id and env-var *name*; read it in code at runtime, never copy its values into the repo.
- Packages not listed in a ticket's *Dependencies/license*. Adding anything to the default dependency group (the Pi install must not change), or editing `uv.lock` other than through `uv add --group laptop`.
- Changing `src/audio_engine`, `src/voice_agent`, `src/mcp_servers`, `src/bench`, the existing tests, or any other ticket file. You may only append to the Execution Log of 21 and 24.
- Enabling `live` or `pi` tests, or setting `VOICE_AGENT_LIVE` / `VOICE_AGENT_PI`.
- Leaving background processes running. Every server you start for a test or benchmark must be stopped before you move on; check with `pgrep -af lan_speech`.

## Allowed side effects
- `scripts/power_mode.sh max` before a benchmark and `scripts/power_mode.sh balanced` right after, even if the benchmark fails. Use no other power or CPU tuning.
- Binding a server to `127.0.0.1` only. Never use `0.0.0.0` or a LAN address in tests or benchmarks.

## When to stop
- Stop with `BLOCKED` on any of these: a failing Proof row you can't fix within two attempts, an Assumption that turns out false, a Contract you would need to change, or a decision the ticket doesn't settle. Write the reason in the ticket's Execution Log, commit what is green, and end the session. Never work around a guardrail.
- When both tickets are DONE (or one is BLOCKED), finish with:
  1. `git status` clean on the branch,
  2. `pgrep -af "lan_speech|doc_chat"` empty,
  3. power profile back on `balanced`,
  4. a final summary listing each ticket's status line, the Proof checkmarks and the commit SHAs.
