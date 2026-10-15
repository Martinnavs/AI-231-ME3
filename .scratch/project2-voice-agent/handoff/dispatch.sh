#!/usr/bin/env bash
# usage: dispatch.sh <NN> <ticket-file-slug> <base-sha>   — spawn an agy worker on one ticket, in its own worktree
set -euo pipefail
NN=$1; SLUG=$2; BASE=$3
ROOT=/mnt/jfs_hpc/home/anthony.martin.navarez/ME3
H=$ROOT/.scratch/project2-voice-agent/handoff
WT=/mnt/jfs_hpc/home/anthony.martin.navarez/ME3-wt/agy-$NN
[ -d "$WT" ] || git -C "$ROOT" worktree add -q -b "agy/$NN" "$WT" "$BASE"
cd "$WT" && uv sync -q
MSG=$H/to-claude/$NN-agy-result.md
PROMPT="You are agy, a worker agent. Your worktree is $WT (branch agy/$NN, based on $BASE); work only inside it.
1. Read .scratch/project2-voice-agent/RUNNER.md in full, then execute ticket .scratch/project2-voice-agent/tickets/$SLUG.md exactly as RUNNER.md says (assumptions, contracts, Proof tests, gate suite, Execution Log entry in the ticket, one commit '$NN: <ticket title>' on branch agy/$NN).
2. Do not touch files outside the ticket's Files list. Do not merge, push, rebase onto anything, or edit another ticket. No paid/authenticated API calls. No new packages beyond the ticket's Dependencies/license.
3. When finished (DONE, BLOCKED or PARTIAL), write $MSG with: status line, Proof checkmarks, commit SHA, 'details in ticket $NN'. Write it last, via a .tmp file then mv.
If you hit a material deviation, stop and write the BLOCKED message instead of guessing."
echo "$PROMPT" > "$H/to-agy/$NN-claude-task.md"
exec agy -p "$PROMPT" --dangerously-skip-permissions --print-timeout 3000 > "$H/logs/agy-$NN.log" 2>&1
