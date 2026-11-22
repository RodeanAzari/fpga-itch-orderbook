"""One feed frame on the board through MII -> eth_rx FIFO -> udp_rx -> ITCH decoder (ILA).

Usage: python -m analysis.ila_frame --mii MII.csv --core CORE.csv --packets PKTS [--symbol S]
       [--table]

MII.csv / CORE.csv are `write_hw_ila_data -csv_file` dumps of the two ILA cores of
`make impl ILA=1` (syn/ila_probes.tcl), captured by syn/ila_capture.tcl during a replay of
PKTS (host/replay --out). Checks, each printed as name=0/1, all must be 1:
  mii_fcs      the MII frame (nibbles low first, after the SFD) has a good FCS (CRC-32)
  fifo_eq_mii  eth_rx's output frame = the MII frame without its FCS
  payload_eq   the feed udp_rx's payload = that frame's UDP payload (IPv4 header length,
               UDP length from the frame)
  sent         that payload is a packet the host sent (its index in PKTS)
  records      the decoder's record types for it = the golden model's for that packet
               (model.moldudp64.FeedDecoder over PKTS up to it)
Exit status 0 only if every check is 1. --table also prints the README's sample tables.
"""

from __future__ import annotations

import argparse
import csv
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

from model.itch import iter_raw
from model.moldudp64 import FeedDecoder

# Probe roles by net name (syn/ila_probes.tcl probes ob_core's nets: eth_rx out f_*,
# feed udp_rx out u_*, decoder records r_*).
# MII: mii_rx's IOB input register (the pins one rx_clk later; the pins can't be probed).
_M = "u_core/u_eth_rx/u_mii_rx/"
MII_ROLES = {_M + "rxd_q": "rxd", _M + "dv_q_reg_0": "dv", _M + "er_q": "er"}
CORE_ROLES = {
    "u_core/f_tdata": "f_data",
    "u_core/f_tvalid": "f_valid",
    "u_core/f_tready": "f_ready",
    "u_core/f_tlast": "f_last",
    "u_core/u_tdata": "u_data",
    "u_core/u_tvalid": "u_valid",
    "u_core/u_tready": "u_ready",
    "u_core/u_tlast": "u_last",
    "u_core/r_valid": "r_valid",
    "u_core/r_ready": "r_ready",
    "u_core/r_type": "r_type",
    "u_core/e_ok": "ok",
}
PREAMBLE, SFD = 0x55, 0xD5
ETH_HDR, UDP_HDR = 14, 8


@dataclass
class Trace:
    cols: dict[str, list[int]]  # role -> sample values
    trigger: int  # sample index of the trigger


def load(path: Path, roles: dict[str, str]) -> Trace:
    """A Vivado ILA CSV: a header of probe names, a radix row, then one row per sample."""
    with path.open() as f:
        rows = list(csv.reader(f))
    head, radix, data = rows[0], rows[1], rows[2:]
    cols: dict[str, list[int]] = {}
    for i, name in enumerate(head):
        base = name.split("[")[0].strip()
        role = roles.get(base)
        if role is None:
            continue
        if role in cols:
            raise ValueError(f"{path}: two probes for {role}")
        base_n = 16 if "HEX" in radix[i].upper() else 2 if "BIN" in radix[i].upper() else 10
        cols[role] = [int(r[i], base_n) for r in data]
    missing = set(roles.values()) - cols.keys()
    if missing:
        raise ValueError(f"{path}: no probe for {sorted(missing)} in {head}")
    trig_col = head.index("TRIGGER")
    trigger = next(k for k, r in enumerate(data) if r[trig_col].strip() == "1")
    return Trace(cols, trigger)


def mii_frame(t: Trace) -> tuple[bytes, int, int]:
    """The first whole rx_dv run from the trigger: (bytes after the SFD, sfd sample, end)."""
    dv, rxd = t.cols["dv"], t.cols["rxd"]
    start = t.trigger
    while start > 0 and dv[start - 1]:
        start -= 1
    if start == 0 and dv[0]:
        raise ValueError("MII frame starts before the capture window")
    end = start
    while end < len(dv) and dv[end]:
        end += 1
    if end == len(dv):
        raise ValueError("MII frame runs past the capture window")
    nib = rxd[start:end]
    if len(nib) % 2:
        raise ValueError(f"odd nibble count {len(nib)}")
    octets = bytes(nib[i] | nib[i + 1] << 4 for i in range(0, len(nib), 2))  # low nibble first
    k = 0
    while k < len(octets) and octets[k] == PREAMBLE:
        k += 1
    if k >= len(octets) or octets[k] != SFD:
        raise ValueError(f"no SFD after {k} preamble bytes")
    return octets[k + 1 :], start + 2 * k, end


def stream(t: Trace, p: str, from_sample: int) -> tuple[bytes, int, int]:
    """The first AXI-Stream packet on prefix p whose first beat is at or after
    from_sample: (bytes, first beat sample, last beat sample). Only a packet known to be
    whole counts: one after a tlast in the window, or after the window opened idle."""
    d, v, r, last = (t.cols[f"{p}_{s}"] for s in ("data", "valid", "ready", "last"))
    whole = not v[0]  # a packet starting later than sample 0 on an idle stream is whole
    out = bytearray()
    first = -1
    for j in range(len(v)):
        if not (v[j] and r[j]):
            continue
        if first < 0 and j >= from_sample and whole:
            first = j
        if first >= 0:
            out.append(d[j])
            if last[j]:
                return bytes(out), first, j
        elif last[j]:
            whole = True
    raise ValueError(f"{p}: no whole packet from sample {from_sample} in the window")


def records(t: Trace, a: int, b: int) -> list[int]:
    """Record types the decoder handed on in samples [a, b]."""
    v, r, ty = t.cols["r_valid"], t.cols["r_ready"], t.cols["r_type"]
    return [ty[j] for j in range(a, b + 1) if v[j] and r[j]]


def udp_payload(frame: bytes) -> bytes:
    ihl = (frame[ETH_HDR] & 0x0F) * 4
    udp = ETH_HDR + ihl
    length = int.from_bytes(frame[udp + 4 : udp + 6], "big")
    return frame[udp + UDP_HDR : udp + length]


def check(
    mii: Trace, core: Trace, pkts: list[bytes], symbol: str = "AAPL"
) -> tuple[dict[str, int], dict[str, object]]:
    wire, sfd_at, mii_end = mii_frame(mii)
    fcs_ok = zlib.crc32(wire[:-4]) == int.from_bytes(wire[-4:], "little")
    frame, f0, f1 = stream(core, "f", 0)
    payload, u0, u1 = stream(core, "u", f0)
    want = udp_payload(frame)
    idx = pkts.index(payload) if payload in pkts else -1
    got = records(core, u0, len(core.cols["r_valid"]) - 1)
    dec = FeedDecoder(symbol)
    exp: list[int] = []
    for k in range(idx + 1):
        exp = [rec.kind for rec in dec.feed(pkts[k])]
    if idx < 0:
        exp = [-1]
    got = got[: len(exp)] if len(got) >= len(exp) else got
    res = {
        "mii_fcs": int(fcs_ok),
        "fifo_eq_mii": int(frame == wire[:-4]),
        "payload_eq": int(payload == want),
        "sent": int(idx >= 0),
        "records": int(got == exp),
    }
    info: dict[str, object] = {
        "frame_bytes": len(frame),
        "payload_bytes": len(payload),
        "packet_index": idx,
        "records_n": len(got),
        "record_types": bytes(got).decode("ascii", "replace"),
        "sfd_sample": sfd_at,
        "mii_end": mii_end,
        "f_first": f0,
        "f_last": f1,
        "u_first": u0,
        "u_last": u1,
        "wire": wire,
        "frame": frame,
        "payload": payload,
    }
    return res, info


# Frame fields by byte offset after the SFD (Ethernet II, IPv4 without options, UDP,
# MoldUDP64 header: session 10, sequence 8, count 2; buildplan 5).
FIELDS = [(0, 6, "destination MAC (IPv4 multicast 01:00:5e:..)"), (6, 12, "source MAC"),
          (12, 14, "EtherType 0x0800 (IPv4)"), (14, 15, "IPv4 version 4, IHL 5"),
          (23, 24, "IPv4 protocol 17 (UDP)"), (30, 34, "IPv4 destination (feed group)"),
          (36, 38, "UDP destination port"), (42, 52, "MoldUDP64 session"),
          (52, 60, "MoldUDP64 sequence number"), (60, 62, "MoldUDP64 message count"),
          (62, 64, "first message length"), (64, 65, "first message type")]  # fmt: skip


def tables(mii: Trace, core: Trace, info: dict[str, object]) -> str:
    """Markdown sample tables for the README (buildplan 11.4 item 7)."""
    rxd, dv = mii.cols["rxd"], mii.cols["dv"]
    sfd = int(str(info["sfd_sample"]))  # first nibble of the 0xD5 SFD byte
    out = ["MII (rx_clk 25 MHz, 40 ns per sample; mii_rx's input register):", "",
           "| Sample | rx_dv | rxd | Byte (low nibble first) | |",
           "|---|---|---|---|---|"]  # fmt: skip
    for j in range(sfd - 4, sfd + 14):
        byte = ""
        if (j - sfd) % 2 == 1:
            byte = f"0x{rxd[j - 1] | rxd[j] << 4:02X}"
        note = ("preamble" if j < sfd else "SFD" if j <= sfd + 1
                else "destination MAC" if j < sfd + 14 else "")  # fmt: skip
        out.append(f"| {j} | {dv[j]} | 0x{rxd[j]:X} | {byte} | {note} |")
    f0, u0 = int(str(info["f_first"])), int(str(info["u_first"]))
    d, v, r = core.cols["f_data"], core.cols["f_valid"], core.cols["f_ready"]
    beats = [j for j in range(f0, len(v)) if v[j] and r[j]]
    out += ["", "eth_rx output and feed udp_rx payload (core clock 125 MHz, 8 ns per sample):",
            "", "| Byte offset | eth_rx sample | eth_rx out | udp_rx sample | udp_rx payload "
            "| Field |",
            "|---|---|---|---|---|---|"]  # fmt: skip
    ud, uv, ur = core.cols["u_data"], core.cols["u_valid"], core.cols["u_ready"]
    ubeats = [j for j in range(u0, len(uv)) if uv[j] and ur[j]]
    frame = info["frame"]
    assert isinstance(frame, bytes)
    hdr = ETH_HDR + (frame[ETH_HDR] & 0x0F) * 4 + UDP_HDR  # payload byte k = frame byte hdr+k
    for lo, hi, name in FIELDS:
        for off in range(lo, hi):
            j = beats[off]
            us, pay = "", ""
            if off >= hdr:
                us, pay = str(ubeats[off - hdr]), f"0x{ud[ubeats[off - hdr]]:02X}"
            label = name if off == lo else ""
            out.append(f"| {off} | {j} | 0x{d[j]:02X} | {us} | {pay} | {label} |")
    rv, rr, rt = core.cols["r_valid"], core.cols["r_ready"], core.cols["r_type"]
    recs = [j for j in range(u0, len(rv)) if rv[j] and rr[j]][:5]
    out += ["", "First decoder records:", "", "| Sample | Type |", "|---|---|"]
    out += [f"| {j} | {chr(rt[j])} |" for j in recs]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mii", type=Path, required=True)
    ap.add_argument("--core", type=Path, required=True)
    ap.add_argument("--packets", type=Path, required=True)
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--table", action="store_true", help="also print the README tables")
    a = ap.parse_args()
    mii, core = load(a.mii, MII_ROLES), load(a.core, CORE_ROLES)
    with a.packets.open("rb") as f:
        pkts = list(iter_raw(f))
    res, info = check(mii, core, pkts, a.symbol)
    for k in (
        "frame_bytes",
        "payload_bytes",
        "packet_index",
        "records_n",
        "record_types",
        "sfd_sample",
        "mii_end",
        "f_first",
        "f_last",
        "u_first",
        "u_last",
    ):
        print(f"{k}={info[k]}")
    for k, v in res.items():
        print(f"{k}={v}")
    if a.table:
        print(tables(mii, core, info))
    return 0 if all(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
