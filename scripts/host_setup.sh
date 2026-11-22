#!/usr/bin/env bash
# Host side of a board run, after the ASIX adapter is attached to WSL (usbipd attach):
#   sudo scripts/host_setup.sh [IFACE]      (default: the first enx* interface)
# Host 192.168.10.1/24, static neighbour for the FPGA (it answers no ARP), a deep pfifo
# so replay bursts are not dropped in the qdisc, and no NCM TX aggregation delay
# (docs/log.md 2026-11-09). Needs root; run it in your own terminal (sudo needs a tty).
set -euo pipefail
if=${1:-$(ls /sys/class/net | grep '^enx' | head -1)}
[ -n "$if" ] || { echo "no enx* interface: attach the adapter first" >&2; exit 1; }
ip addr replace 192.168.10.1/24 dev "$if"
ip link set "$if" up
ip neigh replace 192.168.10.2 lladdr 02:00:0a:0b:0c:0d dev "$if" nud permanent
tc qdisc replace dev "$if" root pfifo limit 10000
echo 0 > "/sys/class/net/$if/cdc_ncm/tx_timer_usecs"
ip -br addr show "$if"
echo "tx_timer_usecs=$(cat /sys/class/net/$if/cdc_ncm/tx_timer_usecs)"
