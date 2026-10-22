"""Security layer: encryption, authentication, replay protection and the eavesdropper."""
import os
import tempfile
import threading

import pytest

from acousticnet.arq import ArqSender, LinkFailed
from acousticnet.crypto import SecureChannel, AuthError
from acousticnet.events import EventBus
from acousticnet.frame import Frame, T_DATA
from acousticnet.sniffer import Sniffer, load_capture
from acousticnet.transfer import make_messages, receive_file
from acousticnet.virtual import VirtualNet, run_virtual_transfer


def test_seal_open_roundtrip_and_failures():
    ch = SecureChannel("correct horse")
    n = ch.nonce(b"\x01\x02\x03\x04", 7)
    sealed = ch.seal(n, b"top secret message")
    assert b"secret" not in sealed
    assert ch.open(n, sealed) == b"top secret message"
    with pytest.raises(AuthError):
        SecureChannel("wrong horse").open(n, sealed)               # wrong key
    bad = bytearray(sealed)
    bad[3] ^= 1
    with pytest.raises(AuthError):
        ch.open(n, bytes(bad))                                       # tampered
    with pytest.raises(AuthError):
        ch.open(ch.nonce(b"\x01\x02\x03\x04", 8), sealed)           # moved to another position
    assert ch.seal(ch.nonce(b"\x01\x02\x03\x04", 8), b"top secret message") != sealed


def test_encrypted_transfer_and_refusals():
    data = os.urandom(300)
    _, got = run_virtual_transfer(data, "sr", 4, key="vit", seed=1)
    assert got == data
    cases = [(dict(key="vit", rx_key="tiv"), "authentication failed"),
             (dict(key="vit", rx_key=""), "has no key"),
             (dict(rx_key="vit"), "unencrypted file refused")]
    for kw, text in cases:
        with pytest.raises(LinkFailed, match=text):
            run_virtual_transfer(data, "sr", 4, seed=2, **kw)


def _receiver(net, link, out_dir, key, box):
    def run():
        try:
            box.append(receive_file(link, out_dir, linger=20, idle_timeout=200, key=key))
        finally:
            link.done()
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_replay_of_recorded_session_is_refused():
    out_dir = tempfile.mkdtemp()
    msgs, sid = make_messages(b"pay 100 rupees to Eve" * 4, "order.txt", 32, SecureChannel("k"))
    # first delivery: accepted
    net = VirtualNet(2, seed=1)
    a, b = net.link(1), net.link(2)
    box = []
    t = _receiver(net, b, out_dir, "k", box)
    try:
        ArqSender(a, 2, "sr", 4).send_all(msgs)
    finally:
        a.done()
    t.join(10)
    assert box and box[0][1]
    # the very same recording played again: refused
    net = VirtualNet(2, seed=2)
    a, b = net.link(1), net.link(2)
    box = []
    t = _receiver(net, b, out_dir, "k", box)
    try:
        with pytest.raises(LinkFailed, match="replay"):
            ArqSender(a, 2, "sr", 4).send_all(msgs)
    finally:
        a.done()
    t.join(10)


class _FakeNode:
    def __init__(self):
        self.bus = EventBus()
        self.raw_listeners = []
        self.promiscuous = False

    def now(self):
        return 0.0


def test_sniffer_steals_plain_files_but_not_encrypted_ones(tmp_path):
    node = _FakeNode()
    events = []
    node.bus.subscribe(events.append)
    sn = Sniffer(node, str(tmp_path))
    secret = b"the exam answers are A, C, B, D\n" * 3
    plain, _ = make_messages(secret, "answers.txt", 32)
    for i, m in enumerate(plain):
        sn.on_frame(Frame(T_DATA, 1, 2, i, m))
    stolen = [e for e in events if e["kind"] == "sniff_file"]
    assert stolen and open(stolen[0]["path"], "rb").read() == secret
    enc, _ = make_messages(secret, "answers2.txt", 32, SecureChannel("k"))
    for i, m in enumerate(enc):
        sn.on_frame(Frame(T_DATA, 1, 2, i, m))
    assert any(e["kind"] == "sniff_encrypted" for e in events)
    assert sn.stats["stolen_files"] == 1


def test_sniffer_recording_can_be_replayed_and_is_refused(tmp_path):
    """Eve records an encrypted transfer (she cannot read it), plays the RECORDING back to Bob
    pretending to be Alice, and Bob refuses it: the session id was already used."""
    node = _FakeNode()
    sn = Sniffer(node, str(tmp_path / "eve"))
    out_dir = str(tmp_path / "bob")
    msgs, _ = make_messages(b"transfer 500 rupees to Bob\n" * 3, "order.txt", 32, SecureChannel("k"))
    net = VirtualNet(2, seed=4)
    a, b = net.link(1), net.link(2)
    box = []
    t = _receiver(net, b, out_dir, "k", box)
    try:
        ArqSender(a, 2, "sr", 4).send_all(msgs)
    finally:
        a.done()
    t.join(10)
    assert box and box[0][1]
    for i, m in enumerate(msgs):                    # what Eve's microphone picked up meanwhile
        sn.on_frame(Frame(T_DATA, 1, 2, i, m))
    src, dst, recorded = load_capture(sn.flows[(1, 2)]["capture"])
    assert (src, dst) == (1, 2) and recorded == msgs
    net = VirtualNet(2, seed=5)
    eve_as_alice, b = net.link(1), net.link(2)
    t = _receiver(net, b, out_dir, "k", [])
    try:
        with pytest.raises(LinkFailed, match="replay"):
            ArqSender(eve_as_alice, 2, "sr", 4).send_all(recorded)
    finally:
        eve_as_alice.done()
    t.join(10)
