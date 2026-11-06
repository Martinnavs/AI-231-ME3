# HARNESS=pi: the exact commands (ticket set Y, step Y6)

Nothing here has been run on the Pi. Run only after the owner's go. Laptop commands run from the
repo root; `<PI>` is `$PI_USER@$PI_V4` from `config/pi.env` (refresh with `make pi-detect` first,
the Pi may have changed address after a reboot).

## 0. apt packages (on the Pi, needs sudo)

Already present: rsync, python3, pip3, ffmpeg, PipeWire. Needed:

    sudo apt install python3-venv python3-dev build-essential libsndfile1 libportaudio2

(`python3-venv` for `python3 -m venv`; `libsndfile1` for soundfile; `libportaudio2` for sounddevice,
probably already there from the speaker. `build-essential`/`python3-dev` only if a wheel is missing
for aarch64; numba 0.68.1 has one.) No uv, mpv or yt-dlp is needed on the Pi.

## 1. Keys (owner, once; this document never reads them)

    ssh <PI> 'mkdir -p ~/.secrets/google-ai-studio ~/.secrets/tavily && chmod 700 ~/.secrets'
    scp ~/.secrets/google-ai-studio/api-key.txt <PI>:.secrets/google-ai-studio/
    scp ~/.secrets/tavily/api-key.txt <PI>:.secrets/tavily/
    ssh <PI> 'chmod 600 ~/.secrets/*/api-key.txt'

The session token (`~/.config/lan_speech_token`) is already on the Pi (the speaker uses it).

## 2. Copy the code (laptop)

    make pi-detect
    rsync -a --delete-excluded -e "ssh -i $PI_KEY" \
      --exclude .git --exclude .venv --exclude .venv-agent --exclude .scratch --exclude out \
      --exclude models --exclude finetune --exclude resources --exclude bench --exclude __pycache__ \
      ./ <PI>:<PI_REPO>/
    make pi-env

(Needed on the Pi: `src/`, `pipeline/src/`, `scripts/`, `config/`, `deploy/`, `corpus/`, `assets/`.
Check `corpus/` and `assets/` are small enough before the first copy: `du -sh corpus assets`.)

## 3. Build the venvs (on the Pi, in the repo)

    cd <PI_REPO> && deploy/install-pi.sh --venvs

This runs `pip install -r deploy/pi-mcp-requirements.txt` into a new `.venv-mcp` (the Pi's `.venv` is uv-made, has no pip, and stays the speaker's) and
`pip install -r deploy/pi-agent-requirements.txt` into a new `.venv-agent` (two venvs because
mcp-server-time needs mcp>=2.3 and fastmcp needs mcp<2), then `pi_check.sh`, then installs and
enables the `pi-speaker`, `pi-device-mcp` and `pi-agent` user units (linger on). Starting is separate.

## 4. Start and check (laptop)

    make pi-firewall                         # once per laptop (sudo): the Pi must reach 8089, 8770, 8796 over the cable
    make pi-tune                             # once (sudo on the Pi): CPU governor performance, Wi-Fi power save off, laptop profile performance
    HARNESS=pi make up PI_START=systemd      # router on the cable address, speech, resolver, then the Pi units
    HARNESS=pi make status
    HARNESS=pi scripts/stack/talk.sh         # doc_chat with --agent-url ws://<PI_V4>:8781

`make pi-firewall` opens the three ports in the cable interface's firewalld zone. That zone is often shared with Wi-Fi
("public"), so the firewall stops blocking those ports on Wi-Fi too; the services still bind to the cable address only,
which is what keeps them off Wi-Fi.

Without systemd: `HARNESS=pi make up` starts the Pi agent and apps server over ssh (with the same
timeout as the speaker).

## 5. Roll back

    HARNESS=pi make down; make up            # the laptop harness is the default and is unchanged
    ssh <PI> 'systemctl --user disable --now pi-agent'
