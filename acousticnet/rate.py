"""
rate.py - adaptive speed ("rate adaptation"), the way Wi-Fi does it.

Every ACK from the receiver now carries the signal-to-noise ratio (SNR) at which it heard our
last burst.  After every burst the RateController decides the speed (MCS) of the next one:

  * frames were lost (more than 1 in 4)  -> step DOWN one speed       (Auto Rate Fallback, ARF)
  * reported SNR too low for this speed  -> jump down to a safe speed (SNR-guided)
  * two clean bursts in a row and SNR has room for one more speed -> step UP one (probing)
  * a clean burst and SNR good enough for a speed two or more steps faster -> jump UP straight
    to it (otherwise recovering from the slowest speed would take minutes)
  * no answer at all (timeout)           -> step DOWN one speed; a second timeout in a row
                                            -> straight to the most robust speed

Analogy: driving on a road you don't know.  You speed up a little after a few smooth
kilometres, slow down at the first pothole, and brake hard if you can see the fog is thick.

Why not just always use the fastest speed?  Fast M-FSK packs more bits into each tone, so it
needs a cleaner signal.  In a noisy room, frames at the fast speed are lost and every loss
costs a retransmission - you end up slower than a careful medium speed.
"""


class RateController:
    def __init__(self, modem, start: int = 1, margin_db: float = 0.5, up_after: int = 2,
                 bus=None, clock=None, lowest: int = None, highest: int = None):
        self.table = modem.mcs_table
        levels = sorted(modem.available_mcs)
        lo = levels[0] if lowest is None else lowest
        hi = levels[-1] if highest is None else highest
        self.levels = [m for m in levels if lo <= m <= hi]
        self.cur = min(max(start, self.levels[0]), self.levels[-1])
        self.margin_db = margin_db
        self.up_after = up_after
        self.streak = 0
        self.silent = 0                     # consecutive timeouts
        self.bus = bus
        self.clock = clock
        self.history = [self.cur]

    @property
    def current(self) -> int:
        return self.cur

    def best_for_snr(self, snr_db: float) -> int:
        ok = [m for m in self.levels if self.table[m].min_snr_db + self.margin_db <= snr_db]
        return max(ok) if ok else self.levels[0]

    def _set(self, new, reason, snr=None):
        if new != self.cur:
            old, self.cur = self.cur, new
            if self.bus is not None:
                self.bus.emit("rate", t=round(self.clock(), 3) if self.clock else None, old=old, new=new,
                              name=self.table[new].label(), bps=round(self.table[new].rate_bps),
                              reason=reason, snr=None if snr is None else round(snr, 1))
        self.history.append(self.cur)

    def _step(self, direction):
        i = self.levels.index(self.cur) + direction
        return self.levels[min(max(i, 0), len(self.levels) - 1)]

    def on_burst(self, sent: int, lost: int, snr_db: float = None):
        self.silent = 0
        ceiling = self.best_for_snr(snr_db) if snr_db else self.levels[-1]
        if sent and lost * 4 > sent:
            self.streak = 0
            self._set(min(self._step(-1), ceiling), f"{lost} of {sent} frames lost", snr_db)
            return
        self.streak = self.streak + 1 if lost == 0 else 0
        gap = self.levels.index(ceiling) - self.levels.index(self.cur)
        if gap < 0:
            self.streak = 0
            self._set(ceiling, "SNR too low for this speed", snr_db)
        elif gap >= 2 and lost == 0 and snr_db:
            self.streak = 0
            self._set(ceiling, "strong signal, jumping up", snr_db)
        elif gap >= 1 and self.streak >= self.up_after:
            self.streak = 0
            self._set(self._step(+1), "clean bursts, trying faster", snr_db)
        else:
            self.history.append(self.cur)

    def on_timeout(self):
        """no answer at all: once may be bad luck, twice in a row means the channel collapsed"""
        self.streak = 0
        self.silent += 1
        if self.silent >= 2:
            self._set(self.levels[0], "no answer twice: most robust speed")
        else:
            self._set(self._step(-1), "no answer (timeout)")
