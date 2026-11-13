#!/usr/bin/env bash
# pi_sync.sh [sync | mode demo|benchmark|auto]   (make pi-sync, make pi-mode MODE=...)
# Make the Pi's code exactly one git ref, the same way every time:
#   1. exports PI_REF (default HEAD) with git archive, never the working tree, so untracked files and
#      uncommitted edits cannot leak; refuses when tracked files under the synced folders are modified
#   2. rsyncs src, pipeline/src, pipeline/config, config, corpus and scripts to $PI_REPO with --delete
#      (files the repo dropped go too), keeping the Pi's own config/pi.env, profile.md, publish.toml (the demo repository, set on the Pi only) and pi_known_hosts
#   3. makes the shell scripts executable (git on the laptop's disk stores them as 644, and a service whose
#      script is not executable fails with 203/EXEC)
#   4. writes $PI_REPO/.deployed (commit, ref, date, a hash of the dependency files) and warns when the
#      dependency files changed since the last sync: the Pi's venvs are not rebuilt here
#   5. sets the search mode the agent's search server reads (demo = unconstrained Tavily, benchmark = Google
#      first, auto = decided by the Tavily credit thresholds)
#   6. restarts pi-speaker and pi-agent, waits for their ports
#   7. verifies: every synced file matches the ref by checksum, both services are active, the router is
#      reachable from the Pi, and prints the search mode the Pi's search server will use
# Variables: PI_REF (default HEAD)  PI_RESTART=all|agent|none (default all)
#            PI_SEARCH_MODE=demo|benchmark|auto|keep (default demo)  FORCE=1 (sync a ref with local edits)  DRY=1
# `mode` only changes the search mode (the search server reads it on every call, no restart).
source "$(dirname "$0")/lib.sh"
load_env
SYNC_DIRS=(src pipeline/src pipeline/config config corpus scripts)
KEEP=(--exclude=pi.env --exclude=profile.md --exclude=publish.toml --exclude=pi_known_hosts --exclude=__pycache__ '--exclude=*.pyc')
PI_REF=${PI_REF:-HEAD}
PI_RESTART=${PI_RESTART:-all}
PI_SEARCH_MODE=${PI_SEARCH_MODE:-demo}
DEPENDENCY_FILES=(pyproject.toml uv.lock pipeline/pyproject.toml pipeline/uv.lock)
mapfile -t SSH_ARGS < <(ssh_opts)
RSH="ssh ${SSH_ARGS[*]}"
REMOTE="$PI_USER@$PI_V4"
cd "$STACK_REPO" || exit 1

set_mode() {
  case "$1" in
    demo | benchmark) pi_ssh "mkdir -p ~/.local/state/voice_agent && echo $1 > ~/.local/state/voice_agent/tavily_mode" ;;
    auto) pi_ssh "rm -f ~/.local/state/voice_agent/tavily_mode" ;;
    keep) ;;
    *) die "search mode must be demo, benchmark, auto or keep, not $1" ;;
  esac
}

show_mode() {
  [ "$DRY" = 1 ] && { say_dry "read the search mode the Pi will use"; return 0; }
  pi_ssh "cd $PI_REPO && PYTHONPATH=src .venv-mcp/bin/python -c 'from voice_agent.tavily_budget import credit_status; print(credit_status()[2])'" 2>/dev/null || echo unknown
}

dependency_hash() {
  local f
  for f in "${DEPENDENCY_FILES[@]}"; do git show "$PI_REF:$f" 2>/dev/null; done | sha256sum | cut -c1-12
}

sync_code() {
  git rev-parse --verify -q "$PI_REF^{commit}" > /dev/null || die "unknown git ref: $PI_REF"
  if [ "$PI_REF" = HEAD ] && [ "${FORCE:-0}" != 1 ] && [ "$DRY" != 1 ]; then
    git diff --quiet HEAD -- "${SYNC_DIRS[@]}" ||
      die "tracked files under ${SYNC_DIRS[*]} have uncommitted changes: commit them, or FORCE=1 to sync the commit without them"
  fi
  SHA=$(git rev-parse --short=12 "$PI_REF^{commit}")
  TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
  git archive "$PI_REF" "${SYNC_DIRS[@]}" | tar -x -C "$TMP"
  echo "syncing $PI_REF ($SHA) to $REMOTE:$PI_REPO"
  local previous
  previous=$(pi_ssh "cat $PI_REPO/.deployed 2>/dev/null" 2>/dev/null | sed -n 's/^deps: //p')
  local d
  for d in "${SYNC_DIRS[@]}"; do
    run rsync -rlt --delete "${KEEP[@]}" -e "$RSH" "$TMP/$d/" "$REMOTE:$PI_REPO/$d/"
  done
  pi_ssh "cd $PI_REPO && find scripts -name '*.sh' -exec chmod +x {} +"
  DEPS=$(dependency_hash)
  pi_ssh "printf 'commit: $SHA\nref: $PI_REF\ndate: %s\ndeps: $DEPS\n' \"\$(date -Is)\" > $PI_REPO/.deployed"
  if [ -n "$previous" ] && [ "$previous" != "$DEPS" ]; then
    echo "WARNING: the dependency files (pyproject.toml, uv.lock) changed since the last sync: rebuild the Pi's venvs"
  fi
}

restart_services() {
  local units
  case "$PI_RESTART" in
    none) return 0 ;;
    agent) units="pi-agent" ;;
    all) units="pi-speaker pi-agent" ;;
    *) die "PI_RESTART must be all, agent or none, not $PI_RESTART" ;;
  esac
  pi_ssh "systemctl --user reset-failed $units 2>/dev/null; systemctl --user restart $units"
  if [ "$DRY" = 1 ]; then say_dry "wait for ports $PORT_AGENT and $PORT_PI_SPEAKER on the Pi"; return 0; fi
  pi_ssh "for i in \$(seq 1 45); do ss -ltn | grep -q ':$PORT_AGENT ' && ss -ltn | grep -q ':$PORT_PI_SPEAKER ' && exit 0; sleep 2; done; exit 1" ||
    die "the Pi services did not come up in 90 s: journalctl --user -u pi-agent on the Pi"
}

verify() {
  if [ "$DRY" = 1 ]; then say_dry "verify checksums, services, router reachability"; return 0; fi
  local d diff bad=0
  for d in "${SYNC_DIRS[@]}"; do
    diff=$(rsync -rltnc --delete -i "${KEEP[@]}" -e "$RSH" "$TMP/$d/" "$REMOTE:$PI_REPO/$d/" | grep -v '^\.d' || true)
    if [ -n "$diff" ]; then echo "MISMATCH in $d:"; echo "$diff" | head -5; bad=1; fi
  done
  [ "$bad" = 0 ] && echo "verify: every synced file matches $SHA"
  local active
  active=$(pi_ssh "systemctl --user is-active pi-speaker pi-agent" | tr '\n' ' ')
  echo "verify: services $active"
  case "$active" in "active active ") ;; *) bad=1 ;; esac
  local router
  router=$(pi_ssh "curl -s -m3 -o /dev/null -w '%{http_code}' http://$LAPTOP_IP:$PORT_ROUTER/health" 2>/dev/null || true)
  if [ "$router" = 200 ]; then echo "verify: the router answers the Pi"; else echo "WARNING: the router does not answer the Pi (http ${router:-none}): make up on the laptop"; fi
  echo "verify: the search mode on the Pi is $(show_mode)"
  [ "$bad" = 0 ] || die "verification failed"
}

case "${1:-sync}" in
  sync)
    sync_code
    set_mode "$PI_SEARCH_MODE"
    restart_services
    verify
    echo "Pi is at $SHA ($PI_REF)"
    ;;
  mode)
    [ -n "${2:-}" ] || die "usage: pi_sync.sh mode demo|benchmark|auto"
    set_mode "$2"
    echo "search mode on the Pi: $(show_mode)"
    ;;
  *) die "usage: pi_sync.sh [sync | mode demo|benchmark|auto]" ;;
esac
