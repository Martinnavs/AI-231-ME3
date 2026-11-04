#!/usr/bin/env bash
# THE LIVE LOOP: laptop mic -> speech server -> agent server -> Kitten -> Pi speaker. Say the lines of docs/stress/spoken-test-v3.md.
source "$(dirname "$0")/env.sh"; cd "$REPO" || exit 1
export LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token)
RUN=out/sessions/spoken-v3-$(date +%m%d-%H%M); echo "$RUN" > "$D/last_session"
exec uv run --group laptop python -W ignore -u -m doc_chat --mic --llm agent --no-docs --stream \
  --server ws://$LAPTOP_IP:8770 --play-remote ws://$PI_V4:8780 --vad both --vad-confidence 0.5 \
  --silero-kofn 7/10 --level-kofn 3/8 --barge-echo-quantile 0.95 --barge-echo-window 60 --barge-echo-margin 1.2 \
  --barge-echo-prior 0.8 --trace-guard --history 4 --max-words 90 --aec --duck-db -15 --strip-echo-prefix \
  --barge-warmup-s 1.0 --barge-min-bar 0.06 --fillers --record "$RUN" --note "${NOTE:-spoken v3: router $ROUTER_MODEL, call_policy + docs race, laptop mic, Pi speaker}"
