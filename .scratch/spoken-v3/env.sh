# shared settings for the spoken-v3 scripts (source me)
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
D=$REPO/.scratch/spoken-v3; mkdir -p "$D/logs" "$D/pids"
LAPTOP_IP=$(ip -4 -o addr show | awk '/ 169\.254\./ {split($4,a,"/"); print a[1]; exit}')   # the cable address
CABLE_IF=$(ip -4 -o addr show | awk '/ 169\.254\./ {print $2; exit}')
PI_V4=169.254.230.219
PI_V6=fe80::18ac:b795:c102:5eb1
ROUTER_MODEL=${ROUTER_MODEL:-router-350m-kd-web2-s0-unverified}      # the candidate; router-350m-kd-s0 = the one live before
G=$HOME/work/gguf-tools
pi_ssh() { ssh -i ~/.ssh/id_ed25519_pi -o HostKeyAlias="$PI_V6%enp0s20f0u2c2" -o ConnectTimeout=8 "rpi-anavarez@$PI_V6%$CABLE_IF" "$@"; }
