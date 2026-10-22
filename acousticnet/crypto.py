"""
crypto.py - security for a medium that EVERYONE in the room can hear.

Sound is broadcast: any laptop nearby can record our frames (see `sniff`).  So, like TLS
protects web traffic, we protect the file transfer at the application layer:

  1. Key derivation   passphrase --PBKDF2-HMAC-SHA256 (20 000 rounds)--> master key
                      master --HMAC--> encryption key, authentication key  (never reuse one key
                      for two jobs).  The 20 000 rounds make guessing passphrases slow.
  2. Encryption       keystream = HMAC-SHA256(enc_key, nonce || block counter)   (a PRF used in
                      counter mode = a stream cipher);  ciphertext = plaintext XOR keystream
  3. Authentication   tag = first 6 bytes of HMAC-SHA256(auth_key, nonce || ciphertext)
                      ("encrypt-then-MAC").  Any change to the ciphertext, or a wrong key,
                      makes the tag check fail -> the receiver throws the message away.
  4. Freshness        nonce = random 4-byte session id || 4-byte message number.  The receiver
                      remembers session ids it has completed, so playing back a RECORDING of an
                      old transfer (replay attack) is refused.

Analogy: encryption is a locked box (nobody can read the letter inside); the tag is a wax seal
with your family crest (nobody can swap the letter without breaking the seal); the session id is
the date on the letter (an old letter re-delivered tomorrow is spotted).

Only Python's standard library is used (hashlib, hmac, os).  Honest limits are listed in the
README: the ARQ ACKs themselves are not authenticated, and a 48-bit tag is a deliberate trade
of strength for airtime (each tag byte costs 80 ms at 100 bit/s).
"""
import hashlib
import hmac
import os

TAG_BYTES = 6
NONCE_BYTES = 8
SESSION_BYTES = 4


class AuthError(Exception):
    """wrong key, or the message was modified on the way"""


class SecureChannel:
    def __init__(self, passphrase: str):
        if not passphrase:
            raise ValueError("empty passphrase")
        master = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), b"AcousticNet-v1", 20000)
        self.enc_key = hmac.new(master, b"encryption", hashlib.sha256).digest()
        self.mac_key = hmac.new(master, b"authentication", hashlib.sha256).digest()

    @staticmethod
    def new_session() -> bytes:
        return os.urandom(SESSION_BYTES)

    @staticmethod
    def nonce(session: bytes, index: int) -> bytes:
        return session + int(index).to_bytes(NONCE_BYTES - SESSION_BYTES, "big")

    def _keystream(self, nonce: bytes, n: int) -> bytes:
        out = bytearray()
        block = 0
        while len(out) < n:
            out += hmac.new(self.enc_key, nonce + block.to_bytes(4, "big"), hashlib.sha256).digest()
            block += 1
        return bytes(out[:n])

    def seal(self, nonce: bytes, plaintext: bytes) -> bytes:
        ks = self._keystream(nonce, len(plaintext))
        ct = bytes(a ^ b for a, b in zip(plaintext, ks))
        tag = hmac.new(self.mac_key, nonce + ct, hashlib.sha256).digest()[:TAG_BYTES]
        return ct + tag

    def open(self, nonce: bytes, sealed: bytes) -> bytes:
        if len(sealed) < TAG_BYTES:
            raise AuthError("message too short")
        ct, tag = sealed[:-TAG_BYTES], sealed[-TAG_BYTES:]
        good = hmac.new(self.mac_key, nonce + ct, hashlib.sha256).digest()[:TAG_BYTES]
        if not hmac.compare_digest(tag, good):                 # constant-time comparison
            raise AuthError("authentication tag mismatch")
        ks = self._keystream(nonce, len(ct))
        return bytes(a ^ b for a, b in zip(ct, ks))
