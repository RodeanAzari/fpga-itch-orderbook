#!/usr/bin/env bash
# One hardware run: configure the board (host/capture), replay a feed slice once acked,
# then compare the capture log with the golden model (analysis/compare.py).
#   scripts/board_run.sh NAME MODE PPS COUNT [extra capture args...]
# Needs: the board programmed (make prog), the host on 192.168.10.1 with a static neighbour
# for the FPGA (docs/log.md 2026-11-09), ITCH=<length-prefixed ITCH file> (default: the
# AAPL extract), LO_PRICE (window low edge for that day), SESSION (MoldUDP64 session),
# CUT_THROUGH=1 for a cut-through bitstream (ADR 0013), MAX_PAYLOAD=N for smaller packets
# (replay --max-payload; default 1472).
# Writes data/runs/NAME.{log,capture.txt,capture.err,replay.txt} and
# data/runs/<ITCH stem>_slice<COUNT>.pkts (the feed as sent). Exit status: compare's.
set -euo pipefail
name=$1 mode=$2 pps=$3 count=$4; shift 4
itch=${ITCH:-/mnt/d/itch/derived/2019-10-18_AAPL.itch}
lo=${LO_PRICE:-2140700}  # ADR 0002: base 23,455 ticks
if_ip=${IF_IP:-192.168.10.1}
R=data/runs; mkdir -p $R
session=${SESSION:-20191018AA}
ct_arg=; [ "${CUT_THROUGH:-0}" = 1 ] && ct_arg=--cut-through  # the bitstream's build mode
mp=${MAX_PAYLOAD:+--max-payload $MAX_PAYLOAD}
pk=$R/$(basename "$itch" .itch)_slice$count${MAX_PAYLOAD:+_mp$MAX_PAYLOAD}.pkts  # per day and size
[ -f "$pk" ] || build/host-Release/replay --in "$itch" --out "$pk" --count "$count" --session "$session" $mp >/dev/null
rm -f $R/$name.ready
build/host-Release/capture --log $R/$name.log --lo-price "$lo" --mode "$mode" --bind-ip "$if_ip" \
  --ready-file $R/$name.ready --idle 3 --duration 600 "$@" >$R/$name.capture.txt 2>$R/$name.capture.err &
cap=$!
for _ in $(seq 100); do [ -f $R/$name.ready ] && break; kill -0 $cap 2>/dev/null || break; sleep 0.1; done
[ -f $R/$name.ready ] || { wait $cap || true; cat $R/$name.capture.err; exit 1; }
build/host-Release/replay --in "$itch" --dst 239.1.1.1 --port 26400 --if-ip "$if_ip" \
  --count "$count" --session "$session" --pps "$pps" $mp >$R/$name.replay.txt 2>/dev/null
wait $cap
.venv/bin/python -m analysis.compare --log $R/$name.log --packets "$pk" ${ct_arg}
