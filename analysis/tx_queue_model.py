"""Approximate model of the output path before ADR 0012: which BBOs should have waited for
a full out_builder byte FIFO, compared with the BBOs that did (T2-T1 > 1,000 cycles).

Model (datagram level, 125 MHz cycles), driven by each record's board T2:
- record j reaches the writer at a_j = max(T2_j, w_{j-1} + 1); its write ends at
  w_j = max(a_j + 64, rd(j - 32) + 1): the 2,048-byte FIFO holds 32 BBOs, so its last
  byte needs the record 32 earlier read out;
- a datagram starts when udp_tx is free and a record is complete, takes the complete
  records (<= 22), and reads them at 1 byte/cycle after a 12-byte header;
- udp_tx moves 42 + payload bytes into eth_tx's 2,048-byte FIFO and is free once its last
  byte is in; the wire drains at 10 cycles/byte plus 24 bytes per frame.
Approximate: it ignores stats datagrams and the byte-level interleaving, so it is
evidence at burst level, not record level.

Usage: python -m analysis.tx_queue_model --log data/runs/RUN.log --out X.csv
CSV: index, observed, predicted, bbos_prev_20k, bbos_prev_40k (BBOs by T2 in the
20k / 40k cycles before it), for every record observed or predicted to stall.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import struct
from pathlib import Path

from analysis.compare import read_log
from model import protocol as P

FIFO_RECS, MAX_N, HDR, IP_UDP_ETH, WIRE_OVH, EFIFO = 32, 22, 12, 42, 24, 2048
OBSERVED_MIN = 1000  # T2-T1 cycles: far above any packet-internal wait (<= 125)
PREDICTED_MIN = 500  # modelled writer wait, cycles


def predict(t2: list[int]) -> set[int]:
    """Indices of records the model says waited > PREDICTED_MIN for FIFO room."""
    n_rec = len(t2)
    w: list[int] = [0] * n_rec
    rd: list[int | None] = [None] * n_rec
    st = {"free": 0, "wire_end": 0, "prev_ws": None, "prev_f": 0, "taken": 0}
    j = 0

    def form() -> None:
        while st["taken"] < j:
            taken = st["taken"]
            s = max(st["free"], w[taken] + 1)
            n = 0
            while n < MAX_N and taken + n < j and w[taken + n] + 1 <= s:
                n += 1
            if taken + n == j and n < MAX_N and j < n_rec:
                # record j is not known yet; it is complete by s only if it does not
                # wait on this datagram's reads (it can't: those come after s)
                if j - FIFO_RECS < taken and max(t2[j], w[j - 1] + 1) + 64 + 1 <= s:
                    return  # compute j first
            for p in range(n):
                rd[taken + p] = s + HDR + 64 * (p + 1)
            frame = IP_UDP_ETH + HDR + 64 * n
            c = s + HDR + 64 * n + 3
            ws = max(st["wire_end"], c + 4)
            e = c + frame
            if st["prev_ws"] is not None:
                e = max(e, st["prev_ws"] + 10 * (8 + st["prev_f"] - (EFIFO - frame)))
            st.update(wire_end=ws + (frame + WIRE_OVH) * 10, prev_ws=ws, prev_f=frame,
                      free=e + 1, taken=taken + n)  # fmt: skip

    waits = set()
    while j < n_rec:
        if j >= FIFO_RECS and rd[j - FIFO_RECS] is None:
            form()
            if rd[j - FIFO_RECS] is None:
                raise RuntimeError(f"model stuck at record {j}")
        a = max(t2[j], w[j - 1] + 1) if j else t2[j]
        room = rd[j - FIFO_RECS] if j >= FIFO_RECS else None
        w[j] = max(a + 64, room + 1 if room is not None else 0)
        if w[j] - (a + 64) > PREDICTED_MIN:
            waits.add(j + 1)  # the next BBO's T2-T1 carries the wait
        j += 1
        form()
    return {k for k in waits if k < n_rec}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    _, dgs = read_log(a.log)
    by_seq = {d.pkt_seq: d for d in dgs if d.kind == P.BBO}  # as latency.py
    times = [struct.unpack(">QQQ", r[P.TIMES]) for k in sorted(by_seq) for r in by_seq[k].records]
    t2 = [t[2] for t in times]
    obs = {i for i, t in enumerate(times) if t[2] - t[1] > OBSERVED_MIN}
    pred = predict(t2)

    def before(i: int, span: int) -> int:
        return i - bisect.bisect_left(t2, t2[i - 1] - span)

    with a.out.open("w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(("index", "observed", "predicted", "bbos_prev_20k", "bbos_prev_40k"))
        for i in sorted(obs | pred):
            wr.writerow((i, int(i in obs), int(i in pred), before(i, 20_000), before(i, 40_000)))
    print(f"observed {len(obs)}, predicted {len(pred)}, same record {len(obs & pred)}")


if __name__ == "__main__":
    main()
