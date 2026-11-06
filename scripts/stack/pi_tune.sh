#!/usr/bin/env bash
# Latency tuning for the Pi harness (2026-11-06; with all three, first 20 v4 lines 1.65 -> 1.50 s median):
#   Pi:     CPU governor "performance" (kept across reboots by a small systemd unit) and Wi-Fi power
#           save off (NetworkManager, kept across reboots); both need sudo on the Pi (asks for the password)
#   laptop: power profile "performance" (powerprofilesctl; plug in AC as well)
# Then prints what is in effect. DRY=1 only prints the commands.
source "$(dirname "$0")/lib.sh"
load_env
remote=$(cat <<'EOF'
set -e
unit=/etc/systemd/system/me3-cpu-performance.service
sudo tee "$unit" >/dev/null <<'UNIT'
[Unit]
Description=CPU governor performance (ME3 voice agent latency)
[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for g in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do echo performance > "$g"; done'
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now me3-cpu-performance.service
wifi=$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2=="802-11-wireless"{print $1; exit}')
if [ -n "$wifi" ]; then
  sudo nmcli connection modify "$wifi" 802-11-wireless.powersave 2
  sudo /usr/sbin/iw dev wlan0 set power_save off
fi
echo "pi governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
echo "pi wifi ($wifi): $(/usr/sbin/iw dev wlan0 get power_save)"
echo "pi throttled: $(vcgencmd get_throttled)"
EOF
)
if [ "$DRY" = 1 ]; then
  echo "+ ssh -t $PI_USER@$PI_V4 <governor unit, wifi power save off>"
  echo "+ powerprofilesctl set performance"
  exit 0
fi
ssh -t -i "$PI_KEY" -o UserKnownHostsFile="$PI_HOSTS_FILE" -o HostKeyAlias=pi-cable "$PI_USER@$PI_V4" "$remote" ||
  die "the Pi tuning failed (see the output above)"
if command -v powerprofilesctl >/dev/null; then
  powerprofilesctl set performance && echo "laptop power profile: $(powerprofilesctl get)"
fi
ac=$(cat /sys/class/power_supply/A*/online 2>/dev/null | head -1)
[ "$ac" = 1 ] || echo "laptop: on battery; plug in AC for the full speed (speech and voice run here)"
