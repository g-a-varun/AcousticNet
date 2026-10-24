# AcousticNet: Demo and Viva Guide

A complete guide to presenting AcousticNet and answering questions on it: the pitch, the demo, every concept layer by layer, the results, and the questions examiners ask.

---

## Contents

**Part A. Know it in five minutes**
1. The one-minute pitch
2. Ten numbers to remember
3. The six ideas the project rests on

**Part B. Showcasing the demo**
4. The demo, step by step (one laptop)
5. Live demo on three laptops
6. The real-hardware story
7. If something goes wrong

**Part C. The concepts, layer by layer**
8. The stack at a glance
9. Physical layer: bits to sound
10. The streaming receiver
11. Framing
12. Error detection and correction
13. Reliability: ARQ
14. Adaptive speed
15. Medium access: ALOHA and CSMA
16. Network layer: routing through a relay
17. Security
18. Application layer
19. How the results were produced

**Part D. Results**
20. Figure by figure
21. What happened on real laptops

**Part E. Viva questions and answers**
22. Questions by topic

**Part F. Quick reference**
23. Formula sheet
24. Command sheet
25. Glossary

---

# Part A. Know it in five minutes

## 1. The one-minute pitch

> "AcousticNet is a complete computer network that uses sound instead of radio. Laptops with Wi-Fi, Bluetooth and cables switched off send files to each other through their speakers and microphones. We wrote every layer ourselves in Python: a modem with five speeds, framing, CRC and Hamming error control, three ARQ protocols, Wi-Fi-style adaptive speed, an ALOHA/CSMA testbed, distance-vector routing through a relay laptop, and encryption tested against an eavesdropping laptop. Every claim is backed by simulations of the real code compared against theory, and 75 automated tests."

Why sound is a good choice, in one sentence: **it is a real, shared, noisy, half-duplex medium where every problem in the syllabus (noise, errors, collisions, delay, routing, eavesdropping) happens audibly in front of you.**

## 2. Ten numbers to remember

| # | Number | What it is |
|---|---|---|
| 1 | **1400 Hz / 2600 Hz** | Tone for bit 0 / bit 1 at base speed |
| 2 | **100 baud = 100 bit/s** | Base speed; five speeds from **57 to 800 bit/s** |
| 3 | **7 bytes** | Header (TYPE+FLAGS, SRC, DST, SEQ, LEN, MCS, CRC-8) |
| 4 | **CRC-16** missed **2 of 360,000** | Corrupted messages in the error-detection test; none of the bursts up to 16 bits |
| 5 | **Selective Repeat W=8: 11** resends vs **Go-Back-N W=8: 50** | At 20 % loss, 40-chunk file |
| 6 | **4.8×** | Adaptive speed vs base speed on a clean channel (290 vs 61 bit/s) |
| 7 | **44.4 % / 24.6 %** | Slotted / pure ALOHA peak for 3 stations (textbook: 36.8 % / 18.4 %) |
| 8 | **0.69 → 0.49** | CSMA throughput, zero vs realistic sound-card latency |
| 9 | **40 of 50** | Triangle runs that still count to infinity even with poison reverse |
| 10 | **75** | Automated tests, all passing |

## 3. The six ideas the project rests on

1. **FSK survives a bad channel.** The receiver only asks *which* tone is louder, never *how* loud, so distance and volume changes don't matter.
2. **Detect, correct, resend.** CRC detects damage, Hamming fixes small damage, ARQ resends what is still broken.
3. **Half-duplex forces bursts.** A laptop can't hear while it talks, so the sender sends a burst and the last frame says "your turn" (POLL), like Wi-Fi Block-ACK.
4. **Speed adapts to the channel.** The header always goes at the robust speed and announces the payload's speed, like Wi-Fi's PLCP header.
5. **Theory must match the setup.** Three laptops are compared with the 3-station formula, not the infinite-station textbook one.
6. **A broadcast medium needs encryption.** Everyone in the room hears everything, so confidentiality, integrity and freshness must be built in.

---

# Part B. Showcasing the demo

## 4. The demo, step by step (one laptop)

**Everything runs on one laptop.** `sim-demo` runs the real protocol code (modem, receiver, CRC, ARQ, rate control, encryption, routing) through a simulated room inside the computer. It uses no microphone and no speaker, so a noisy classroom cannot affect it. Mute the laptop.

Before the review: close old terminals and browser tabs, open PowerShell in the project folder, and keep the `results\figures` and `results\demo_run\screenshots` folders open in a window.

Between demos press **Ctrl+C** to close the previous one.

### Step 1. The tests (start it while you introduce the project)
```
py -m pytest -q
```
**You'll see:** `75 passed`.
**Say:** "Every layer has its own tests: CRC check values, Hamming fixing every single-bit error, all ARQ modes delivering byte-exact files at up to 35 % loss, routing, encryption and the full stack in a simulated room."

### Step 2. File transfer, noise and Selective Repeat (about 40 s)
```
py -m acousticnet sim-demo --rate base
```
**What to point at, in order:**
1. **The spectrogram.** "Each thin column is one bit. A tone in the teal lane is a 0 (1400 Hz), in the amber lane a 1 (2600 Hz). The rising streak before each frame is the sync chirp."
2. **The chunk squares** turning green, and "RX DATA n OK" in the event log: each frame passed its CRC.
3. **Press "Make a noise burst"** right after "RX DATA 1 OK" appears. The next frame is damaged: the log shows a failed check (header CRC-8 or CRC-16), and its square stays empty.
4. **The ACK.** "The receiver reports what it has. Only the damaged frame is sent again, not the ones after it. That is Selective Repeat."
5. **The end:** "SHA-256 matches: all 175 bytes arrived exactly as sent."

**Say:** "CRC-16 detected the damage, the frame was thrown away, and ARQ resent only what was lost."

### Step 3. Adaptive speed (about 1 min)
```
py -m acousticnet sim-demo --scenario speed --speed 2
```
**Point at the Link panel:** it starts at **800 bit/s (16-FSK)**. When the simulated fan switches on, frames are lost and the speed **drops to the robust speed (57 bit/s, with FEC)**. When the fan stops, it **jumps straight back to 800 bit/s**. The event log explains every change ("4 of 4 frames lost", "strong signal, jumping up").
**Say:** "Same idea as Wi-Fi rate adaptation: use the fastest speed the channel can carry right now."

### Step 4. Security (about 40 s)
```
py -m acousticnet sim-demo --scenario security --speed 2
```
**Say:** "Sound is broadcast, so anyone nearby can hear every frame. The demo shows that an unencrypted file can be read by a listener, an encrypted one cannot, and the receiver rejects both an old recording played back and data sent with the wrong key: confidentiality, integrity and freshness, the same things TLS gives a web page."

### Step 5. Routing through a relay (about 1 min)
```
py -m acousticnet sim-demo --scenario relay --speed 2
```
**Point at:** the Routing panel filling in ("node 1 via node 3, 2 hops"), **traceroute 1 → 3 → 2**, and the file arriving through the relay with SHA-256 matching.
**Say:** "Laptops A and B can't hear each other. The middle laptop learns routes with distance-vector routing, like RIP, and forwards frames hop by hop."

### Step 6. The graphs
Open `results\figures`. Show Fig. 3 (ARQ), Fig. 5 (CRC), Fig. 7 (speed ladder), Fig. 1 (ALOHA/CSMA), Fig. 9 (routing). Section 20 has the one-line message of each.

### Step 7. The real-hardware evidence
Show the screenshot of the MacBook ↔ ROG ping (Section 6), then close with the hardware story.

**Closing line:**
> "We built a complete network stack on the worst medium we could find and measured it against theory. Two findings the textbook doesn't mention: sound-card latency, not the speed of sound, is what handicaps CSMA on laptops, and 16-FSK costs no more signal than 2-FSK."

## 5. Live demo on three laptops

Use this when you demo over real air. Section 4 is the backup for any act that fails.

| | Machine | Address | Job |
|---|---|---|---|
| **Laptop 1** | ROG Strix | 1 | Sender (A) |
| **Laptop 2** | TUF | 2 | Receiver (B), on the projector |
| **Laptop 3** | MacBook Pro | 3 | Relay in the middle (Act 4), MAC sink (Act 6) |

On the ASUS laptops type `py -m acousticnet ...`; on the Mac type `python3 -m acousticnet ...`.

### Setup (every laptop, once)
1. Open a terminal in the project folder (on Windows: `cd X:\Projects\AcousticNet`). Activate your virtual environment if you use one.
2. Volume **100 %**. Turn **off** ASUS AI noise cancellation and Windows microphone "Audio enhancements". On the Mac, set Mic Mode to **Standard**.
3. Airplane mode **on** (show that Wi-Fi and Bluetooth are off).
4. Run the self-test and wait for **READY**:
```
py -m acousticnet selftest
```
(Mac: `python3 -m acousticnet selftest`)

### Rules
- Put **`--robust` on every command on every laptop.** Both ends must use the same settings.
- Start the receiver (and relay) **first**, the sender **last**.
- Press **Ctrl+C** before typing the next command on the same laptop.
- Laptops **30–50 cm apart**, speakers facing each other, nothing in between, quietest corner of the room.
- Laptop 3 runs **nothing** except in Acts 4 and 6. Its routing adverts would collide with the other transfers.

### Act 1. Ping (about 30 s)
**Laptop 2:**
```
py -m acousticnet recv --robust
```
**Laptop 1:**
```
py -m acousticnet ping --robust
```
**Expect:** 3 replies, CRC OK. If there are no replies, move the laptops closer and try once more.

### Act 2. File transfer and a clap (about 1 min)
**Laptop 2:** leave `recv --robust` running.
**Laptop 1:** Ctrl+C, then:
```
py -m acousticnet send demo_files\hello.txt --robust
```
**Do:** clap once, loudly, next to Laptop 2 while a DATA frame is playing.
**Expect:** that frame fails its CRC, only that frame is sent again, then **"SHA-256 MATCHES"**.

### Act 3. Encrypted transfer (about 1 min)
**Laptop 2:** Ctrl+C, then:
```
py -m acousticnet recv --robust --key vit-cn-lab
```
**Laptop 1:** Ctrl+C, then:
```
py -m acousticnet send demo_files\hello.txt --robust --key vit-cn-lab
```
**Expect:** "Encrypted + authenticated", SHA-256 matches.
For the listener, replay and wrong-key part, run `py -m acousticnet sim-demo --scenario security --speed 2` on Laptop 1.

### Act 4. Relay with three laptops (about 3–4 min)
**Place them in a row:** Laptop 1 — Laptop 3 — Laptop 2, about 40 cm apart.

**Laptop 2:** Ctrl+C, then:
```
py -m acousticnet recv --robust --routed --ignore 1
```
**Laptop 3 (Mac):**
```
python3 -m acousticnet relay --robust
```
**Laptop 1:** Ctrl+C. Wait about **1 minute**, until Laptop 2's Routing panel shows the route through node 3. Then:
```
py -m acousticnet traceroute --to 2 --robust --ignore 2
```
**Expect:** `1 -> 3 -> 2`. Then Ctrl+C and:
```
py -m acousticnet send demo_files\hello.txt --robust --routed --ignore 2
```
**Expect:** the file reaches Laptop 2 through Laptop 3, SHA-256 matches, and Laptop 3 counts the relayed frames.
**Say:** "`--ignore` emulates a wall. Laptops 1 and 2 act as if they can't hear each other, so every frame has to go through Laptop 3."

**After this act, press Ctrl+C on Laptop 3.**

### Act 5 (optional, quiet room only). Adaptive speed (about 2 min)
This act runs **without** `--robust` on both laptops, because `--robust` fixes the speed.
**Laptop 2:** Ctrl+C, then:
```
py -m acousticnet recv --volume 1.0
```
**Laptop 1:** Ctrl+C, then:
```
py -m acousticnet send demo_files\secret_message.txt --volume 1.0
```
**Do:** play white noise from a phone 20–30 cm from Laptop 2 for about 15 s, then stop it.
**Expect:** the speed drops during the noise and jumps back up after it stops.

### Act 6 (optional). Shared channel: ALOHA and CSMA (about 3 min)
**Laptop 1:** Ctrl+C, then:
```
py -m acousticnet mac-station --addr 1 --robust
```
**Laptop 2:** Ctrl+C, then:
```
py -m acousticnet mac-station --addr 2 --robust
```
**Laptop 3 (Mac), last:**
```
python3 -m acousticnet mac-sink --robust --stations 1,2 --protocols slotted,csma --p 0.5 --duration 60
```
**Expect:** Laptop 3 shows the throughput of each run and saves `results/measured/mac_measured.csv`. For 2 stations at this load, slotted ALOHA's theory is 0.5; in the simulated room it came out at 0.43–0.51.

### If a live act fails twice in a row
Don't keep retrying. Say: "This room is too echoey for this act; here is the same code in the simulated room." Then run its backup on Laptop 1:

| Act | Backup on Laptop 1 |
|---|---|
| 1–2 | `py -m acousticnet sim-demo --rate base` |
| 3 | `py -m acousticnet sim-demo --scenario security --speed 2` |
| 4 | `py -m acousticnet sim-demo --scenario relay --speed 2` |
| 5 | `py -m acousticnet sim-demo --scenario speed --speed 2` |
| 6 | `py -m acousticnet sim-mac` |

**Checked before the review:** in the simulated room with these exact `--robust` settings (code copied from your folder on 24 Oct), the direct, encrypted and relayed transfers all finished with SHA-256 matching, traceroute found 1 → 3 → 2, and the 2-station MAC run completed. A real room can still be worse than the simulation, which is why the backups are there.

## 6. The real-hardware story

Tell this honestly. Examiners value diagnosis more than a perfect demo.

**What worked on real laptops (23 Oct 2026):**
- **Acoustic ping between a MacBook Pro and the ROG Strix over real air:** 3 of 3 replies, CRC OK, SNR 24–26 dB, tone clarity 94–97 %, round trip 9.2 s (8.6 s airtime plus about 0.3 s latency per laptop).
- **Data frames decoded over real air** on the ROG with CRC OK at 15–24 dB SNR.
- The self-test passed on all three laptops.

**What did not work reliably:** complete file transfers in an echoey classroom.

**What we found, step by step:**
1. **Audio latency is about 400 ms on Windows** (self-test measured 391 and 416 ms), almost 3× what our simulator assumed. Every round trip is slower.
2. **The return path was the weak one.** Data frames reached the receiver, but its ACKs came back garbled. Gaming-laptop speakers point down and to the sides, so the receiver was quieter towards the sender than the sender was towards it. Raising the receiver's volume to 100 % fixed ping.
3. **Long frames lose sync.** At 50 baud with FEC a data frame lasts 10–12 s; one cough or a small clock drift during that time corrupts it beyond repair, and each retry round takes a minute.
4. **The fix we added: `--robust`.** One flag on both laptops: 100 baud, Hamming FEC on every frame (headers and ACKs too), full volume, short frames (8-byte chunks, about 2.7 s per frame). In a simulated room with the real 0.4 s latency it delivers the file byte-identical.

**One sentence for the viva:** "The single-link physical layer works on real hardware; reliability in an echoey room was limited by latency, a weak return path and long frames, and we built a robust mode with short FEC-protected frames to address it."

## 7. If something goes wrong

| Problem | Do this |
|---|---|
| A live act fails twice | Run its backup from the table in Section 5 |
| A demo shows an error | Skip it and open its screenshots in `results\demo_run\screenshots` |
| The laptop itself fails | Present from the screenshots and `results\demo_run\SUMMARY.md` on any computer |
| Asked "show me it works" | Run any `sim-demo` again: the same code gives the same behaviour every time |
| Dashboard doesn't open | Open `http://127.0.0.1:8765` in the browser; the terminal also prints every event |

The saved run (`results\demo_run\`) contains the test output (75/75), a log for each demo, the files that arrived, and 11 dashboard screenshots.

---

# Part C. The concepts, layer by layer

## 8. The stack at a glance

| OSI layer | What AcousticNet does | File |
|---|---|---|
| Application | File transfer (META + chunks + SHA-256), ping | `transfer.py` |
| Security | Encryption, authentication, replay protection | `crypto.py` |
| Transport-like | Stop-and-Wait, Go-Back-N, Selective Repeat | `arq.py` |
| Network | Distance-vector routing, TTL, traceroute | `network.py` |
| Data link (MAC) | ALOHA, Slotted ALOHA, CSMA; adaptive speed | `mac.py`, `rate.py` |
| Data link (LLC) | Frame format, CRC-8, CRC-16, Hamming, interleaving | `frame.py`, `coding.py` |
| Physical | 2/4/16-FSK modem, chirp sync, streaming receiver | `phy.py`, `receiver.py` |
| Hardware | Sound card, or the simulated room | `audio.py`, `channel.py` |

The glue is **`link.py` (Node)**: one laptop. It offers the upper layers four functions: `send(frame)`, `recv(timeout)`, `channel_busy()` and `now()`. Upper layers never deal with sound directly. **`network.py` (NetLink)** offers the same four functions across several hops, so ARQ and file transfer run unchanged through a relay.

## 9. Physical layer: bits to sound (`phy.py`)

### FSK: frequency shift keying
Each bit becomes a short tone: **1400 Hz means 0, 2600 Hz means 1**. At 100 baud each tone lasts **10 ms**. The receiver cuts the recording into 10 ms slices and asks, for each slice, "is there more 1400 Hz energy or more 2600 Hz energy?"

**Analogy:** two singers, one low and one high. To read a bit you only need to know which singer is louder, not how loud either one is.

**Why not ASK or PSK?**
- **ASK** (amplitude) needs the exact loudness, which changes with distance, volume and the microphone's automatic gain.
- **PSK** (phase) needs the exact phase, which echo scrambles.
- **FSK** only compares two energies, so it survives all of that.

### Why exactly 1400 and 2600 Hz
1. **Whole cycles per symbol, so the tones are orthogonal.** 1400/100 = 14 cycles and 2600/100 = 26 cycles fit exactly into one 10 ms symbol. Measuring 1400 Hz inside a perfect 2600 Hz symbol then gives exactly zero, so the tones never leak into each other.
2. **Not harmonics of each other.** An earlier version used 1200/2400 Hz, but 2400 is the second harmonic of 1200, so a distorting speaker playing a 0 would leak into the 1. 2600/1400 is not a whole number.
3. **Valid at every speed.** 1400 and 2600 are multiples of 25, 50, 100 and 200, so all four baud rates keep whole cycles. `config.py` rejects any setting that breaks this rule.
4. **Laptop sweet spot.** Small speakers are weak below about 500 Hz and microphones roll off above about 8 kHz; 1–3 kHz is loud and clear on nearly every laptop.

### How a tone is generated
```python
freq  = np.repeat(np.where(bits == 1, 2600.0, 1400.0), samples_per_symbol)
phase = 2 * np.pi * np.cumsum(freq) / 48000
sound = np.sin(phase)
```
**Continuous phase:** the phase is accumulated (`cumsum`), so there is no sudden jump between symbols. Jumps would cause clicks and spread energy into other frequencies.

### Sampling
48,000 samples per second, the native rate of laptop audio. The highest tone is 3.8 kHz, far below the Nyquist limit of 24 kHz. At 100 baud one symbol is 480 samples.

### The preamble: how a frame is found
```
[preroll 50 ms][ chirp 100 ms, 900→3100 Hz ][guard 20 ms][ SYNC | HEADER | PAYLOAD | CRC ]
```
- **Preroll:** a soft tone that wakes the microphone's noise gate and auto-gain, so the important part isn't clipped.
- **Chirp:** a "wheeeep" rising from 900 to 3100 Hz. The receiver slides a copy along the recording (a **matched filter**, using normalised cross-correlation). Where a chirp starts, the correlation peaks sharply, even in noise. A frame is tried when the peak exceeds 0.30. **Analogy:** the conductor's baton tap before the orchestra starts.
- **Guard:** two silent symbols so the chirp's echo dies away before the data.
- **Symbol timing:** the receiver tries 17 slightly different start points and keeps the one where the 16-bit sync word decodes with the fewest errors and the cleanest tones (up to 2 sync-bit errors tolerated).

### Five speeds: M-ary FSK
With **M** tones, each tone carries **log₂M** bits.

| MCS | Name | Tones | Tone length | Bits per tone | Speed | Needs (header SNR) |
|---|---|---|---|---|---|---|
| 0 | Robust | 2 + Hamming FEC | 10 ms | 1 × 4/7 | **57 bit/s** | 12.0 dB |
| 1 | Base | 2 (1400/2600 Hz) | 10 ms | 1 | **100 bit/s** | 14.5 dB |
| 2 | Fast | 4 (1100…2900 Hz, 600 Hz apart) | 10 ms | 2 | **200 bit/s** | 14.5 dB |
| 3 | Faster | 16 (1100…2600 Hz, 100 Hz apart) | 10 ms | 4 | **400 bit/s** | 14.5 dB |
| 4 | Fastest | 16 (800…3800 Hz, 200 Hz apart) | 5 ms | 4 | **800 bit/s** | 16.0 dB |

**Bit rate = baud × log₂M × code rate.** Example: MCS 4 = 200 × 4 × 1 = 800 bit/s; MCS 0 = 100 × 1 × 4/7 = 57 bit/s.

**Our finding: 16-FSK needs no more signal than 2-FSK at the same tone length.** Every tone has the same energy. A 16-FSK tone carries 4 bits, so each bit gets 4× the energy, which outweighs having 16 tones to tell apart. The price is bandwidth (16 tones instead of 2), not power. Halving the tone length (MCS 4) halves the energy per tone and costs about 3 dB. This is the textbook statement "M-ary FSK trades bandwidth for power efficiency", measured.

### SNR and tone clarity
- **SNR** in AcousticNet = loudest tone's energy vs the median of the other tones, measured on the header, in dB.
- **Tone clarity** = how much the winning tone beats the runner-up, averaged over the payload: (top − second)/(top + second). A clean link shows about 95–99 %; at 63 % roughly a quarter of the energy lands in the wrong tone.

### Bandwidth
About 1.2 kHz for 2-FSK (1400 to 2600 Hz plus a margin), up to about 3 kHz for MCS 4 (800 to 3800 Hz). The receiver band-passes 700–4000 Hz.

## 10. The streaming receiver (`receiver.py`)

The microphone never stops. Its samples go into a **ring buffer** holding the last 90 s, and each sample has an **absolute index** (its number since start). A receiver thread walks forward:
1. **Search** for a chirp in new audio only.
2. **Decode** the sync word, header and body.
3. **Hand** the frame to the node.

If a frame has only half arrived, it waits and continues later. It processes only new audio and runs about **150× faster than real time**.

**The sample counter is the clock:** `now() = samples recorded / 48000`. Every timer uses it. That is why the same code runs at real speed on laptops and faster in the simulator.

**Honest timeouts:** a timeout is declared only after the receiver thread has actually examined all audio up to the deadline. A slow laptop gets a slower transfer, never a wrong one.

## 11. Framing (`frame.py`)

```
SYNC (16 bits) | TYPE+FLAGS | SRC | DST | SEQ | LEN | MCS | CRC-8 | PAYLOAD (0..255 bytes) | CRC-16
               └──────── 7-byte header, always at base speed ────┘└── at the speed MCS says ──┘
```

| Field | Why it exists |
|---|---|
| **SYNC** `0xB3A5` or `0x4C5A` | Marks the start of data and says whether the header is FEC-coded. The two words are bitwise opposites, so they can't be confused |
| **TYPE / FLAGS** | DATA, ACK, PING, PONG, CTRL, MAC, BER, MSG, ROUTE, TTLX, ALERT. Flags: POLL ("answer now"), SR (Selective Repeat), NET (routed frame) |
| **SRC / DST** | 8-bit addresses; 255 = broadcast |
| **SEQ** | Sequence number 0–255, wraps around |
| **LEN** | Payload length, so the receiver knows how long to listen |
| **MCS** | The payload's speed. The header goes at base speed so everyone can read it, then the receiver switches. **Wi-Fi's PLCP header does exactly this** with its SIGNAL field |
| **CRC-8** | Protects the header. If LEN or MCS were wrong the receiver would wait for the wrong number of symbols. IPv4 has a header checksum for the same reason |
| **CRC-16** | Covers header + payload, so a body can't be glued to the wrong header |

## 12. Error detection and correction (`coding.py`)

### CRC-16/CCITT
Polynomial **0x1021** (x¹⁶ + x¹² + x⁵ + 1), initial value 0xFFFF. The frame is treated as a long binary number and divided by the polynomial; the remainder is the CRC. The receiver repeats the division and compares.

**Guarantees:** a CRC with r check bits detects **every burst of length ≤ r** (here 16), every single-bit error and every odd number of bit errors. Random longer errors slip through with probability about 2⁻¹⁶.

**Measured (Fig. 5):** out of 360,000 corrupted messages, CRC-16 missed **2**, and none of the bursts up to 16 bits. The Internet checksum (used by IP/TCP/UDP) missed **3 %** of 2-bit errors. Single parity misses every even number of flipped bits.

**Analogy:** CRC is a seal on an envelope. It tells you the letter was tampered with but can't repair it.

### Hamming(7,4)
Every 4 data bits get 3 parity bits (rate 4/7). Each parity bit checks an overlapping group of positions (1,3,5,7 / 2,3,6,7 / 4,5,6,7). On receive, the 3 checks form a **syndrome**, and **the syndrome is the position of the wrong bit** (0 means no error). Flip it and the error is fixed. It corrects one error per 7-bit block.

**Analogy:** a spelling checker that fixes one typo per word.

### Interleaving
A clap destroys many consecutive bits, but Hamming fixes only one per block. So codewords are written as rows and **sent column by column**. A burst then hits each codeword at most once, and Hamming fixes them all.

**Analogy:** write your letter across many pages, so a coffee stain smudges only one letter per word.

### Why SHA-256 as well?
CRC protects each frame; SHA-256 checks the **whole file end to end**, after reassembly. It catches anything the per-frame checks can't (a chunk placed in the wrong position, a bug in reassembly). This is the end-to-end argument: final correctness is checked at the ends.

## 13. Reliability: ARQ (`arq.py`)

ARQ = Automatic Repeat reQuest: number the frames, acknowledge what arrived, resend what didn't.

| Protocol | Sender | Receiver | One frame lost, so… |
|---|---|---|---|
| **Stop-and-Wait** | 1 frame, then wait | Accepts in order | Resends that frame |
| **Go-Back-N** | Window of W frames | Accepts **only in order** | Resends that frame **and all after it** |
| **Selective Repeat** | Window of W frames | **Buffers** out-of-order frames | Resends **only** that frame |

Stop-and-Wait is Go-Back-N with W = 1, and the code uses exactly that.

### The acoustic twist: half-duplex bursts
A laptop's own speaker drowns its microphone, so the receiver can't ACK each frame while the sender talks. The sender sends a **burst**, and the last frame carries **POLL** ("your turn"):
```
sender:   [DATA 0][DATA 1][DATA 2][DATA 3 + POLL]
receiver:                                        [ACK: have 0,1,3; missing 2 + SNR]
sender:   [DATA 2][DATA 4] ...
```
This is how **Wi-Fi 802.11n Block-ACK** works, and how HDLC uses its P/F bit.

### ACK contents
- **Selective Repeat:** `[next expected][32-bit bitmap of later frames received][SNR × 4]` = 6 bytes.
- **Go-Back-N / Stop-and-Wait:** `[next expected][SNR × 4]` = 2 bytes (cumulative ACK).
- Every ACK carries the SNR at which the burst was heard, for rate control.

### Window and sequence-number limits
Sequence numbers are 8 bits (0–255) and wrap. With m-bit numbers:
- **Selective Repeat:** W ≤ 2^(m−1) = **128**, otherwise a new frame 5 and an old frame 5 look identical. We use ≤ 32 because the bitmap is 32 bits.
- **Go-Back-N:** W ≤ 2^m − 1 = **255**.

### Lost-ACK handling
- **Status poll:** if no ACK arrives in time, the sender sends a tiny "what do you have?" frame (HDLC's RR,P) instead of resending everything. Only what's really missing is resent.
- **Linger:** after receiving everything, the receiver keeps answering for **20 s**, in case its last ACK was lost. TCP's **TIME_WAIT** exists for the same reason (the two-generals problem: the last message can never be confirmed).
- **Give-up rule:** only *silent* rounds count; after **25** silent timeouts the sender gives up. "The receiver answered but the frame is still missing" is a different situation and doesn't count.
- **ALERT:** if the receiver refuses a transfer (for example, wrong key), it answers every poll with a 1-byte reason code, like a TLS alert, so the sender stops at once instead of retrying for minutes.

### ACK timeout
`timeout = 2 × audio latency + ACK airtime + 1 s margin`.

### Stop-and-Wait efficiency
Efficiency = T_frame / (T_frame + T_ack + 2 × latency + processing). With 3.6 s frames, a 1.2 s ACK and two sound-card delays, about half the time is waiting: **46 bit/s out of 100**.

### Results (Fig. 3, base speed, 1280-byte file)

| Frames lost | Stop-and-Wait | GBN W=4 | SR W=4 | GBN W=8 | SR W=8 |
|---|---|---|---|---|---|
| 0 % | 46 | 61 | 60 | 65 | 64 |
| 20 % | 28 | 36 | 40 | 29 | **45** |
| 40 % | 11 | 13 | 21 | 9 | **25** |

Goodput in bit/s. At 20 % loss, GBN W=8 resends **50** frames for a 40-chunk file; SR W=8 resends **11**. **Bigger windows help Selective Repeat but hurt Go-Back-N under loss**, because GBN throws away everything after a loss.

**Frame size has a sweet spot:** 64 bytes at a bit error rate of 10⁻³, 16 bytes at 3 × 10⁻³. Small frames waste time on preamble and header; big frames are hit by errors more often.

## 14. Adaptive speed (`rate.py`)

**Goal:** use the fastest speed the channel can carry right now; fall back when it gets worse. Same as Wi-Fi rate adaptation.

**Analogy:** driving on an unknown road. Speed up a little after a few smooth kilometres, slow down at the first pothole, brake hard when you see thick fog.

1. **Link probe.** Before a transfer, PING/PONG. The PONG carries the SNR at which the PING was heard; the sender measures the PONG. The worse direction picks the **starting speed** and the **chunk size** (32 bytes at slow speeds up to 96 bytes at MCS 4, so the fixed preamble and header don't dominate: Wi-Fi's frame aggregation idea).
2. **After every burst** the ACK reports SNR and losses:

| Situation | Decision | Wi-Fi name |
|---|---|---|
| More than 1 frame in 4 lost | One speed **down** | Auto Rate Fallback (ARF) |
| Reported SNR below what this speed needs | Jump **down** to a safe speed | SNR-based selection |
| Two clean bursts and SNR allows one more speed | One speed **up** (probing) | ARF |
| A clean burst and SNR allows 2+ steps up | Jump **up** straight to it | — |
| No answer | One speed down; a second silence in a row: straight to the **most robust** | — |

3. **Short bursts at slow speeds.** Burst airtime is capped at about 12.5 s so the speed can be re-decided quickly.

**Why not always use the fastest speed?** It needs a cleaner signal. In noise its frames are lost, and every loss costs a whole retransmission round.

**Results:** adaptive is **4.8× faster** than base speed on a clean channel (290 vs 61 bit/s), and best or within 15 % of the best at every SNR (Fig. 7). With a fan switching on and off, adaptive finishes a 6 KB file in **213 s**, **22 % sooner** than the best fixed speed (273 s) (Fig. 8).

## 15. Medium access: ALOHA and CSMA (`mac.py`, `theory.py`)

**Setup:** one **sink** laptop (base station) and N **station** laptops share one acoustic channel.
1. The sink broadcasts a **START** frame (protocol, load p, duration).
2. Each station sends short 4-byte frames for 90 s:
   - **Pure ALOHA:** every 1/8 frame-time, transmit with probability p/8.
   - **Slotted ALOHA:** at each slot boundary (slot = frame + 250 ms guard), transmit with probability p.
   - **Non-persistent CSMA:** like ALOHA, but listen first; if busy, wait a random 0.5–1.5 frame-times and listen again.
3. The sink counts correctly decoded frames; overlapping frames come out garbled.
4. The sink **polls** each station for how many frames it tried, a third MAC style (controlled access).
5. One CSV row per run: offered load **G**, throughput **S**.

### Why 3-station theory, not the textbook
The textbook peaks (18.4 % pure, 36.8 % slotted) assume **infinitely many** stations. With N stations, a frame survives if each of the other N − 1 stays quiet:
- **Slotted:** S = G(1 − G/N)^(N−1) → peak **44.4 %** for N = 3 (at G = 1).
- **Pure:** S = G(1 − G/N)^(2(N−1)) → peak **24.6 %** for N = 3 (at G = 0.6).

Comparing three laptops with the infinite-station numbers would make them appear to "beat theory". They don't; the yardstick was wrong.

**Why pure ALOHA is half as good:** its vulnerable period is 2 frame times (a frame collides with anything starting up to one frame before or after it); slotting cuts it to 1.

### Aligning slots without a shared clock: timing advance
Every station hears the START frame end at the same moment. Each laptop has a different audio latency (50–300 ms), measured by `selftest`. To make its sound hit the air exactly at a slot boundary, each station schedules its samples **round-trip latency early**. GSM phones do the same and call it **timing advance**.

### Our finding: CSMA is limited by the sound card
CSMA works only if a station hears that someone else started *before* it starts too. The blind window is the **sensing delay** a (as a fraction of frame time) in Kleinrock and Tobagi's formula:

S = G·e^(−aG) / (G(1 + 2a) + e^(−aG))

For radio, a is tiny. For laptops, the speed of sound is not the problem (2 m = 6 ms). The **sound card** is: a station hears audio that is already 50–150 ms old, and its own sound leaves 50–150 ms after it decides. So a ≈ (round-trip latency + 30 ms) / frame time ≈ **0.19**.

**Results:** the same 3 laptops would reach **0.69** with zero audio latency but only **0.49** with realistic latency (Fig. 1). Best CSMA falls from **0.69 (0 ms) to 0.45 (300 ms) to 0.31 (600 ms)** (Fig. 2). At laptop latency, CSMA is barely better than slotted ALOHA's 0.444.

### Why not CSMA/CD?
A laptop can't hear others while its own speaker plays, so it can't detect a collision while transmitting. That is the same reason Wi-Fi uses CSMA/CA, not CSMA/CD.

## 16. Network layer: routing through a relay (`network.py`)

**The problem:** laptops A and B are too far apart (or behind a wall) to hear each other, but both hear R in the middle. R must relay: `A ))) R ))) B`.

### Distance-vector routing (Bellman–Ford, as in RIP)
Every laptop keeps a table *destination → (next hop, cost in hops)* and shares it with neighbours.
1. **Neighbour discovery:** any frame decoded cleanly (SNR ≥ 6 dB) proves "I can hear X".
2. **Distance vectors:** each router broadcasts its table in a ROUTE frame every 20 s (randomised). A neighbour offering destination D at cost c gives us D at cost c + 1 via that neighbour (Bellman–Ford update).
3. **Poison reverse:** if my best route to D goes *through you*, I tell you my cost to D is infinity (**16**, as in RIP), so you never route D back through me.
4. **Timeouts and triggered updates:** a neighbour not heard for 4 periods (80 s) is dead; routes through it become 16 and an immediate update is sent.
5. **Jitter and quiet-when-busy:** advert times are randomised. Two laptops that can't hear each other (a **hidden-terminal** pair) would otherwise answer the same advert at the same moment, collide at the relay, and keep colliding because their timers stay in step (Floyd & Jacobson, 1993). While carrying data, a laptop postpones adverts.

**Analogy:** asking for directions without a map. Each shopkeeper only knows how far the station is from the shops next door, and adds one block. After a few rounds of gossip, everyone knows the shortest way.

### Forwarding, TTL and traceroute
- A routed frame carries a 3-byte **network header** `[net_src, net_dst, TTL]` inside its payload (flag NET). The link header names only the **next hop**.
- The relay waits until the room is quiet (half-duplex) and resends the burst: **store-and-forward**.
- The relay decrements TTL. At 0 it drops the frame and sends **TTLX** ("time exceeded") back, exactly like ICMP.
- **Traceroute** sends pings with TTL = 1, 2, 3…; whoever answers with TTLX reveals a hop. Result: `1 → 3 → 2`.

### Count-to-infinity (Fig. 9)
When a destination dies, two routers can keep learning the stale route from each other, one hop longer each time, until the cost reaches infinity (16).
- **Line A–R–B, B dies:** without poison reverse, **14 rounds** to reach 16; with poison reverse, **1 round**.
- **Triangle (a loop of 3):** poison reverse still counts to infinity in **40 of 50** random runs (11.4 rounds on average). With a 20 s advert period, 14 rounds is almost **5 minutes** of frames sent to a dead laptop.
- **Why:** poison reverse only stops a route bouncing straight back to whoever sent it. In a triangle, A learns it from C, which learned it from B, which learned it from A. Real protocols add hold-down timers or use link-state routing.
- **Convergence:** a line of N laptops needs exactly N − 1 advert rounds, because news travels one hop per round.

**Relaying at least halves throughput:** every frame crosses the same shared, half-duplex channel twice (A → R, then R → B).

## 17. Security (`crypto.py`)

**The threat:** sound is broadcast. Any laptop in the room can record every frame, including frames addressed to someone else (promiscuous mode, like Wireshark on Wi-Fi). Addresses are not authenticated, so a recording can be replayed claiming any address.

**The defence, like TLS but small, at the application layer** (so it stays end-to-end even through a relay):

| Step | How | Protects |
|---|---|---|
| Key derivation | Passphrase → **PBKDF2-HMAC-SHA256** (20,000 rounds) → master key → separate encryption and authentication keys | Makes passphrase guessing slow; never reuses one key for two jobs |
| Encryption | Keystream = HMAC-SHA256(enc_key, nonce ‖ block counter), XOR with the message: a pseudo-random function in **counter mode**, i.e. a stream cipher | **Confidentiality** |
| Authentication | Tag = first 6 bytes of HMAC-SHA256(auth_key, nonce ‖ ciphertext): **encrypt-then-MAC** | **Integrity**: a modified message or wrong key fails the check |
| Freshness | Nonce = random 4-byte **session id** ‖ 4-byte message number; the receiver remembers completed sessions | **Replay** protection |

**Analogy:** encryption is a locked box (nobody can read the letter); the tag is a wax seal (nobody can swap the letter without breaking it); the session id is the date on the letter (an old letter re-delivered tomorrow is spotted).

**Cost on air:** 6 bytes per message (19 % of a 32-byte chunk, 6 % of a 96-byte chunk), plus a 4-byte session id once per file.

**Why encrypt-then-MAC:** the receiver checks the tag before decrypting anything, so forged or damaged data is discarded without ever being processed. It is the generally recommended order.

**Why HMAC instead of AES:** only Python's standard library is used, which has no AES. HMAC is a pseudo-random function, and a PRF in counter mode is a standard stream-cipher construction. In production you'd use AES-GCM or ChaCha20-Poly1305.

**Why only a 6-byte tag:** airtime (each byte costs 80 ms at 100 bit/s). A 48-bit tag still gives a forger a 1 in 2⁴⁸ (about 1 in 280 trillion) chance per try, and every try takes seconds on air.

**What is not protected (say this honestly):** headers and addresses (a listener sees who talks to whom), the ACKs (someone could disrupt a transfer, which is denial of service, not data theft), and jamming. No forward secrecy: one passphrase protects every session, and it must be shared in person. A key exchange (Diffie–Hellman) would be the next step.

## 18. Application layer (`transfer.py`)

### File transfer
- **Message 0 = META:** file name, size, number of chunks, first 8 bytes of SHA-256.
- **Messages 1…n = chunks** (32 bytes by default at base speed).
- The receiver reassembles them and reports **"SHA-256 matches"** only if every bit is right.
- The received name is sanitised, so a sender can't write outside the `received` folder (no `../../` tricks).

### Acoustic ping
PING, then PONG. The round-trip time is split into **airtime** (PING 1.37 s + PONG 1.45 s at base speed) plus **latency and processing**, which is where the sound-card delay shows up. The PONG also carries the SNR at which the PING was heard.

## 19. How the results were produced

| Simulator | What is simulated | What is real | Used for |
|---|---|---|---|
| **SimAir** (virtual room, `audio.py`) | Sound cards, distance, walls, echo, noise, latency | Everything else: modem, receiver, ARQ, rate control, routing, crypto, MAC, dashboard | `sim-demo`, end-to-end tests, emulated MAC results |
| **VirtualNet** (`virtual.py`) | The physical layer (frames lost with a chosen probability) | ARQ, rate control, file transfer, crypto | Thousands of ARQ and rate-control runs in seconds |
| **Waveform channel** (`channel.py`) | Noise and echo applied to real waveforms | Modem and receiver | Frame success vs SNR and echo |
| **Event simulator** (`experiments/mac_sim.py`) | Everything, as time intervals | Same decision rules as `mac.py` | Smooth MAC curves, latency sweep |
| **Round-based routing** (`experiments/routing_sim.py`) | The radio part | The very `DVTable` class the router uses | Convergence, count-to-infinity |

**Key point for the viva:** SimAir drives the **same audio callback a real sound card would**, so the simulator tests the real code path, not a copy. All simulations use fixed random seeds, so anyone who runs `python -m acousticnet simulate` gets the same numbers.

**Self-checks that the simulators are right:** the event simulator with 50 stations and zero latency lands on the textbook ALOHA curves; with 10 stations it lands on the Kleinrock–Tobagi CSMA curve.

---

# Part D. Results

## 20. Figure by figure

| Fig. | Shows | Headline |
|---|---|---|
| **1** | ALOHA / Slotted ALOHA / CSMA, 3 stations | Pure 0.23 (theory 0.246); slotted **0.447** (theory 0.444); CSMA 0.69 with no latency vs **0.49** with laptop latency |
| **2** | CSMA vs audio latency | 0.69 at 0 ms → 0.45 at 300 ms → 0.31 at 600 ms; sound-card latency is CSMA's handicap |
| **3** | ARQ goodput vs loss; frame-size sweet spot | SR W=8 best under loss; GBN W=8 resends 50 vs SR 11 at 20 % loss |
| **4** | Physical layer vs noise and echo | Each halving of bit rate buys about 3 dB; FEC about 3 dB more. Strong echo: 50 baud delivers 63 % of frames, 200 baud 37 %; FEC lifts them to 90 % / 63 % |
| **5** | Parity vs checksum vs CRC-8 vs CRC-16 | CRC-16 missed 2 of 360,000; none of the bursts ≤ 16 bits |
| **6** | BER vs distance on real laptops | Created when the `ber-rx`/`ber-tx` hardware test is run |
| **7** | Speed ladder and adaptive goodput | MCS 1–3 need the same SNR; MCS 4 needs about 3 dB more; adaptive 4.8× base on a clean channel |
| **8** | A fan switching on and off | Adaptive 213 s vs best fixed 273 s: 22 % sooner |
| **9** | Routing | Line converges in N − 1 rounds; poison reverse fixes 2-node loops (1 round vs 14) but not triangles (40/50 still count up) |

**Figures 1–5 and 7–9 come from simulation of the real code.** Say so clearly. Figure 6 needs hardware measurements.

## 21. What happened on real laptops

See Section 6. The short version: **ping and single frames worked over real air (SNR 24–26 dB); full transfers were unreliable in an echoey room because of 400 ms Windows latency, a weak return path and 10–12 s frames; we diagnosed each cause and added `--robust` (short FEC-protected frames, full volume).**

---

# Part E. Viva questions and answers

## 22. Questions by topic

### General and novelty

**Q. Why sound?**
It's a real, shared, noisy, half-duplex medium we could build every layer for using only laptops, and every syllabus problem becomes audible and visible.

**Q. Isn't 100 bit/s useless?**
For pairing tokens, payments and air-gapped links it's enough; up to 800 bit/s is possible in a quiet room. The point is the protocol stack and the measurements against theory.

**Q. Is this used in the real world?**
Yes: Google Pay's earlier "Audio QR" used near-inaudible sound to pair phones; Chromecast guest mode used sound tokens; air-gapped data leakage through speakers is an active security research area.

**Q. How is this different from ggwave or minimodem?**
Those are modems only. We wrote our own modem and built the data-link, MAC, network and security layers on top, plus theory comparisons. We used no data-over-sound library.

**Q. What did you use libraries for?**
NumPy for FFT and array maths, sounddevice for raw audio input/output, Matplotlib for graphs, pytest for tests. Every protocol (modem, framing, CRC, Hamming, ARQ, rate control, MAC, routing, crypto construction, dashboard) is our own code.

**Q. What is your own finding, beyond the textbook?**
Two: sound-card latency, not the speed of sound, handicaps CSMA on laptops; and 16-FSK needs no more SNR than 2-FSK at the same tone length.

### Physical layer

**Q. Why FSK and not ASK or PSK?**
ASK needs exact loudness (changes with distance and auto-gain); PSK needs exact phase (echo scrambles it). FSK only compares which tone is louder.

**Q. Why 1400 and 2600 Hz?**
Whole cycles per symbol (orthogonal), not harmonics of each other, valid at 25–200 baud, and in the laptop speaker's sweet spot.

**Q. What does orthogonal mean here?**
Over one symbol, the correlation of one tone with the other is exactly zero, because both complete whole cycles. So energy at one tone never shows up as energy at the other.

**Q. What is the chirp for?**
Detecting a frame and timing it. Its sharp correlation peak (matched filter) marks exactly where the frame starts, even in noise.

**Q. Why sample at 48 kHz?**
It's the native rate of laptop audio; our highest tone is 3.8 kHz, far below Nyquist (24 kHz).

**Q. Baud vs bit rate vs goodput?**
Baud = symbols per second. Bit rate = baud × bits per symbol × code rate. Goodput = useful file bits per second after headers, ACKs, waiting and retransmissions (46–65 bit/s at 100 bit/s base speed).

**Q. Why does 16-FSK not need more SNR than 2-FSK?**
Same tone length means the same energy per tone, but each tone carries 4 bits, so energy per bit is 4×. That outweighs choosing among 16 tones. The cost is bandwidth.

**Q. How does the receiver know the payload speed?**
The 7-byte header always goes at base speed and contains the MCS byte, protected by CRC-8. After the header, the receiver switches speed, like Wi-Fi's PLCP SIGNAL field.

**Q. How do you measure SNR?**
On the base-speed header: the energy of the winning tone vs the median energy of the other tones, in dB.

### Data link

**Q. Why does the header need its own CRC?**
LEN and MCS decide how many symbols to wait for and how to read them; a wrong value would waste time or misread the body. Same reason as IPv4's header checksum.

**Q. CRC vs checksum?**
CRC-16 catches every burst up to 16 bits; the Internet checksum missed 3 % of 2-bit errors in our test (Fig. 5). Sound errors come in bursts, so CRC is the right choice.

**Q. How does Hamming find the error?**
Three parity checks over overlapping bit groups; the 3-bit syndrome equals the position of the wrong bit.

**Q. Why interleave?**
A clap causes a burst of consecutive errors; interleaving spreads it so each Hamming codeword gets at most one error, which it can fix.

**Q. Why both CRC and SHA-256?**
CRC checks each frame; SHA-256 checks the whole reassembled file end to end.

### ARQ

**Q. Why burst + POLL instead of an ACK per frame?**
Half-duplex: a laptop can't hear while its speaker plays. Same idea as 802.11n Block-ACK and HDLC's P/F bit.

**Q. Why is Selective Repeat's window limited?**
With 8-bit sequence numbers, W ≤ 128, otherwise a new and an old frame with the same number look identical. We use ≤ 32 because of the 32-bit bitmap.

**Q. Why does Go-Back-N get worse with a bigger window under loss?**
One loss makes it resend the whole rest of the window; bigger windows mean more wasted resends (50 vs 11 for SR at 20 % loss).

**Q. What if the last ACK is lost?**
The sender sends a status poll; the receiver lingers 20 s to answer it, like TCP TIME_WAIT. This is the two-generals problem: the last message can't itself be confirmed.

**Q. What if a lot of ACKs are lost?**
The sender asks "what do you have?" rather than resending blindly; only silent rounds count towards giving up (25).

**Q. Why is Stop-and-Wait slow?**
It waits for an ACK after every frame. With 3.6 s frames, a 1.2 s ACK and two sound-card delays, about half the time is idle: 46 bit/s of 100.

**Q. Why do sequence numbers wrap?**
They're 8 bits to save airtime; the receiver maps a wrapped number back to the nearest absolute index within the window.

### Adaptive speed

**Q. Why not always use the fastest speed?**
It needs a cleaner signal; in noise its frames are lost and every loss costs a retransmission round. Fixed MCS 4 fails completely at −3 dB.

**Q. How does the sender know the channel quality?**
The link probe before the transfer, then the SNR in every ACK plus the losses it sees.

**Q. Compare with Wi-Fi.**
Wi-Fi's ARF steps down after failures and up after successes; SNR-based schemes (RBAR) use the receiver's SNR; Linux's Minstrel samples rates statistically. Ours combines ARF with SNR-guided jumps.

**Q. Why bigger chunks at high speed?**
Preamble and header always go at base speed (about 0.9 s per frame). At 800 bit/s a 32-byte payload takes only 0.3 s, so overhead would dominate; bigger payloads amortise it (Wi-Fi frame aggregation).

### MAC

**Q. Why aren't your ALOHA peaks 18.4 % and 36.8 %?**
Those assume infinitely many stations. For N = 3: 24.6 % and 44.4 %.

**Q. What is "a" in CSMA and why does it matter?**
Sensing delay ÷ frame time. On laptops it's dominated by sound-card latency (about 0.19), so CSMA loses most of its advantage over slotted ALOHA.

**Q. How do slots line up without a shared clock?**
Everyone hears the START frame end at the same time; each laptop schedules its sound early by its measured round-trip latency (GSM timing advance).

**Q. Why not CSMA/CD?**
A laptop can't hear others while its own speaker plays, so it can't detect collisions while sending. Wi-Fi uses CSMA/CA for the same reason.

**Q. What is the polling part?**
After each run the sink asks each station for its counts over sound: controlled access, a third MAC style.

### Network layer

**Q. How does distance-vector routing work?**
Each router tells neighbours its cost to every destination; a neighbour adds one hop and keeps the cheapest (Bellman–Ford). A line of N routers converges in N − 1 rounds.

**Q. What is count-to-infinity?**
After a destination dies, routers keep learning the stale route from each other, one hop longer each time, until 16. Fig. 9: 14 rounds without poison reverse.

**Q. What does poison reverse fix, and what can't it fix?**
It stops a route bouncing straight back (2-router loops: 1 round instead of 14). It can't stop loops of 3: our triangle still counts up in 40 of 50 runs.

**Q. How would you fix that?**
Hold-down timers, split horizon variants, or switch to link-state routing (each router knows the full topology, like OSPF).

**Q. What is a hidden terminal?**
A and B can't hear each other but both reach R; if they send together they collide at R and neither notices. We reduce it with jittered adverts and quiet-when-busy; RTS/CTS would be the next step.

**Q. How does traceroute work?**
Pings with TTL = 1, 2, 3…; the relay where TTL hits 0 drops the ping and sends "time exceeded" back, revealing itself.

**Q. Why does relaying at least halve throughput?**
Every frame crosses the same shared half-duplex channel twice, plus a wait for quiet before forwarding.

### Security

**Q. Why encrypt at the application layer, not per link?**
So it stays end-to-end: the relay forwards data it can't read, as TLS protects a web page across many routers.

**Q. Why encrypt-then-MAC?**
The tag is checked before anything is decrypted, so forged or damaged data is discarded unprocessed.

**Q. How is a replay detected?**
Every transfer gets a random 4-byte session id, part of every nonce and covered by the tag. The receiver remembers completed sessions and refuses a repeat.

**Q. Why HMAC-SHA256 as the cipher?**
Standard library only (no AES). HMAC is a PRF; a PRF in counter mode is a standard stream cipher. Production: AES-GCM or ChaCha20-Poly1305.

**Q. Why PBKDF2 with 20,000 rounds?**
To make each passphrase guess slow, so guessing the passphrase is expensive.

**Q. What isn't protected?**
Headers/addresses, ACKs (denial of service possible, not data theft), jamming; no forward secrecy; the passphrase must be shared in person.

### Implementation and hardware

**Q. Did it work on real laptops?**
Ping and single frames, yes: 3/3 replies at 24–26 dB SNR between a MacBook and the ROG. Full file transfers were unreliable in an echoey classroom. We found three causes (400 ms Windows latency, a weak return path, 10–12 s frames losing sync) and added a robust mode with short FEC-protected frames.

**Q. Is the simulation just faked?**
No. The simulator replaces only the sound card; the same modem, receiver, ARQ, routing and crypto code runs, driven through the same audio callback a real sound card uses. The results are reproducible with fixed seeds, and 75 tests check every layer.

**Q. How do you know your simulator is correct?**
With 50 stations and zero latency it reproduces the textbook ALOHA curves; with 10 stations it lands on the Kleinrock–Tobagi CSMA curve.

**Q. How does the dashboard get live data?**
A small web server from Python's standard library streams every event to the browser with Server-Sent Events; the spectrogram samples the microphone buffer about 12 times a second, one column per symbol.

**Q. How is latency measured?**
`selftest` plays a chirp and finds it in its own recording with the matched filter; the offset is the round-trip audio latency. It's saved and used for ACK timeouts and timing advance.

**Q. What would you improve next?**
Reliability in echoey rooms (shorter frames, an echo-tolerant receiver), OFDM for higher speed, near-ultrasonic tones (18–20 kHz) for inaudibility, link-state routing, and a Diffie–Hellman key exchange.

**Q. What was the hardest bug?**
Several are worth telling: the original starter modem reported the chirp 10 symbols late, so it never decoded anything; the receiver fell 30 s behind by rescanning old audio until we made it incremental; and the backup demo crashed if a clap destroyed the very last ACK, fixed by letting the receiver linger the full 20 s.

---

# Part F. Quick reference

## 23. Formula sheet

| Quantity | Formula | Value / note |
|---|---|---|
| Samples per symbol | fs / baud | 48000 / 100 = 480 |
| Cycles per symbol | f / baud | 1400/100 = 14, 2600/100 = 26 (must be whole) |
| Bit rate | baud × log₂M × code rate | MCS 4: 200 × 4 = 800; MCS 0: 100 × 4/7 = 57 |
| Nyquist limit | fs / 2 | 24 kHz (highest tone 3.8 kHz) |
| Hamming(7,4) rate | 4/7 | Fixes 1 error per 7 bits |
| CRC-r guarantee | All bursts ≤ r bits | CRC-16: ≤ 16 |
| SR window | W ≤ 2^(m−1) | m = 8 → 128 (we use ≤ 32) |
| GBN window | W ≤ 2^m − 1 | m = 8 → 255 |
| Stop-and-Wait efficiency | T_f / (T_f + T_ack + 2·latency + proc) | ≈ 46 % here |
| Pure ALOHA (∞ stations) | S = G·e^(−2G) | Peak 18.4 % at G = 0.5 |
| Slotted ALOHA (∞) | S = G·e^(−G) | Peak 36.8 % at G = 1 |
| Slotted ALOHA (N) | S = G(1 − G/N)^(N−1) | N = 3: 44.4 % |
| Pure ALOHA (N) | S = G(1 − G/N)^(2(N−1)) | N = 3: 24.6 % at G = 0.6 |
| Non-persistent CSMA | S = G·e^(−aG) / (G(1+2a) + e^(−aG)) | a ≈ 0.19 on laptops |
| Sensing delay a | (latency + 30 ms) / frame time | ≈ 0.19 |
| ACK timeout | 2·latency + ACK airtime + 1 s | |
| Bellman–Ford update | cost(D via n) = cost_n(D) + 1 | Infinity = 16 |
| Forgery chance | 2^(−48) per try | 6-byte tag |

**Frame airtimes (base speed, 100 baud):** 32-byte DATA 3.61 s; ACK 1.21 s (GBN) / 1.53 s (SR); PING 1.37 s + PONG 1.45 s; MCS 0 DATA 6.07 s; 96-byte DATA at MCS 4 1.87 s.

## 24. Command sheet

| Command | What it does |
|---|---|
| `py -m pytest -q` | Run all 75 tests |
| `py -m acousticnet sim-demo --rate base` | File transfer in the simulated room; noise button on the dashboard |
| `py -m acousticnet sim-demo --scenario speed --speed 2` | Adaptive speed with a simulated fan |
| `py -m acousticnet sim-demo --scenario security --speed 2` | Encryption, replay and wrong-key checks |
| `py -m acousticnet sim-demo --scenario relay --speed 2` | Routing through a relay, traceroute |
| `py -m acousticnet selftest` | Check a laptop's audio, save its latency |
| `py -m acousticnet recv --robust` | Real receiver, robust mode |
| `py -m acousticnet send FILE --robust` | Real sender, robust mode |
| `py -m acousticnet ping --robust` | Real acoustic ping |
| `py -m acousticnet simulate` | Regenerate all simulation results |
| `py -m acousticnet plot` | Redraw all figures |

Add `--log results\run.csv` to any command to save every event to a file.

## 25. Glossary

| Term | Meaning |
|---|---|
| **ACK** | Acknowledgement: "I received these frames" |
| **ARF** | Auto Rate Fallback: step speed down after failures, up after successes |
| **ARQ** | Automatic Repeat reQuest: detect loss and resend |
| **Baud** | Symbols per second |
| **Block-ACK** | One ACK covering a burst of frames (802.11n) |
| **Chirp** | A tone sweeping in frequency, used to find and time a frame |
| **CRC** | Cyclic redundancy check: polynomial-division error detection |
| **CSMA** | Carrier sense multiple access: listen before talking |
| **Distance vector** | Routing by sharing cost-to-destination tables with neighbours |
| **Encrypt-then-MAC** | Encrypt first, then authenticate the ciphertext |
| **FEC** | Forward error correction: fix errors without resending |
| **FSK** | Frequency shift keying: data as different tones |
| **Goodput** | Useful bits delivered per second |
| **Half-duplex** | Can send or receive, not both at once |
| **Hamming(7,4)** | Code adding 3 parity bits to 4 data bits; fixes 1 error |
| **Hidden terminal** | Two senders that can't hear each other collide at a receiver |
| **HMAC** | Keyed hash for authentication |
| **Interleaving** | Spreading codewords so a burst hits each one only once |
| **Matched filter** | Correlating with a known shape to find it in noise |
| **MCS** | Modulation and coding scheme: one of the five speeds |
| **Nonce** | Number used once; here session id + message number |
| **Orthogonal tones** | Tones that don't leak into each other over a symbol |
| **PBKDF2** | Slow key derivation from a passphrase |
| **POLL** | Flag meaning "answer me now" |
| **Poison reverse** | Advertise infinity back to the neighbour you route through |
| **Selective Repeat** | Resend only the frames that were lost |
| **SNR** | Signal-to-noise ratio, in dB |
| **Store-and-forward** | Receive a frame completely, then retransmit it |
| **Timing advance** | Sending early to compensate for known delay |
| **TTL** | Time to live: hop counter that stops loops |
| **TTLX** | AcousticNet's "time exceeded" message, like ICMP |
