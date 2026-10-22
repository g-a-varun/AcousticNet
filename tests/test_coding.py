"""Unit tests for coding.py and frame.py (no audio needed)."""
import numpy as np
import pytest

from acousticnet.coding import (crc16_ccitt, crc8, internet_checksum, hamming74_encode,
                                hamming74_decode, interleave, deinterleave, fec_encode,
                                fec_decode, bytes_to_bits, bits_to_bytes)
from acousticnet.config import DEFAULT_PHY, build_mcs_table
from acousticnet.frame import (Frame, T_DATA, F_POLL, encode_frame_parts, parse_header,
                               parse_body, header_bit_count, body_bit_count, SYNC_BITS)

MCS = build_mcs_table(DEFAULT_PHY)


def test_crc_known_check_values():
    # standard "check" values for these CRC variants
    assert crc16_ccitt(b"123456789") == 0x29B1
    assert crc8(b"123456789") == 0xF4


def test_internet_checksum_known_value():
    # classic RFC 1071 example words: 0001 f203 f4f5 f6f7 -> sum 0xddf2 -> checksum 0x220d
    assert internet_checksum(bytes.fromhex("0001f203f4f5f6f7")) == 0x220D


def test_bits_roundtrip():
    data = bytes(range(256))
    assert bits_to_bytes(bytes_to_bits(data)) == data


def test_hamming_corrects_every_single_bit_error():
    rng = np.random.default_rng(0)
    data = rng.integers(0, 2, 400).astype(np.uint8)
    cw = hamming74_encode(data)
    for blk in range(len(cw) // 7):
        for pos in range(7):
            bad = cw.copy()
            bad[blk * 7 + pos] ^= 1
            out, fixed = hamming74_decode(bad)
            assert fixed == 1
            assert np.array_equal(out, data)


def test_interleaver_spreads_bursts():
    data = bytes(range(32))
    coded = fec_encode(data)
    n_codewords = len(coded) // 7
    # a burst as long as the number of codewords hits each codeword once -> fully fixed
    bad = coded.copy()
    bad[100:100 + n_codewords] ^= 1
    out, fixed = fec_decode(bad)
    assert out == data
    assert fixed == n_codewords


def test_interleave_inverse():
    x = np.arange(70) % 2
    assert np.array_equal(deinterleave(interleave(x)), x)


@pytest.mark.parametrize("mcs", [0, 1, 2, 3, 4])
def test_frame_bits_roundtrip(mcs):
    m = MCS[mcs]
    f = Frame(T_DATA, src=3, dst=7, seq=200, payload=b"hello acoustic world", flags=F_POLL)
    base, body = encode_frame_parts(f, m)
    hb = header_bit_count(m.fec)
    assert len(base) == SYNC_BITS + hb
    hdr, _ = parse_header(base[SYNC_BITS:], m.fec)
    assert hdr["src"] == 3 and hdr["dst"] == 7 and hdr["seq"] == 200 and hdr["flags"] == F_POLL
    assert hdr["mcs"] == mcs
    assert len(body) == body_bit_count(len(f.payload), m)
    assert len(body) % m.bits_per_symbol == 0
    payload, ok, _ = parse_body(body, hdr, m)
    assert ok and payload == f.payload


def test_header_crc_rejects_corruption():
    base, _ = encode_frame_parts(Frame(T_DATA, 1, 2, 5, b"x"), MCS[1])
    base[SYNC_BITS + 3] ^= 1
    hdr, _ = parse_header(base[SYNC_BITS:], False)
    assert hdr is None


def test_body_crc_detects_corruption():
    f = Frame(T_DATA, 1, 2, 5, b"payload!")
    base, body = encode_frame_parts(f, MCS[1])
    hdr, _ = parse_header(base[SYNC_BITS:], False)
    body = body.copy()
    body[10] ^= 1
    _, ok, _ = parse_body(body, hdr, MCS[1])
    assert not ok
