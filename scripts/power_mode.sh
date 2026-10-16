#!/usr/bin/env bash
# Switch the laptop's power profile via power-profiles-daemon (no sudo needed).
# Usage: scripts/power_mode.sh max|balanced|save|status
set -euo pipefail

cpu=/sys/devices/system/cpu/cpu0/cpufreq

status() {
    local ac="n/a"
    for f in /sys/class/power_supply/AC*/online; do [[ -r $f ]] && ac=$(<"$f"); done
    echo "profile:  $(powerprofilesctl get)"
    echo "on AC:    $([[ $ac == 1 ]] && echo yes || echo "no (battery)")"
    echo "governor: $(<"$cpu/scaling_governor")"
    echo "EPP:      $(<"$cpu/energy_performance_preference")"
    if [[ -r /sys/devices/system/cpu/intel_pstate/no_turbo ]]; then
        echo "turbo:    $([[ $(</sys/devices/system/cpu/intel_pstate/no_turbo) == 0 ]] && echo on || echo off)"
    fi
    awk '{s+=$1; n++} END {printf "avg MHz:  %.0f\n", s/n/1000}' \
        /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq
}

case "${1:-status}" in
    max)      powerprofilesctl set performance ;;
    balanced) powerprofilesctl set balanced ;;
    save)     powerprofilesctl set power-saver ;;
    status)   ;;
    *) echo "usage: $0 max|balanced|save|status" >&2; exit 2 ;;
esac
status
