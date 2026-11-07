#!/usr/bin/env bash
# doc_chat client wired to the fdbench virtual cables (mic = fdb_to_bot.monitor, speaker = fdb_from_bot)
export HARNESS=pi
source /mnt/School/PhD/AI231/AI-231-ME3-agy/scripts/stack/lib.sh
load_env; need_token
cd "$STACK_REPO" || exit 1
export FDBENCH_MARKS=udp://127.0.0.1:8792
RUN=out/sessions/bench-$(date +%m%d-%H%M)
exec uv run --group laptop python -W ignore -u -m doc_chat --mic --play --llm agent --no-docs --slot "doc+term?" --stream \
  --server "ws://$LAPTOP_IP:$PORT_SPEECH" --agent-url "$AGENT_URL" --vad both --vad-confidence 0.5 \
  --silero-kofn 7/10 --level-kofn 3/8 --barge-in --duck-db -15 --strip-echo-prefix --fillers \
  --record "$RUN" --note "fdbench cables, Pi harness"
