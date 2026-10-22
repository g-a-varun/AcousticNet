"""
config.py - every tunable number of AcousticNet lives here.

Why one file?  So that the modem, the receiver, the ARQ timers and the
experiments all agree on the same numbers, and you change a value once.

The most important rule (explained in README section "Why 1400 Hz and 2600 Hz"):
    each tone must fit a WHOLE number of cycles into one symbol.
    cycles per symbol = tone_frequency / baud
    1400 / 100 = 14 cycles, 2600 / 100 = 26 cycles  -> OK
That makes the two tones "orthogonal": when we measure how much 1400 Hz is
in a symbol, a perfect 2600 Hz symbol contributes exactly zero.
"""
import math
from dataclasses import dataclass, replace

SUPPORTED_BAUDS = (25, 50, 100, 200)


@dataclass(frozen=True)
class PhyConfig:
    # --- sampling ---------------------------------------------------------
    fs: int = 48000            # samples per second (sound card rate)
    baud: int = 100            # symbols (bits) per second on air

    # --- the two FSK tones ------------------------------------------------
    f0: float = 1400.0         # tone that means bit 0
    f1: float = 2600.0         # tone that means bit 1
    amp: float = 0.5           # peak amplitude of generated sound (0..1)

    # --- preamble (the "hey, a frame is coming" part) -----------------------
    preroll_s: float = 0.05    # soft tone that wakes up mic noise-gates / AGC
    chirp_s: float = 0.10      # length of the sync chirp
    chirp_lo: float = 900.0    # chirp sweeps from this frequency ...
    chirp_hi: float = 3100.0   # ... up to this one
    guard_syms: int = 2        # silent symbols between chirp and data (lets chirp echo die)
    ramp_s: float = 0.004      # fade-in/fade-out so the speaker does not "click"

    # --- receiver ----------------------------------------------------------
    detect_threshold: float = 0.30   # normalised chirp correlation needed to try decoding
    band_lo: float = 700.0           # band-pass used for detection / carrier sense
    band_hi: float = 4000.0          # (covers the fastest 16-tone mode, 800-3800 Hz)
    max_sync_errors: int = 2         # bit errors tolerated in the 16-bit sync word

    def __post_init__(self):
        if self.fs % self.baud:
            raise ValueError(f"fs={self.fs} must be divisible by baud={self.baud}")
        for f in (self.f0, self.f1):
            if (f / self.baud) != int(f / self.baud):
                raise ValueError(
                    f"tone {f} Hz is not a whole number of cycles per symbol at {self.baud} baud; "
                    f"use baud in {SUPPORTED_BAUDS}")

    @property
    def sps(self) -> int:
        """samples per symbol"""
        return self.fs // self.baud

    def with_(self, **kw) -> "PhyConfig":
        return replace(self, **kw)


# =============================================================================
# Speeds ("MCS" = modulation and coding scheme, the same word Wi-Fi uses)
#
# Every frame starts with the preamble + sync + header at the robust BASE rate
# (2-FSK, cfg.baud).  The header carries an MCS number that says how the PAYLOAD that
# follows is sent.  So a receiver can always read the header, and the sender can pick a
# faster payload speed when the room is quiet.  802.11 Wi-Fi does exactly this: the PLCP
# header goes at the lowest rate and announces the rate of the rest of the frame.
#
# M-FSK: instead of 2 tones (1 bit per symbol) we use M tones; each symbol is ONE of them,
# so it carries log2(M) bits.  16 tones -> 4 bits per symbol.  Tones are all whole
# multiples of the symbol rate, spaced at least one symbol rate apart -> still orthogonal.
# =============================================================================

@dataclass(frozen=True)
class Mcs:
    index: int
    name: str
    baud: int
    tones: tuple            # frequency of each symbol value (len = M, a power of 2)
    fec: bool               # Hamming(7,4) + interleaving on the payload (and header)
    min_snr_db: float       # header-SNR this speed needs for >= 75 % of frames (measured in sim)

    @property
    def bits_per_symbol(self) -> int:
        return int(math.log2(len(self.tones)))

    @property
    def rate_bps(self) -> float:
        return self.baud * self.bits_per_symbol * (4 / 7 if self.fec else 1)

    def label(self) -> str:
        return f"MCS{self.index} {len(self.tones)}-FSK {self.baud} Bd{' +FEC' if self.fec else ''} ({self.rate_bps:.0f} bit/s)"


def build_mcs_table(cfg: "PhyConfig"):
    """MCS 0/1 follow the configured base rate (so --baud 50 slows them down);
    MCS 2-4 are fixed fast modes."""
    base = (cfg.f0, cfg.f1)
    return (
        Mcs(0, "robust", cfg.baud, base, True, MCS_MIN_SNR[0]),
        Mcs(1, "normal", cfg.baud, base, False, MCS_MIN_SNR[1]),
        Mcs(2, "fast", 100, (1100.0, 1700.0, 2300.0, 2900.0), False, MCS_MIN_SNR[2]),
        Mcs(3, "faster", 100, tuple(float(f) for f in range(1100, 2700, 100)), False, MCS_MIN_SNR[3]),
        Mcs(4, "fastest", 200, tuple(float(f) for f in range(800, 4000, 200)), False, MCS_MIN_SNR[4]),
    )


# Header-SNR (dB, as MEASURED by the receiver) at which each speed delivers >= 75 % of its
# 32-byte frames; printed by experiments/phy_sim.py ("mcs" experiment, results/sim/phy_sim.csv).
# 75 % rather than 99 %: ARQ repairs the rest, and a faster speed that loses 1 frame in 4 still
# beats a slower one (rate_sim.py checked the choice).  MCS 1-3 need the SAME signal: 16-FSK
# sends 4 bits per tone with the same tone length, so each bit gets more energy - M-FSK trades
# bandwidth for robustness.  The measured header SNR saturates around 20 dB in an echoey room,
# so rate control also watches real frame losses.
MCS_MIN_SNR = (12.0, 14.5, 14.5, 14.5, 16.0)
# Payload bytes per frame for each starting speed: faster speeds carry bigger frames,
# otherwise the slow preamble+header would dominate (Wi-Fi's frame aggregation idea).
CHUNK_FOR_MCS = (32, 32, 48, 64, 96)


@dataclass(frozen=True)
class LinkConfig:
    """Timers and sizes for the data-link layer (ARQ) and file transfer."""
    chunk_size: int = 32          # payload bytes per DATA frame
    window: int = 4               # frames per burst (Go-Back-N / Selective Repeat)
    max_tries: int = 25           # consecutive silent timeouts before giving up
    processing_margin_s: float = 1.0   # extra slack added to ACK timeout
    default_rt_latency_s: float = 0.35 # used when no calibration file exists
    linger_s: float = 20.0        # receiver keeps re-ACKing after finishing (like TCP TIME_WAIT)


# Frame / address conventions -------------------------------------------------
BROADCAST = 0xFF
SINK_ADDR = 100          # address used by the MAC-experiment "sink" (base station)

DEFAULT_PHY = PhyConfig()
DEFAULT_LINK = LinkConfig()
