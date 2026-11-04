#!/usr/bin/env bash
# Shared helpers for scripts/stack/*.sh (source me). Reads config/pi.env, written by pi_detect.sh.
STACK_REPO=${STACK_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
PI_ENV_FILE=${PI_ENV_FILE:-$STACK_REPO/config/pi.env}
PI_HOSTS_FILE=${PI_HOSTS_FILE:-$STACK_REPO/config/pi_known_hosts}
STACK_DIR=${STACK_DIR:-$STACK_REPO/out/stack}
TOKEN_FILE=${TOKEN_FILE:-$HOME/.config/lan_speech_token}
DRY=${DRY:-0}

die() { echo "stack: $*" >&2; exit 1; }

# run CMD...: print it when DRY=1, run it otherwise
run() {
  if [ "$DRY" = 1 ]; then printf '+'; printf ' %q' "$@"; printf '\n'; return 0; fi
  "$@"
}
say_dry() { [ "$DRY" = 1 ] && echo "+ ($*)"; return 0; }

env_age_min() {
  local m; m=$(stat -c %Y "$PI_ENV_FILE" 2>/dev/null) || { echo 999999; return; }
  echo $(( ($(date +%s) - m) / 60 ))
}

# load_env: config/pi.env, or detect it first when missing or older than PI_ENV_MAX_AGE_MIN.
load_env() {
  local max=${PI_ENV_MAX_AGE_MIN:-120}
  if [ ! -f "$PI_ENV_FILE" ] || [ "$(env_age_min)" -ge "$max" ]; then
    if [ "$DRY" = 1 ]; then
      echo "+ $STACK_REPO/scripts/stack/pi_detect.sh   (config/pi.env missing or older than ${max} min)"
      [ -f "$PI_ENV_FILE" ] || PI_ENV_FILE=$STACK_REPO/config/pi.env.example
    else
      echo "config/pi.env missing or older than ${max} min: running pi-detect"
      "$STACK_REPO/scripts/stack/pi_detect.sh" || exit 1
    fi
  fi
  set -a
  # shellcheck disable=SC1090
  . "$PI_ENV_FILE"
  set +a
  for k in CABLE_IF LAPTOP_IP PI_V4 PI_USER PI_KEY PI_REPO PI_AUDIO_DEVICE; do
    [ -n "${!k:-}" ] || die "$k is empty in $PI_ENV_FILE: run make pi-detect"
  done
  PORT_ROUTER=${PORT_ROUTER:-8089}; PORT_SPEECH=${PORT_SPEECH:-8770}; PORT_AGENT=${PORT_AGENT:-8781}
  PORT_PI_SPEAKER=${PORT_PI_SPEAKER:-8780}; PORT_PI_DEVICE=${PORT_PI_DEVICE:-8790}
  mkdir -p "$STACK_DIR/logs" "$STACK_DIR/pids" 2>/dev/null || true
}

ssh_opts() {
  printf '%s\n' -i "$PI_KEY" -o BatchMode=yes -o ConnectTimeout="${SSH_TIMEOUT:-6}" \
    -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile="$PI_HOSTS_FILE" -o HostKeyAlias=pi-cable
}
# pi_ssh REMOTE_COMMAND: reaches the Pi on its v4 cable address
pi_ssh() {
  local -a o; mapfile -t o < <(ssh_opts)
  run ssh "${o[@]}" "$PI_USER@$PI_V4" "$@"
}

port_listening() { ss -ltn 2>/dev/null | grep -q ":$1 "; }
pi_port_open() { (exec 3<>"/dev/tcp/$PI_V4/$1") 2>/dev/null; }

need_token() {
  [ "$DRY" = 1 ] && return 0
  [ -r "$TOKEN_FILE" ] || die "no $TOKEN_FILE: make a fresh pair (docs/PI-BRIDGE.md)"
  LAN_SPEECH_TOKEN=$(cat "$TOKEN_FILE"); export LAN_SPEECH_TOKEN
}
