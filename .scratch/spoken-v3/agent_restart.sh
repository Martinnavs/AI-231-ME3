#!/usr/bin/env bash
# restart ONLY the agent server (needed once the Pi device tools are up: it leaves them out if they are down at start)
source "$(dirname "$0")/env.sh"; cd "$REPO" || exit 1
export LAN_SPEECH_TOKEN=$(cat ~/.config/lan_speech_token)
pid=$(ss -ltnp | awk '$4 ~ /:8781$/ {match($0,/pid=[0-9]+/); print substr($0,RSTART+4,RLENGTH-4)}' | head -1); [ -n "$pid" ] && kill "$pid" && echo "stopped agent ($pid)"
[ -f "$D/pids/agent" ] && kill "$(cat "$D/pids/agent")" 2>/dev/null; sleep 2
cd pipeline && nohup uv run python -m voice_pipeline.agent_server --ipv4 --hedge -1 --notices '' --router-url http://127.0.0.1:8089 > "$D/logs/agent.log" 2>&1 &
echo $! > "$D/pids/agent"
for i in $(seq 120); do grep -q "agent server on" "$D/logs/agent.log" && break; sleep 1; done
grep "agent server on" "$D/logs/agent.log" && echo "tools: $(grep -o "advertised FunctionSchema '[a-z_]*'" "$D/logs/agent.log" | sed "s/.*'\(.*\)'/\1/" | tr '\n' ' ')"
grep "left out" "$D/logs/agent.log" || echo "no MCP server left out"
