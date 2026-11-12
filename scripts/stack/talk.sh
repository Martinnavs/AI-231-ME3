#!/usr/bin/env bash
# THE LIVE LOOP in the foreground (needs the microphone): laptop mic -> speech server -> agent server -> Kitten -> Pi speaker.
# Variables: NOTE, SPEECH_PORT (default PORT_SPEECH), TALK_ARGS (extra doc_chat flags), DRY=1
# The gates against speech that is not for the assistant (--addressee on, --voice-gate-db auto) are on
# and read their levels from out/calibration.json (make calibrate): no flags to set after a
# calibration. TALK_ARGS comes last, so it can override ("--voice-gate-db 0" turns the loudness gate off).
source "$(dirname "$0")/lib.sh"
load_env
need_token
cd "$STACK_REPO" || exit 1
# preflight: the client needs the speech server and the agent server; without them it only prints
# "speech server unavailable" and make reports "Error 2"
reach() { timeout 2 bash -c "exec 3<>/dev/tcp/$1/$2" 2> /dev/null; }
if [ "$DRY" != 1 ]; then
  agent_host=${AGENT_URL#ws://}; agent_host=${agent_host%%:*}
  reach "$LAPTOP_IP" "${SPEECH_PORT:-$PORT_SPEECH}" || die "the speech server is not running at $LAPTOP_IP:${SPEECH_PORT:-$PORT_SPEECH}: run make up first (make status shows what is up)"
  reach "$agent_host" "$PORT_AGENT" || die "the agent server is not reachable at $AGENT_URL: run make up first (make status shows what is up)"
fi
RUN=out/sessions/stack-$(date +%m%d-%H%M)
mkdir -p "$STACK_DIR"; [ "$DRY" = 1 ] || echo "$RUN" >"$STACK_DIR/last_session"
# shellcheck disable=SC2086
run uv run --group laptop python -W ignore -u -m doc_chat --mic --llm agent --no-docs --slot "doc+term?" --stream \
  --server "ws://$LAPTOP_IP:${SPEECH_PORT:-$PORT_SPEECH}" --play-remote "ws://$PI_V4:$PORT_PI_SPEAKER" --agent-url "$AGENT_URL" --vad both --vad-confidence 0.5 \
  --silero-kofn 7/10 --level-kofn 3/8 --barge-echo-quantile 0.95 --barge-echo-window 60 --barge-echo-margin 1.2 \
  --barge-echo-prior 0.8 --trace-guard --history 4 --max-words 90 --aec --duck-db -15 --strip-echo-prefix \
  --barge-warmup-s 1.0 --barge-min-bar 0.06 --fillers --addressee on --voice-gate-db auto --record "$RUN" --note "${NOTE:-stack: router ${ROUTER_MODEL:-?}, laptop mic, Pi speaker}" ${TALK_ARGS:-}
