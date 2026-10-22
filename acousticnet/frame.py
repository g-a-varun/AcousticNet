"""
frame.py - what one AcousticNet frame looks like as bits.

On air (left to right in time):

  [preroll][ chirp ][guard][ SYNC 16 bits ][ HEADER 7 bytes ] [ PAYLOAD 0..255 bytes | CRC-16 ]
   ^ pure sound, built in phy.py          ^ always at the robust     ^ at the speed (MCS) the
                                            BASE rate (2-FSK)          header announces

HEADER (7 bytes):
  byte 0  TYPE (high 4 bits) | FLAGS (low 4 bits)
  byte 1  SRC   link-layer source address
  byte 2  DST   link-layer destination address (0xFF = everyone)
  byte 3  SEQ   sequence number (0..255, wraps around)
  byte 4  LEN   payload length in bytes
  byte 5  MCS   speed of the payload (see config.build_mcs_table)
  byte 6  CRC-8 of bytes 0..5

Why a separate CRC-8 on the header?  The receiver must trust LEN and MCS to know how many
more symbols to wait for and how to read them.  IPv4 has a header checksum for the same reason.

CRC-16 covers header bytes 0..5 + payload, so a body can't be glued to the wrong header.

The SYNC word also tells the receiver whether the header is FEC-coded:
  SYNC_PLAIN = 0xB3A5  -> header sent as raw bits
  SYNC_FEC   = 0x4C5A  -> header Hamming(7,4)-coded + interleaved (used with MCS 0)
(0x4C5A is the bit-wise opposite of 0xB3A5, so the two can't be confused.)
"""
from dataclasses import dataclass

import numpy as np

from .coding import (bytes_to_bits, bits_to_bytes, crc8, crc16_ccitt, fec_encode,
                     fec_decode, fec_bits_for, int_to_bits)

SYNC_PLAIN = 0xB3A5
SYNC_FEC = 0x4C5A
SYNC_BITS = 16
HEADER_BYTES = 7
CRC_BYTES = 2
MAX_PAYLOAD = 255

# Frame types -----------------------------------------------------------------
T_DATA = 1      # a chunk of user data (ARQ)
T_ACK = 2       # acknowledgement (ARQ)
T_PING = 3      # "are you there?" (acoustic ping)
T_PONG = 4      # reply to ping (carries the SNR at which the ping was heard)
T_CTRL = 5      # experiment control (start / report requests)
T_MAC = 6       # MAC-experiment traffic frame
T_BER = 7       # bit-error-rate test frame (known pseudo-random payload)
T_MSG = 8       # one-shot unreliable text message
T_ROUTE = 9     # distance-vector routing advertisement
T_TTLX = 10     # "time to live exceeded" (what traceroute listens for, like ICMP type 11)
T_ALERT = 11    # "I refuse this transfer, here is why" (like a TLS alert)

TYPE_NAMES = {T_DATA: "DATA", T_ACK: "ACK", T_PING: "PING", T_PONG: "PONG", T_CTRL: "CTRL",
              T_MAC: "MAC", T_BER: "BER", T_MSG: "MSG", T_ROUTE: "ROUTE", T_TTLX: "TTLX",
              T_ALERT: "ALERT"}

# Flags (low nibble of byte 0) -------------------------------------------------
F_POLL = 0x1    # "please answer with an ACK right after this frame"
F_SR = 0x2      # sender is using Selective Repeat (else Go-Back-N / Stop-and-Wait)
F_NET = 0x4     # payload starts with a 3-byte network header [net_src, net_dst, ttl] (routing)


@dataclass
class Frame:
    type: int
    src: int
    dst: int
    seq: int = 0
    payload: bytes = b""
    flags: int = 0
    mcs: int = None                 # payload speed; None = the node's default
    # ---- filled in by the receiver only ----
    crc_ok: bool = True
    fec: bool = False
    fec_fixed: int = 0              # Hamming blocks corrected
    rx_start: int = 0               # absolute sample index where the chirp started
    rx_end: int = 0                 # absolute sample index of the last payload sample
    snr_db: float = 0.0             # tone signal-to-noise ratio of the base-rate header
    quality: float = 0.0            # 0..1, how clearly the payload symbols were separated
    sync_errors: int = 0
    link_src: int = None            # set by the network layer when a frame was relayed

    @property
    def name(self) -> str:
        return TYPE_NAMES.get(self.type, f"T{self.type}")

    def header_bytes(self) -> bytes:
        if not 0 <= len(self.payload) <= MAX_PAYLOAD:
            raise ValueError("payload too long")
        h = bytes([((self.type & 0xF) << 4) | (self.flags & 0xF), self.src & 0xFF, self.dst & 0xFF,
                   self.seq & 0xFF, len(self.payload), (self.mcs or 0) & 0xFF])
        return h + bytes([crc8(h)])

    def __repr__(self):
        return (f"Frame({self.name} {self.src}->{self.dst} seq={self.seq} len={len(self.payload)} mcs={self.mcs}"
                f"{' POLL' if self.flags & F_POLL else ''}{'' if self.crc_ok else ' CRC-FAIL'})")


def header_bit_count(fec: bool) -> int:
    return fec_bits_for(HEADER_BYTES) if fec else HEADER_BYTES * 8


def body_bit_count(payload_len: int, mcs) -> int:
    """payload + CRC bits, after FEC, padded to whole symbols of this MCS"""
    n = payload_len + CRC_BYTES
    bits = fec_bits_for(n) if mcs.fec else n * 8
    k = mcs.bits_per_symbol
    return -(-bits // k) * k


def body_symbol_count(payload_len: int, mcs) -> int:
    return body_bit_count(payload_len, mcs) // mcs.bits_per_symbol


def encode_frame_parts(frame: Frame, mcs):
    """-> (base_bits, body_bits): base part = sync + header (2-FSK, base rate);
    body = payload + CRC-16 (sent with the frame's MCS, padded to whole symbols)."""
    frame.mcs = mcs.index
    head = frame.header_bytes()
    body = bytes(frame.payload) + crc16_ccitt(head[:6] + bytes(frame.payload)).to_bytes(2, "big")
    if mcs.fec:
        base = np.concatenate([int_to_bits(SYNC_FEC, 16), fec_encode(head)])
        body_bits = fec_encode(body)
    else:
        base = np.concatenate([int_to_bits(SYNC_PLAIN, 16), bytes_to_bits(head)])
        body_bits = bytes_to_bits(body)
    pad = body_bit_count(len(frame.payload), mcs) - len(body_bits)
    if pad:
        body_bits = np.concatenate([body_bits, np.zeros(pad, np.uint8)])
    return base, body_bits


def parse_header(bits, fec: bool):
    """header bits -> (dict, fixed_blocks) or (None, fixed) if CRC-8 fails"""
    if fec:
        head, fixed = fec_decode(bits)
    else:
        head, fixed = bits_to_bytes(bits), 0
    if crc8(head[:6]) != head[6]:
        return None, fixed
    return {"type": head[0] >> 4, "flags": head[0] & 0xF, "src": head[1], "dst": head[2],
            "seq": head[3], "len": head[4], "mcs": head[5], "raw": head[:6]}, fixed


def parse_body(bits, hdr: dict, mcs):
    """body bits (padded) -> (payload, crc_ok, fixed_blocks)"""
    n = hdr["len"] + CRC_BYTES
    if mcs.fec:
        body, fixed = fec_decode(bits[: fec_bits_for(n)])
    else:
        body, fixed = bits_to_bytes(bits[: n * 8]), 0
    payload, rx_crc = body[:-2], int.from_bytes(body[-2:], "big")
    return payload, crc16_ccitt(hdr["raw"] + payload) == rx_crc, fixed
