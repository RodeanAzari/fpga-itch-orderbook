"""End-to-end tests for host/replay against the Python golden model.

Why: on hardware the FPGA's decode is compared with model.moldudp64 run over the same day
file. That comparison is only meaningful if replay sends exactly what the model's
packetize() describes: same batching, same sequence numbers, same end of session, and
faults on exactly the packets its flags name. Here replay writes its packets to a file
(--out), and each run is checked byte for byte against packetize() plus an independent
re-statement of the fault rules, then decoded by SeqTracker to check the receiver-side
effect (a drop is a gap, a duplicate is dropped, a swap is one gap and one late packet).
Pacing is checked by lower bounds on elapsed time only (busy-waiting guarantees them;
upper bounds would be flaky on a shared host). A loopback UDP run checks the socket path.

Run: REPLAY=build/host-Release/replay pytest host/test_replay.py
"""

from __future__ import annotations

import gzip
import io
import os
import socket
import struct
import subprocess
from pathlib import Path

import pytest

from model import generator
from model.itch import iter_raw
from model.moldudp64 import COUNT_END, SeqTracker, end_of_session, packetize

EXE = os.environ.get("REPLAY", "build/host-Release/replay")


def messages(max_payload: int = 1472) -> list[bytes]:
    """Generator output plus edge cases it does not make: empty, 1-byte, and the largest
    message that fits; then only what fits in max_payload (header 20 + prefix 2)."""
    msgs = generator.generate(seed=7, count=3000)
    msgs = msgs[:10] + [b"", b"Z", bytes(1450)] + msgs[10:]
    return [m for m in msgs if len(m) <= max_payload - 22]


def itch_file(path: Path, msgs: list[bytes], gz: bool = False) -> Path:
    data = b"".join(struct.pack(">H", len(m)) + m for m in msgs)
    path.write_bytes(gzip.compress(data) if gz else data)
    return path


def run(*args: str) -> dict[str, str]:
    out = subprocess.run([EXE, *args], capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.splitlines())


def read_packets(path: Path) -> list[bytes]:
    return list(iter_raw(io.BytesIO(path.read_bytes())))


def faulted(pkts: list[bytes], drop: int, dup: int, swap: int) -> list[bytes]:
    """The fault rules as replay's usage states them, written independently of the C++."""
    out: list[bytes] = []
    held: bytes | None = None
    for n, p in enumerate(pkts, start=1):
        if drop and n % drop == 0:
            continue
        if swap and n % swap == 0 and held is None:
            held = p
            continue
        out += [p, p] if dup and n % dup == 0 else [p]
        if held is not None:
            out.append(held)
            held = None
    if held is not None:
        out.append(held)
    return out


def session(name: str) -> bytes:
    return name.encode().ljust(10)


@pytest.mark.parametrize("gz", [False, True])
@pytest.mark.parametrize(
    ("max_payload", "first_seq", "skip", "count"),
    [(1472, 1, 0, 0), (200, 1, 0, 0), (1472, 2**40, 5, 1000), (60, 7, 0, 50)],
)
def test_matches_packetize(
    tmp_path: Path, gz: bool, max_payload: int, first_seq: int, skip: int, count: int
) -> None:
    msgs = messages(max_payload)
    src = itch_file(tmp_path / ("in.itch.gz" if gz else "in.itch"), msgs, gz)
    out = tmp_path / "out.bin"
    s = run(
        "--in",
        str(src),
        "--out",
        str(out),
        "--max-payload",
        str(max_payload),
        "--first-seq",
        str(first_seq),
        "--skip",
        str(skip),
        "--count",
        str(count),
        "--session",
        "ABC",
    )
    sent = msgs[skip : skip + count] if count else msgs[skip:]
    want = list(packetize(sent, session("ABC"), first_seq, max_payload))
    next_seq = first_seq + len(sent)
    assert read_packets(out) == want + [end_of_session(session("ABC"), next_seq)]
    assert int(s["messages"]) == len(sent)
    assert int(s["next_seq"]) == next_seq
    assert int(s["packets_built"]) == len(want)
    assert int(s["packets_sent"]) == len(want) + 1
    assert s["interrupted"] == "0"


@pytest.mark.parametrize(
    ("drop", "dup", "swap"), [(5, 0, 0), (0, 3, 0), (0, 0, 4), (6, 4, 3), (2, 1, 0)]
)
def test_faults(tmp_path: Path, drop: int, dup: int, swap: int) -> None:
    msgs = messages(300)
    src = itch_file(tmp_path / "in.itch", msgs)
    out = tmp_path / "out.bin"
    s = run(
        "--in",
        str(src),
        "--out",
        str(out),
        "--max-payload",
        "300",
        "--drop-every",
        str(drop),
        "--dup-every",
        str(dup),
        "--swap-every",
        str(swap),
    )
    clean = list(packetize(msgs, session("REPLAY0001"), 1, 300))
    want = faulted(clean, drop, dup, swap)
    got = read_packets(out)
    assert got[:-1] == want
    assert got[-1] == end_of_session(session("REPLAY0001"), 1 + len(msgs))

    # Receiver view: every surviving message is delivered exactly once, and only dropped
    # packets' messages are missing.
    t = SeqTracker()
    seqs = [seq for p in got for seq, _ in t.feed(p)]
    assert len(seqs) == len(set(seqs))
    dropped = {n for n in range(1, len(clean) + 1) if drop and n % drop == 0}
    lost = sum(struct.unpack_from(">H", clean[n - 1], 18)[0] for n in dropped)
    assert int(s["dropped_packets"]) == len(dropped)
    assert int(s["dropped_messages"]) == lost
    # A swapped packet arrives after its successor: the successor opens a gap and the late
    # packet is dropped as a duplicate. (A swap of the very last packet goes out in order.)
    first = [struct.unpack_from(">Q", p, 10)[0] for p in want]
    late = [i for i in range(1, len(first)) if first[i] < max(first[:i])]
    late = [i for i in late if want[i] != want[i - 1]]  # a dup's second copy is not late
    late_msgs = sum(struct.unpack_from(">H", want[i], 18)[0] for i in late)
    assert len(seqs) == len(msgs) - lost - late_msgs
    assert bool(late) == (swap > 0)
    assert t.c.duplicates == int(s["duplicated_packets"]) + len(late)
    assert t.c.end_of_session == 1
    assert int(s["packets_sent"]) == len(got)


def test_pps_pacing_lower_bound(tmp_path: Path) -> None:
    src = itch_file(tmp_path / "in.itch", messages(100))
    s = run("--in", str(src), "--out", str(tmp_path / "o"), "--max-payload", "100", "--pps", "2000")
    built = int(s["packets_built"])
    assert built > 300
    # Packet k is due at k / pps; the end-of-session packet is the last one sent.
    assert float(s["elapsed_s"]) >= (built - 1) / 2000
    assert s["mode"] == "pps"
    assert float(s["max_lag_ms"]) >= 0  # reported in pps mode too: a lag is a burst


def test_flat_out_reports_no_lag(tmp_path: Path) -> None:
    src = itch_file(tmp_path / "in.itch", messages(100))
    s = run("--in", str(src), "--out", str(tmp_path / "o"))
    assert "max_lag_ms" not in s  # no schedule, so no lag to report


@pytest.mark.parametrize("speed", [1.0, 4.0])
def test_realtime_follows_itch_timestamps(tmp_path: Path, speed: float) -> None:
    span_ns = 200_000_000
    msgs = [b"S" + bytes(4) + (i * span_ns // 99).to_bytes(6) + b"O" for i in range(100)]
    src = itch_file(tmp_path / "in.itch", msgs)
    # 1 message per packet, so every message's timestamp sets a due time.
    s = run(
        "--in",
        str(src),
        "--out",
        str(tmp_path / "o"),
        "--max-payload",
        "34",
        "--realtime",
        "--speed",
        str(speed),
    )
    assert int(s["packets_built"]) == 100
    assert float(s["elapsed_s"]) >= span_ns / 1e9 / speed
    assert float(s["max_lag_ms"]) >= 0


def test_udp_loopback(tmp_path: Path) -> None:
    msgs = messages()
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(2.0)
    src = itch_file(tmp_path / "in.itch", msgs)
    s = run(
        "--in", str(src), "--dst", "127.0.0.1", "--port", str(rx.getsockname()[1]), "--pps", "20000"
    )
    got = []
    while len(got) < int(s["packets_sent"]):
        got.append(rx.recv(2048))
    want = list(packetize(msgs, session("REPLAY0001"))) + [
        end_of_session(session("REPLAY0001"), 1 + len(msgs))
    ]
    # Loopback may reorder across CPUs (see rtl/eth/host/test_udp_seq_send.py): compare sets.
    assert sorted(got) == sorted(want)
    assert struct.unpack_from(">H", want[-1], 18)[0] == COUNT_END


@pytest.mark.parametrize(
    "bad",
    [
        ["--in", "x"],  # neither --dst nor --out
        ["--in", "x", "--out", "o", "--dst", "127.0.0.1", "--port", "1"],  # both
        ["--in", "x", "--out", "o", "--pps", "10", "--realtime"],
        ["--in", "x", "--out", "o", "--max-payload", "1473"],
        ["--in", "x", "--out", "o", "--first-seq", "0"],
        ["--in", "x", "--out", "o", "--count", "-1"],
        ["--in", "x", "--out", "o", "--speed", "0"],
    ],
)
def test_rejects_bad_arguments(bad: list[str]) -> None:
    assert subprocess.run([EXE, *bad], capture_output=True).returncode == 2


def test_fails_loud_on_bad_input(tmp_path: Path) -> None:
    trunc = tmp_path / "t.itch"
    trunc.write_bytes(b"\x00\x05ab")
    r = subprocess.run(
        [EXE, "--in", str(trunc), "--out", str(tmp_path / "o")], capture_output=True, text=True
    )
    assert r.returncode == 1 and "ends inside" in r.stderr
    r = subprocess.run(
        [EXE, "--in", str(tmp_path / "missing"), "--out", str(tmp_path / "o")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 1 and "cannot open" in r.stderr
    r = subprocess.run(
        [EXE, "--in", str(trunc), "--out", "o", "--drop-every", "1"], capture_output=True, text=True
    )
    assert r.returncode == 1 and "drop_every" in r.stderr
