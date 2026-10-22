"""
AcousticNet - a small computer network whose "cable" is sound.

Layers (bottom to top) and the file that implements each:
    physical     phy.py        2-FSK modem, chirp sync           (+ channel.py room model)
    receive      receiver.py   finds frames in a continuous mic stream
    framing      frame.py      header, CRC-8, CRC-16, sync words  (+ coding.py: CRC/Hamming)
    hardware     audio.py      sound card / simulated room
    node         link.py       one laptop: send, receive, carrier sense
    data link    arq.py        Stop-and-Wait, Go-Back-N, Selective Repeat
    MAC          mac.py        ALOHA, Slotted ALOHA, CSMA experiment (+ theory.py)
    application  transfer.py   file transfer, ping;  bertest.py: BER test
    tools        cli.py, dashboard.py, diagnostics.py, events.py, virtual.py
"""
__version__ = "1.0.0"
