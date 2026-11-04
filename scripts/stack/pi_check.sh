#!/usr/bin/env bash
# Run ON the Pi (make pi-check): is this machine ready to serve the stack? Reports every problem, exits 1 if any.
STACK_REPO=${STACK_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
PI_ENV_FILE=${PI_ENV_FILE:-$STACK_REPO/config/pi.env}
TOKEN_FILE=${TOKEN_FILE:-$HOME/.config/lan_speech_token}
CARDS_FILE=${CARDS_FILE:-/proc/asound/cards}
BAD=0
good() { echo "  ok    $1"; }
bad() { echo "  FAIL  $1"; echo "        fix: $2"; BAD=1; }

echo "pi-check in $STACK_REPO"
if [ -f "$PI_ENV_FILE" ] && (set -a; . "$PI_ENV_FILE") 2>/dev/null; then
  set -a; . "$PI_ENV_FILE"; set +a
  miss=""; for k in PI_REPO PI_AUDIO_DEVICE PORT_PI_SPEAKER PORT_PI_DEVICE; do [ -n "${!k:-}" ] || miss="$miss $k"; done
  [ -z "$miss" ] && good "config/pi.env loads" || bad "config/pi.env lacks:$miss" "run make pi-detect on the laptop, then make pi-env"
else
  bad "config/pi.env missing or unreadable" "on the laptop: make pi-detect pi-env"
fi

[ -x "$STACK_REPO/.venv/bin/python" ] && good "venv .venv/bin/python" || bad "no .venv/bin/python" "build the venv as in docs/PI-TEST-PLAN.md"

if [ -f "$TOKEN_FILE" ]; then
  mode=$(stat -c %a "$TOKEN_FILE")
  [ "$mode" = 600 ] && good "token file mode 600" || bad "token file mode is $mode" "chmod 600 $TOKEN_FILE"
else
  bad "no $TOKEN_FILE" "copy the laptop's token here (docs/PI-BRIDGE.md), then chmod 600"
fi

if [ -n "${PI_AUDIO_DEVICE:-}" ]; then
  grep -qE "^ *[0-9]+ \[[^]]*\]: *USB-Audio - ${PI_AUDIO_DEVICE} *$" "$CARDS_FILE" 2>/dev/null \
    && good "audio device $PI_AUDIO_DEVICE present" || bad "audio device $PI_AUDIO_DEVICE not in $CARDS_FILE" "plug the speaker in, or make pi-detect on the laptop"
fi

missing=""
for i in 01 02 03 04 05 06; do [ -f "$STACK_REPO/tests/fixtures/pi_echo/reply-$i.wav" ] || missing="$missing reply-$i.wav"; done
[ -z "$missing" ] && good "six clips in tests/fixtures/pi_echo" || bad "missing clips:$missing" "on the laptop: make pi-clips"

[ "$BAD" = 0 ] && echo "pi-check: all good" || echo "pi-check: problems found"
exit "$BAD"
