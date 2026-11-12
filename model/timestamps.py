"""Golden model of rtl/meas/t0_track (ADR 0011): matching each frame's T0 to the frame.

One `step` is one core clock cycle. Within a cycle every check uses the queue sizes from
before the cycle, as the RTL does: a status and an SFD in the same cycle, or an ok and a
frame start, see the counts as they were.

Cut-through (ADR 0013): a frame leaves eth_rx before its status, so a frame start takes
its T0 from PEND directly and DONE is unused. `emitted` (eth_rx `emitted_o`, valid with a
status) says the frame put a beat on eth_rx's output, so its PEND entry was taken by its
start; the status discards only the others it covers. When a start and a status share a
cycle they are the same frame: the start takes the head, the status the entries after it.
A frame of 5 bytes can start after its status (its one beat waits on backpressure): an
emitted status with no start since the previous status moves the head into `hold`, and
the start takes it from there.
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
    cut_through: bool = False
    hold: tuple[int, bool] | None = None  # cut-through: T0 of a frame judged before its start
    started: bool = False  # cut-through: a frame started since the last status

    def step(
        self,
        now: int,
        sfd: bool = False,
        ok: bool = False,
        drop: bool = False,
        ovf_frames: int = 0,
        frame_start: bool = False,
        emitted: bool = False,
    ) -> tuple[int, bool] | None:
        """Apply one cycle. Returns (t0, valid) for a frame start, else None. At most one
        of ok, drop, ovf_frames > 0 per cycle (eth_rx's statuses are per frame).
        `emitted` is read only in cut-through, with a status."""
        if (ok, drop, ovf_frames > 0).count(True) > 1:
            raise ValueError("one eth_rx status per cycle")
        if self.cut_through:
            return self._step_ct(
                now, sfd, ok or drop or ovf_frames > 0, ovf_frames, emitted, frame_start
            )
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

    def _step_ct(
        self, now: int, sfd: bool, status: bool, ovf_frames: int, emitted: bool, start: bool
    ) -> tuple[int, bool] | None:
        pend_size, hold = len(self.pend), self.hold
        lost = 0
        out = None
        if start:
            if hold is not None:
                out, self.hold = hold, None
            elif pend_size == 0:
                lost += 1
                out = (0, False)
            else:
                out = (self.pend.popleft(), True)
        if status:
            want = ovf_frames if ovf_frames else 1
            avail = pend_size - (start and hold is None)  # the start took the head
            if emitted:
                want -= 1
                if not (self.started or start):  # its start is still to come: keep its T0
                    self.hold = (self.pend.popleft(), True) if avail > 0 else (0, False)
                    lost += avail == 0
                    avail = max(avail - 1, 0)
            lost += want > avail
            for _ in range(min(want, avail)):
                self.pend.popleft()
        if sfd:
            if pend_size == self.pend_n:
                lost += 1
            else:
                self.pend.append(now)
        if status:
            self.started = False
        elif start and hold is None:  # a start from hold is a frame already judged
            self.started = True
        self.lost += lost > 0
        return out
