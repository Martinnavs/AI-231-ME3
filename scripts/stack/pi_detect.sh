#!/usr/bin/env bash
# Find the Pi on the cable and write config/pi.env. Read-only on the Pi (ssh read commands only).
# On any failure: names the step, gives a fix hint, exits 1, and leaves config/pi.env as it was.
STACK_REPO=${STACK_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
PI_ENV_FILE=${PI_ENV_FILE:-$STACK_REPO/config/pi.env}
PI_HOSTS_FILE=${PI_HOSTS_FILE:-$STACK_REPO/config/pi_known_hosts}
TOKEN_FILE=${TOKEN_FILE:-$HOME/.config/lan_speech_token}

# last known values: the old file, then the caller's environment wins
if [ -f "$PI_ENV_FILE" ]; then
  _keep=$(env | grep -E '^(PI_USER|PI_KEY|PI_REPO|PI_AUDIO_DEVICE|PI_AUDIO_PATTERN)=' || true)
  # shellcheck disable=SC1090
  . "$PI_ENV_FILE" 2>/dev/null || true
  [ -n "$_keep" ] && while IFS== read -r k v; do export "$k=$v"; done <<<"$_keep"
fi
PI_USER=${PI_USER:-rpi-anavarez}
PI_KEY=${PI_KEY:-$HOME/.ssh/id_ed25519_pi}
PI_REPO=${PI_REPO:-'~/Documents/AI-231-ME3'}
PI_AUDIO_PATTERN=${PI_AUDIO_PATTERN:-CD002}
PI_AUDIO_DEVICE=${PI_AUDIO_DEVICE:-}
PI_SPEAKER_RATE=${PI_SPEAKER_RATE:-48000}
KNOWN_V4=${PI_V4:-}; KNOWN_V6=${PI_V6:-}

STEP=0; STEPNAME=""
step() { STEP=$((STEP + 1)); STEPNAME=$1; printf '[%d/7] %s ... ' "$STEP" "$1"; }
ok() { echo "ok${1:+ ($1)}"; }
fail() { echo "FAILED"; echo "pi-detect failed at step $STEP ($STEPNAME): $1" >&2; echo "  fix: $2" >&2; exit 1; }

ssh_to() { # ssh_to HOST REMOTE_COMMAND
  local host=$1; shift
  ssh -i "$PI_KEY" -o BatchMode=yes -o ConnectTimeout="${SSH_TIMEOUT:-5}" -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$PI_HOSTS_FILE" -o HostKeyAlias=pi-cable "$PI_USER@$host" "$@" 2>/dev/null
}

# pick_audio CARDS_TEXT PATTERN PREFERRED: print the sound-card name to use (the name sounddevice matches, e.g. CD002-AUDIO,
# not the card number), or return 1 and list the USB cards on stdout
pick_audio() {
  local cards=$1 pattern=$2 preferred=$3 names n
  names=$(printf '%s\n' "$cards" | sed -nE 's/^ *[0-9]+ \[[^]]*\]: *USB-Audio - *(.*[^ ]) *$/\1/p')
  for n in $preferred; do printf '%s\n' "$names" | grep -qxF "$n" && { echo "$n"; return 0; }; done
  n=$(printf '%s\n' "$names" | grep -F -- "$pattern" | head -1)
  if [ -n "$pattern" ] && [ -n "$n" ]; then echo "$n"; return 0; fi
  if [ "$(printf '%s\n' "$names" | grep -c .)" = 1 ]; then echo "$names"; return 0; fi
  printf '%s\n' "$names" | tr '\n' ',' | sed 's/,$//'; return 1
}

write_env() { # write_env TMPFILE FINAL: all-or-nothing
  {
    echo "# written by scripts/stack/pi_detect.sh; do not edit, run make pi-detect"
    echo "CABLE_IF=$CABLE_IF"; echo "LAPTOP_IP=$LAPTOP_IP"
    echo "PI_V4=$PI_V4"; echo "PI_V6=$PI_V6"
    echo "PI_USER=$PI_USER"; echo "PI_KEY=$PI_KEY"; echo "PI_REPO=$PI_REPO_ABS"
    echo "PI_AUDIO_DEVICE=$PI_AUDIO_DEVICE"; echo "PI_SPEAKER_RATE=$PI_SPEAKER_RATE"
    echo "PI_LD_LIBRARY_PATH=$PI_LD"
    echo "PORT_ROUTER=${PORT_ROUTER:-8089}"; echo "PORT_SPEECH=${PORT_SPEECH:-8770}"; echo "PORT_AGENT=${PORT_AGENT:-8781}"
    echo "PORT_PI_SPEAKER=${PORT_PI_SPEAKER:-8780}"; echo "PORT_PI_DEVICE=${PORT_PI_DEVICE:-8790}"
    echo "DETECTED_AT=$(date +%Y-%m-%dT%H:%M:%S%z)"
  } >"$1" && mv -f "$1" "$2"
}

[ "${PI_DETECT_SOURCED:-0}" = 1 ] && return 0 2>/dev/null

# 1. cable interface and laptop address
step "cable interface"
line=$(ip -4 -o addr show 2>/dev/null | awk '$4 ~ /^169\.254\./ {split($4,a,"/"); print $2, a[1]; exit}')
[ -n "$line" ] || fail "no 169.254.x.x address on this laptop" "plug the Pi cable in and wait for the link (ip -4 addr), or set the interface to link-local"
CABLE_IF=${line% *}; LAPTOP_IP=${line#* }
ok "$CABLE_IF $LAPTOP_IP"

# 2. find the Pi
step "find the Pi"
[ -r "$PI_KEY" ] || fail "ssh key $PI_KEY not readable" "set PI_KEY=/path/to/key (make pi-detect PI_KEY=...)"
neigh() { ip neigh show dev "$CABLE_IF" 2>/dev/null | awk '$1 != "" && $NF != "FAILED" {print $1}'; }
candidates() {
  neigh | grep -E '^169\.254\.' | grep -vxF "$LAPTOP_IP"
  neigh | grep -E '^fe80:' | sed "s/\$/%$CABLE_IF/"
  [ -n "$KNOWN_V4" ] && echo "$KNOWN_V4"
  [ -n "$KNOWN_V6" ] && echo "${KNOWN_V6%%%*}%$CABLE_IF"
}
REACH=""
try_candidates() {
  local c
  for c in $(candidates | awk '!seen[$0]++'); do
    if [ "$(ssh_to "$c" 'echo pi-ok')" = pi-ok ]; then REACH=$c; return 0; fi
  done
  return 1
}
try_candidates || {
  ping -6 -c 2 -W 2 "ff02::1%$CABLE_IF" >/dev/null 2>&1 || true   # make the Pi show up in ip neigh
  try_candidates
} || fail "no address on $CABLE_IF answered ssh as $PI_USER with $PI_KEY" \
  "check the cable and that the Pi is powered; test by hand: ssh -i $PI_KEY $PI_USER@<pi address>; if the Pi was reinstalled delete config/pi_known_hosts"
ok "$REACH"

# 3. the Pi's own cable addresses (read from the Pi, so nothing is hard-coded)
step "Pi addresses"
addrs=$(ssh_to "$REACH" 'ip -o addr show 2>/dev/null') || addrs=""
PI_V4=$(printf '%s\n' "$addrs" | awk '$3=="inet" && $4 ~ /^169\.254\./ {split($4,a,"/"); print a[1]; exit}')
[ -n "$PI_V4" ] || fail "the Pi has no 169.254.x.x address" "give the Pi's cable interface a link-local address (nmcli connection up on the Pi)"
PI_IF=$(printf '%s\n' "$addrs" | awk -v ip="$PI_V4" '$3=="inet" && index($4, ip"/") {print $2; exit}')
PI_V6=$(printf '%s\n' "$addrs" | awk -v ifn="$PI_IF" '$2==ifn && $3=="inet6" && $4 ~ /^fe80:/ {split($4,a,"/"); print a[1]; exit}')
ok "v4 $PI_V4, v6 ${PI_V6:-none}"

# 4. session token: same on both sides (12-char hash prefixes only)
step "session token"
[ -r "$TOKEN_FILE" ] || fail "$TOKEN_FILE is missing on the laptop" "make a fresh pair as in docs/PI-BRIDGE.md (umask 077; token in ~/.config/lan_speech_token on both)"
LOCAL_SUM=$(sha256sum "$TOKEN_FILE" | cut -c1-12)
PI_SUM=$(ssh_to "$REACH" 'sha256sum "$HOME/.config/lan_speech_token" 2>/dev/null | cut -c1-12')
[ -n "$PI_SUM" ] || fail "no ~/.config/lan_speech_token on the Pi" "copy it: scp -i $PI_KEY $TOKEN_FILE $PI_USER@$PI_V4:.config/lan_speech_token (chmod 600 there)"
[ "$LOCAL_SUM" = "$PI_SUM" ] || fail "token differs (laptop $LOCAL_SUM, Pi $PI_SUM)" "copy the laptop token to the Pi: scp -i $PI_KEY $TOKEN_FILE $PI_USER@$PI_V4:.config/lan_speech_token"
ok "$LOCAL_SUM on both"

# 5. loudspeaker (stable ALSA card name, not a card number)
step "Pi loudspeaker"
CARDS=$(ssh_to "$REACH" 'cat /proc/asound/cards') || CARDS=""
[ -n "$CARDS" ] || fail "could not read /proc/asound/cards on the Pi" "check the Pi's sound stack: ssh in and run cat /proc/asound/cards"
if PI_AUDIO_DEVICE=$(pick_audio "$CARDS" "$PI_AUDIO_PATTERN" "$PI_AUDIO_DEVICE"); then
  ok "$PI_AUDIO_DEVICE"
else
  fail "no USB sound card matching '$PI_AUDIO_PATTERN' (USB cards: ${PI_AUDIO_DEVICE:-none})" \
    "plug the speaker in, or choose one of the names above: make pi-detect PI_AUDIO_DEVICE=<name>"
fi

# 6. repository and venv on the Pi
step "Pi repository"
PI_REPO_ABS=$(ssh_to "$REACH" "r=$(printf '%q' "$PI_REPO"); case \"\$r\" in '~/'*) r=\"\$HOME/\${r#'~/'}\";; esac; cd \"\$r\" 2>/dev/null && pwd") || PI_REPO_ABS=""
[ -n "$PI_REPO_ABS" ] || fail "no repository at $PI_REPO on the Pi" "clone it there or set PI_REPO (make pi-detect PI_REPO=/path)"
ssh_to "$REACH" "test -x '$PI_REPO_ABS/.venv/bin/python'" || fail "no $PI_REPO_ABS/.venv/bin/python on the Pi" "build the venv as in docs/PI-TEST-PLAN.md"
PI_LD=$(ssh_to "$REACH" 'd=$HOME/.local/portaudio/usr/lib/aarch64-linux-gnu; [ -d "$d" ] && echo "$d"') || PI_LD=""
ok "$PI_REPO_ABS"

# 7. write the file, all or nothing
step "write config/pi.env"
mkdir -p "$(dirname "$PI_ENV_FILE")" || fail "cannot create $(dirname "$PI_ENV_FILE")" "check permissions"
TMP=$(mktemp "$PI_ENV_FILE.XXXXXX") || fail "cannot write next to $PI_ENV_FILE" "check permissions"
write_env "$TMP" "$PI_ENV_FILE" || { rm -f "$TMP"; fail "write failed" "check disk space and permissions"; }
ok "$PI_ENV_FILE"
echo "pi-detect: done (Pi $PI_V4, speaker $PI_AUDIO_DEVICE, token $LOCAL_SUM)"
