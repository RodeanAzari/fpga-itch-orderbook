#!/usr/bin/env bash
# ILA capture of one feed frame through MII -> eth_rx FIFO -> udp_rx -> decoder, checked
# against the frame on the wire and the golden model (analysis/ila_frame.py).
#   scripts/ila_run.sh NAME [PPS] [COUNT]
# Needs: the board claimed (coord.py board claim P2) and attached, the host adapter set up
# as for scripts/board_run.sh, and `make impl ILA=1` (build/arty_top_ila/).
# Programs the ILA bitstream, starts a slow replay (default 2 packets/s, 2,000 messages),
# arms both ILA cores once the board has acked its config, then checks the capture.
# Writes data/runs/NAME/{mii,core}.{csv,ila} and the usual board_run files for NAME.
set -euo pipefail
name=$1 pps=${2:-2} count=${3:-2000}
B=build/arty_top_ila R=data/runs
out=$R/$name; mkdir -p "$out"; go=$out/go; rm -f "$go"
eval "$(sed -n '/^vivado-env()/,/^}/p' ~/.bashrc)"
set +eu +o pipefail; vivado-env; set -euo pipefail  # its ls of a missing glob returns 2
vivado -mode batch -nojournal -nolog -source syn/ila_capture.tcl \
  -tclargs $B/arty_top.bit $B/arty_top.ltx "$out" "$go" >"$out/vivado.log" 2>&1 &
viv=$!
until grep -q PROGRAMMED "$out/vivado.log"; do
  kill -0 $viv 2>/dev/null || { tail -20 "$out/vivado.log"; exit 1; }; sleep 0.5
done
scripts/board_run.sh "$name" bbo "$pps" "$count" >"$out/board_run.txt" 2>&1 &
run=$!
until [ -f $R/$name.ready ]; do kill -0 $run 2>/dev/null || break; sleep 0.2; done
sleep 1; touch "$go"
wait $viv || { tail -20 "$out/vivado.log"; exit 1; }
wait $run || echo "board_run: compare did not pass (see $out/board_run.txt)"
.venv/bin/python -m analysis.ila_frame --mii "$out/mii.csv" --core "$out/core.csv" \
  --packets "$R/$(basename "${ITCH:-/mnt/d/itch/derived/2019-10-18_AAPL.itch}" .itch)_slice$count.pkts"
