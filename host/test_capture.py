"""End-to-end tests for host/capture against the golden protocol model.

Why: on hardware, capture's log is the only record of what the FPGA sent, and
analysis/compare runs over it. So (1) the config it sends must be exactly what the RTL
accepts (judged here by model.protocol.parse_config, the same oracle test_ctrl uses for the
RTL), (2) it must not start logging before the ack of *its* config (protocol.md: earlier
output belongs to another run), and (3) the log must hold every well-formed FPGA datagram,
in arrival order, byte for byte, while pkt_seq losses, reordering, foreign and malformed
datagrams are counted and kept out. A fake FPGA on loopback plays the board: it answers the
config from capture's source address, as the RTL does.

Run: CAPTURE=build/host-Release/capture pytest host/test_capture.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from model import protocol as P
from model.itch import iter_raw

EXE = os.environ.get("CAPTURE", "build/host-Release/capture")


class FakeFpga:
    """Receives configs on a loopback port; `script(fake, cfg_payload, src)` answers each."""

    def __init__(self, script: Callable[[FakeFpga, bytes, tuple[str, int]], None]) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.configs: list[bytes] = []
        self.script = script
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while not self.done.is_set():
            try:
                data, src = self.sock.recvfrom(2048)
            except TimeoutError:
                continue
            self.configs.append(data)
            self.script(self, data, src)

    def send(self, payload: bytes, dst: tuple[str, int]) -> None:
        self.sock.sendto(payload, dst)

    def close(self) -> None:
        self.done.set()
        self.thread.join()
        self.sock.close()


def run(fake: FakeFpga, log: Path, *args: str, check: bool = True) -> tuple[int, dict[str, str]]:
    cmd = [EXE, "--log", str(log), "--fpga", "127.0.0.1", "--ctrl-port", str(fake.port)]
    cmd += ["--bind-ip", "127.0.0.1", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if check:
        assert r.returncode == 0, r.stderr
    return r.returncode, dict(ln.split("=", 1) for ln in r.stdout.splitlines())


def ack_for(cfg: bytes) -> bytes:
    return P.packet(P.CONFIG_ACK, 0, P.parse(cfg).records)


def stats(seq: int, base: int, n: int = P.N_STATS) -> bytes:
    return P.packet(P.STATS, seq, [(base + i).to_bytes(8) for i in range(n)])


def bbo(seq: int, n: int) -> bytes:
    return P.packet(P.BBO, seq, [bytes([seq, i]) * 32 for i in range(n)])


def trace(seq: int, n: int) -> bytes:
    return P.packet(P.TRACE, seq, [bytes([seq, i]) * 20 for i in range(n)])


def test_stream(tmp_path: Path) -> None:
    """Retry, pre-ack filtering, then a stream with every fault capture must count."""
    other = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    other.bind(("127.0.0.1", 0))
    good: list[bytes] = []

    def script(fake: FakeFpga, cfg: bytes, src: tuple[str, int]) -> None:
        if len(fake.configs) == 1:
            return  # the first config is lost: capture must resend
        # Before the ack: an earlier run's stats and an ack of a different config.
        fake.send(stats(3, 0), src)
        wrong = bytearray(ack_for(cfg))
        wrong[-1] ^= 1
        fake.send(bytes(wrong), src)
        good.append(ack_for(cfg))
        fake.send(good[-1], src)
        other.sendto(bbo(1, 1), src)  # right content, wrong source
        stream = [
            (bbo(1, 3), True),
            (trace(2, 2), True),
            (stats(3, 100), True),
            (b"\x00" + bbo(4, 1)[1:], False),  # bad magic
            (bbo(4, 1)[:-1], False),  # truncated
            (bbo(4, 1) + b"\x00", False),  # one byte too long
            (P.HEADER.pack(P.MAGIC, P.VERSION, P.BBO, 4, 1, 40) + bytes(64), False),  # rec len
            (P.HEADER.pack(P.MAGIC, P.VERSION, 0x7, 4, 1, 8) + bytes(8), False),  # bad type
            (P.packet(P.CONFIG, 4, P.parse(cfg).records), False),  # FPGA never sends 0x10
            (stats(4, 0, P.N_STATS - 1), False),  # a counter short
            (bbo(7, 22), True),  # 4, 5, 6 lost
            (bbo(5, 1), True),  # late
            (stats(8, 1000), True),
        ]
        for pkt, ok in stream:
            fake.send(pkt, src)
            if ok:
                good.append(pkt)

    fake = FakeFpga(script)
    log, ready = tmp_path / "log.bin", tmp_path / "ready"
    try:
        _, s = run(
            fake,
            log,
            "--lo-price",
            "2140000",
            "--retry-ms",
            "100",
            "--idle",
            "0.5",
            "--ready-file",
            str(ready),
        )
    finally:
        fake.close()
        other.close()
    assert len(fake.configs) == 2 and fake.configs[0] == fake.configs[1]
    assert P.parse_config(fake.configs[0]) == P.Config(lo_price=2140000)
    assert ready.exists()
    assert list(iter_raw(log.open("rb"))) == good
    want = {
        "config_sends": 2, "datagrams": 7, "bbo_datagrams": 3, "trace_datagrams": 1,
        "stats_datagrams": 2, "records": 3 + 2 + 22 + 1, "seq_gaps": 1, "seq_lost": 3,
        "seq_backwards": 1, "other_acks": 0, "pre_ack_ignored": 2, "foreign": 1,
        "malformed": 7, "interrupted": 0,
    }  # fmt: skip
    assert {k: int(s[k]) for k in want} == want
    names = [k.removeprefix("stats.") for k in s if k.startswith("stats.")]
    assert names == list(P.STATS_NAMES)
    assert [int(s["stats." + n]) for n in P.STATS_NAMES] == list(range(1000, 1000 + P.N_STATS))


def test_other_ack_restarts_sequence(tmp_path: Path) -> None:
    """Another host's config mid-run: its ack restarts pkt_seq at 0, not a loss."""

    def script(fake: FakeFpga, cfg: bytes, src: tuple[str, int]) -> None:
        for pkt in [ack_for(cfg), bbo(1, 1), bbo(2, 1), ack_for(cfg), bbo(1, 1), stats(2, 0)]:
            fake.send(pkt, src)

    fake = FakeFpga(script)
    try:
        _, s = run(fake, tmp_path / "log.bin", "--lo-price", "0", "--idle", "0.3")
    finally:
        fake.close()
    assert (s["other_acks"], s["seq_gaps"], s["seq_backwards"], s["datagrams"]) == (
        "1",
        "0",
        "0",
        "6",
    )


@pytest.mark.parametrize(
    ("args", "cfg"),
    [
        (["--lo-price", "0"], P.Config()),
        (
            ["--lo-price", "4294967295", "--mode", "trace", "--locate", "65535",
             "--symbol", "ABCDEFGH", "--group", "224.0.0.1", "--feed-port", "65535"],
            P.Config(0xE0000001, 65535, 1, True, "ABCDEFGH", 0xFFFFFFFF, 65535),
        ),
        (["--lo-price", "7", "--locate", "0", "--symbol", "A"],
         P.Config(locate_valid=True, symbol="A", lo_price=7)),
    ],
)  # fmt: skip
def test_config_encoding(tmp_path: Path, args: list[str], cfg: P.Config) -> None:
    """Every field and boundary value as the RTL's parser (the model) reads it."""

    def script(fake: FakeFpga, c: bytes, src: tuple[str, int]) -> None:
        fake.send(ack_for(c), src)

    fake = FakeFpga(script)
    try:
        _, s = run(fake, tmp_path / "log.bin", *args, "--duration", "0.1")
    finally:
        fake.close()
    assert [P.parse_config(c) for c in fake.configs] == [cfg]
    assert s["datagrams"] == "1"


@pytest.mark.parametrize("answer", ["none", "wrong_echo", "wrong_seq"])
def test_no_ack(tmp_path: Path, answer: str) -> None:
    """No valid ack: retry until the timeout, exit 1, log nothing, no ready file."""

    def script(fake: FakeFpga, cfg: bytes, src: tuple[str, int]) -> None:
        if answer == "wrong_echo":
            a = bytearray(ack_for(cfg))
            a[P.HEADER.size + 7] = 1  # locate_valid flipped
            fake.send(bytes(a), src)
        elif answer == "wrong_seq":
            fake.send(P.packet(P.CONFIG_ACK, 1, P.parse(cfg).records), src)

    fake = FakeFpga(script)
    log, ready = tmp_path / "log.bin", tmp_path / "ready"
    try:
        rc, _ = run(
            fake,
            log,
            "--lo-price",
            "0",
            "--retry-ms",
            "100",
            "--ack-timeout",
            "0.45",
            "--ready-file",
            str(ready),
            check=False,
        )
    finally:
        fake.close()
    assert rc == 1
    assert 4 <= len(fake.configs) <= 5
    assert log.read_bytes() == b"" and not ready.exists()


@pytest.mark.parametrize(
    "args",
    [
        [],  # no --lo-price
        ["--lo-price", "4294967296"],
        ["--lo-price", "0", "--mode", "bb"],
        ["--lo-price", "0", "--symbol", "ABCDEFGHI"],
        ["--lo-price", "0", "--symbol", ""],
        ["--lo-price", "0", "--locate", "65536"],
        ["--lo-price", "-1"],
        ["--lo-price", "0", "--idle", "-1"],
        ["--lo-price", "0", "--feed-port", "0"],
        ["--lo-price", "0", "--retry-ms", "0"],
        ["--lo-price"],
    ],
)
def test_bad_args(tmp_path: Path, args: list[str]) -> None:
    r = subprocess.run([EXE, "--log", str(tmp_path / "l"), *args], capture_output=True)
    assert r.returncode == 2


@pytest.mark.parametrize("group", ["192.168.1.1", "239.1.1", "240.0.0.1"])
def test_bad_group(tmp_path: Path, group: str) -> None:
    r = subprocess.run(
        [EXE, "--log", str(tmp_path / "l"), "--lo-price", "0", "--group", group],
        capture_output=True, text=True,
    )  # fmt: skip
    assert r.returncode == 1 and "--group" in r.stderr
