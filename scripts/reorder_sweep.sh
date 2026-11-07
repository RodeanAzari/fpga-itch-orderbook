#!/bin/bash
# Host-reordering rate sweep against fpga-eth-core rx_stats (board counters over UART).
set -euo pipefail
OUT=${OUT:?}; RATES=${RATES:-"8000 6800 5600 4000"}; SECS=${SECS:-60}
SEND=~/fpga-eth-core/build/host/udp_seq_send
stty -F /dev/ttyUSB1 115200 raw -echo -hupcl
cat /dev/ttyUSB1 >> "$OUT/uart.log" &
READER=$!
trap 'kill $READER' EXIT
sleep 3
seq0=${START_SEQ:-0}
for pps in $RATES; do
  n=$((pps * SECS))
  before=$(grep '^RX' "$OUT/uart.log" | tail -1)
  taskset -c 2 $SEND --dst 239.1.1.1 --port 26400 --if-ip 169.254.235.36 --size 1472 \
      --pps $pps --count $n --start-seq $seq0 > "$OUT/send_$pps.txt"
  sleep 3
  after=$(grep '^RX' "$OUT/uart.log" | tail -1)
  echo "pps=$pps sent=$n start_seq=$seq0" >> "$OUT/runs.txt"
  echo "before $before" >> "$OUT/runs.txt"; echo "after  $after" >> "$OUT/runs.txt"
  seq0=$((seq0 + n))
done
