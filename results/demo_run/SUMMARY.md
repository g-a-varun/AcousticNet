# AcousticNet - demo run results

Run on 24 Oct 2026 on the ROG Strix. Every result below is the real protocol code
(modem, CRC, FEC, ARQ, rate control, encryption, routing) running through the simulated
acoustic room built into the project (`sim-demo`). No microphone or speaker is used, so
room noise does not affect these runs.

| # | What was checked | Result | Log file |
|---|---|---|---|
| 1 | Automated tests for every layer | **75 / 75 passed** | 01_tests.txt |
| 2 | File transfer at base speed, noise burst (clap) at 8 s | Burst destroyed DATA 2; **only DATA 2 was resent**; **SHA-256 matches** (175 bytes) | 02_file_transfer_with_noise.txt |
| 3 | Adaptive speed, noisy fan on at 25 s and off at 60 s | Started at **800 bit/s**, fell to **57 bit/s** when the fan came on, jumped back to **800 bit/s** after; 3048-byte file, **SHA-256 matches** | 03_adaptive_speed.txt |
| 4 | Security | Plain file: Eve **stole it**. Encrypted file: Eve saw **only random bytes**, Bob got it intact. Replay of Eve's recording: **refused**. Forged file with a guessed key: **refused** | 04_security.txt |
| 5 | Routing through a relay (wall between A and B) | Route found via node 3, cost 2 hops; traceroute **1 -> 3 -> 2**; file relayed, **SHA-256 matches**; 8 frames forwarded | 05_relay_routing.txt |

Received files are in `received_files/` (Eve's stolen copy is in `received_files/eve/`).

## Evidence from real laptops (23 Oct 2026)

- MacBook Pro <-> ROG Strix acoustic ping over real air: 3 of 3 replies, CRC OK,
  SNR 24-26 dB, round trip 9.2 s.
- ROG Strix received DATA frames over real air with CRC OK.
- Full file transfers between real laptops were unreliable in an echoey room. Causes
  found: about 400 ms Windows audio latency, the receiver's replies being the weaker
  direction, and long frames losing sync. Fix added: `--robust` (short FEC-protected
  frames at full volume).

## Dashboard screenshots (`screenshots/`)

Taken from the live dashboard while each simulated run was in progress, using the same
code as this folder (copied to Claude's workspace for the screenshots).

| File | Shows |
|---|---|
| 01_transfer_in_progress | Bits arriving as 2-FSK tones, chunks turning green |
| 02a / 02b | The noise burst button pressed; DATA #3 fails CRC; only DATA #3 is resent |
| 03_file_complete_sha256_matches | All chunks confirmed, SHA-256 matches |
| 04 to 07 | Adaptive speed: fastest 16-FSK, drop to the robust speed when the fan starts, jump back up, file complete |
| 08 to 10 | Relay: routes learned, traceroute 1 -> 3 -> 2, file relayed, SHA-256 matches |
