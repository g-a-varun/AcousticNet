"""
coding.py - bits, error DETECTION and error CORRECTION.

    bits <-> bytes ............ helpers
    crc16_ccitt ............... 16-bit CRC that protects every frame body
    crc8 ...................... 8-bit CRC that protects the small frame header
    internet_checksum, parity . used only in the experiment that compares
                                detection strength (experiments/errdetect_sim.py)
    hamming74_encode/decode ... forward error correction (fixes 1 bad bit in 7)
    interleave/deinterleave ... spreads a burst of errors over many codewords

Analogy: CRC is a seal on an envelope (tells you it was opened, can't fix it).
Hamming is a spelling checker (fixes one typo per word).  Interleaving is
writing the letter across many pages so one coffee stain only smudges one
letter per word, which the spelling checker can then fix.
"""
import numpy as np

# --------------------------------------------------------------------------
# bits <-> bytes
# --------------------------------------------------------------------------

def bytes_to_bits(data: bytes) -> np.ndarray:
    """b'\\x80' -> [1,0,0,0,0,0,0,0]  (most significant bit first)"""
    return np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8))


def bits_to_bytes(bits) -> bytes:
    bits = np.asarray(bits, dtype=np.uint8)
    n = len(bits) // 8
    return np.packbits(bits[: n * 8]).tobytes()


def bits_to_int(bits) -> int:
    v = 0
    for b in np.asarray(bits, dtype=np.uint8):
        v = (v << 1) | int(b)
    return v


def int_to_bits(value: int, width: int) -> np.ndarray:
    return np.array([(value >> (width - 1 - i)) & 1 for i in range(width)], dtype=np.uint8)


# --------------------------------------------------------------------------
# CRC-16/CCITT-FALSE  (poly 0x1021, init 0xFFFF)  check("123456789") = 0x29B1
# --------------------------------------------------------------------------

def _make_crc16_table():
    table = []
    for byte in range(256):
        crc = byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


_CRC16_TABLE = _make_crc16_table()


def crc16_ccitt(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        crc = ((crc << 8) & 0xFFFF) ^ _CRC16_TABLE[((crc >> 8) ^ b) & 0xFF]
    return crc


# --------------------------------------------------------------------------
# CRC-8 (poly 0x07, init 0x00)  check("123456789") = 0xF4
# --------------------------------------------------------------------------

def _make_crc8_table():
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
        table.append(crc)
    return table


_CRC8_TABLE = _make_crc8_table()


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = _CRC8_TABLE[crc ^ b]
    return crc


# --------------------------------------------------------------------------
# Weaker detectors (only for the comparison experiment)
# --------------------------------------------------------------------------

def internet_checksum(data: bytes) -> int:
    """16-bit one's-complement sum, as used by IPv4/TCP/UDP headers."""
    if len(data) % 2:
        data = bytes(data) + b"\x00"
    s = 0
    for i in range(0, len(data), 2):
        s += (data[i] << 8) | data[i + 1]
        s = (s & 0xFFFF) + (s >> 16)
    return (~s) & 0xFFFF


def parity_bit(data: bytes) -> int:
    return int(np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8)).sum() & 1)


# --------------------------------------------------------------------------
# Hamming(7,4): 4 data bits -> 7 bits; corrects any single bit error per block
# Codeword layout (positions 1..7): p1 p2 d1 p3 d2 d3 d4
# --------------------------------------------------------------------------

# generator: codeword = data @ G (mod 2)
_G = np.array([
    # p1 p2 d1 p3 d2 d3 d4
    [1, 1, 1, 0, 0, 0, 0],   # d1
    [1, 0, 0, 1, 1, 0, 0],   # d2
    [0, 1, 0, 1, 0, 1, 0],   # d3
    [1, 1, 0, 1, 0, 0, 1],   # d4
], dtype=np.uint8)

# parity-check: syndrome = H @ codeword (mod 2); syndrome value = error position
_H = np.array([
    [1, 0, 1, 0, 1, 0, 1],   # checks positions 1,3,5,7
    [0, 1, 1, 0, 0, 1, 1],   # checks positions 2,3,6,7
    [0, 0, 0, 1, 1, 1, 1],   # checks positions 4,5,6,7
], dtype=np.uint8)

_DATA_POS = np.array([2, 4, 5, 6])  # 0-based indices of d1..d4 inside a codeword


def hamming74_encode(bits) -> np.ndarray:
    bits = np.asarray(bits, dtype=np.uint8)
    if len(bits) % 4:
        raise ValueError("Hamming(7,4) needs a multiple of 4 bits")
    blocks = bits.reshape(-1, 4)
    return ((blocks @ _G) % 2).astype(np.uint8).reshape(-1)


def hamming74_decode(bits):
    """Returns (data_bits, number_of_corrected_blocks)."""
    bits = np.asarray(bits, dtype=np.uint8)
    if len(bits) % 7:
        raise ValueError("Hamming(7,4) decode needs a multiple of 7 bits")
    cw = bits.reshape(-1, 7).copy()
    syn = (cw @ _H.T) % 2                      # shape (n, 3)
    pos = syn[:, 0] * 1 + syn[:, 1] * 2 + syn[:, 2] * 4   # 1..7, 0 = no error
    bad = np.flatnonzero(pos)
    cw[bad, pos[bad] - 1] ^= 1
    return cw[:, _DATA_POS].reshape(-1).astype(np.uint8), int(len(bad))


# --------------------------------------------------------------------------
# Block interleaver: write codewords as rows, transmit column by column.
# A burst of up to n_codewords consecutive bad bits then hits each codeword
# at most once, which Hamming can fix.
# --------------------------------------------------------------------------

def interleave(bits, width: int = 7) -> np.ndarray:
    bits = np.asarray(bits, dtype=np.uint8)
    return bits.reshape(-1, width).T.reshape(-1).copy()


def deinterleave(bits, width: int = 7) -> np.ndarray:
    bits = np.asarray(bits, dtype=np.uint8)
    return bits.reshape(width, -1).T.reshape(-1).copy()


def fec_encode(data: bytes) -> np.ndarray:
    """bytes -> Hamming(7,4) coded + interleaved bits (7/4 times longer)."""
    return interleave(hamming74_encode(bytes_to_bits(data)))


def fec_decode(bits):
    """inverse of fec_encode -> (bytes, corrected_blocks)"""
    data_bits, fixed = hamming74_decode(deinterleave(bits))
    return bits_to_bytes(data_bits), fixed


def fec_bits_for(nbytes: int) -> int:
    return nbytes * 2 * 7
