"""Golden model of rtl/meas/t0_track (ADR 0011): matching each frame's T0 to the frame.

One `step` is one core clock cycle. Within a cycle every check uses the queue sizes from
before the cycle, as the RTL does: a status and an SFD in the same cycle, or an ok and a
frame start, see the counts as they were.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class T0Track:
    pend_n: int = 64
    done_n: int = 128
    pend: deque[int] = field(default_factory=deque)
    done: deque[int | None] = field(default_factory=deque)  # None: T0 lost for that frame
    lost: int = 0

    def step(
        self,
        now: int,
        sfd: bool = False,
        ok: bool = False,
        drop: bool = False,
        ovf_frames: int = 0,
        frame_start: bool = False,
    ) -> tuple[int, bool] | None:
        """Apply one cycle. Returns (t0, valid) for a frame start, else None. At most one
        of ok, drop, ovf_frames > 0 per cycle (eth_rx's statuses are per frame)."""
        if (ok, drop, ovf_frames > 0).count(True) > 1:
            raise ValueError("one eth_rx status per cycle")
        pend_size, done_size = len(self.pend), len(self.done)
        lost = 0
        if ok or drop or ovf_frames:
            want = ovf_frames if ovf_frames else 1
            short = want > pend_size
            taken = [self.pend.popleft() for _ in range(min(want, pend_size))]
            lost += short
            if ok:
                if done_size == self.done_n:
                    lost += 1
                else:
                    self.done.append(None if short else taken[0])
        if sfd:
            if pend_size == self.pend_n:
                lost += 1
            else:
                self.pend.append(now)
        out = None
        if frame_start:
            if done_size == 0:
                lost += 1
                out = (0, False)
            else:
                t0 = self.done.popleft()
                out = (0, False) if t0 is None else (t0, True)
        self.lost += lost > 0  # the RTL's lost_o is one pulse per cycle
        return out
