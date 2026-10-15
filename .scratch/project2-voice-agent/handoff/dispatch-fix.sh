#!/usr/bin/env bash
# usage: dispatch-fix.sh <PKG> <base-sha> "<files>" "<findings>"  — agy worker on one review fix package
set -euo pipefail
PKG=$1; BASE=$2; FILES=$3; FIND=$4
ROOT=/mnt/jfs_hpc/home/anthony.martin.navarez/ME3
H=$ROOT/.scratch/project2-voice-agent/handoff
WT=/mnt/jfs_hpc/home/anthony.martin.navarez/ME3-wt/fix-$PKG
[ -d "$WT" ] || git -C "$ROOT" worktree add -q -b "fix/$PKG" "$WT" "$BASE"
cd "$WT" && uv sync -q
MSG=$H/to-claude/fix-$PKG-agy-result.md
PROMPT="You are agy, a worker agent. Your worktree is $WT (branch fix/$PKG, based on $BASE); work only inside it.
Task: fix review findings $FIND from .scratch/project2-voice-agent/handoff/REVIEW-main-92f3edb.md (read the whole findings text for those ids, and RUNNER.md rules). You may edit ONLY these files: $FILES. Add tests that fail on the old code and pass on the fix. Do not change any contract (public signatures, config schema, event/field names) - if a fix needs that, stop and report BLOCKED.
Run 'make check' and 'uv run pytest -q' before finishing; both must be green. Commit once on branch fix/$PKG as 'fix $PKG: <summary>'. Append a dated entry to the Execution Log of the affected ticket(s) only if that ticket file is in your allowed list; otherwise put details in the result file.
Do not merge, push, rebase, or touch other files. No paid/authenticated API calls. Never print secrets.
When finished (DONE, BLOCKED or PARTIAL) write $MSG (via .tmp then mv, last) with: status, which findings are fixed, test counts, commit SHA."
exec agy -p "$PROMPT" --dangerously-skip-permissions --print-timeout 50m > "$H/logs/agy-fix-$PKG.log" 2>&1
