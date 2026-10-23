"""
AcousticNet - a small computer network whose "cable" is sound.

Layers (bottom to top) and the file that implements each:
    hardware     audio.py      sound card / simulated room        (+ channel.py room model)
    physical     phy.py        2-, 4-, 16-FSK modem, chirp sync
    receive      receiver.py   finds frames in a continuous mic stream
    framing      frame.py      header, CRC-8, CRC-16, sync words  (+ coding.py: CRC/Hamming)
    node         link.py       one laptop: send, receive, carrier sense
    data link    arq.py        Stop-and-Wait, Go-Back-N, Selective Repeat;  rate.py: adaptive speed
    MAC          mac.py        ALOHA, Slotted ALOHA, CSMA experiment (+ theory.py)
    network      network.py    distance-vector routing, forwarding, TTL, traceroute
    application  transfer.py   file transfer, ping;  bertest.py: BER test
    security     crypto.py     encryption + authentication;  sniffer.py: Eve the eavesdropper
    tools        cli.py, dashboard.py, diagnostics.py, events.py, virtual.py, scenarios.py
"""
__version__ = "1.0.0"
