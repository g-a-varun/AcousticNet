# AcousticNet demo-day guide

Oct 23, 2026

The ROG Strix is the receiver on the projector, the TUF sends, and the MacBook Pro plays Eve and then the relay. All three need the same fixed code, a READY self-test and their own speakers as the sound output.

## Roles

Keep these roles for every rehearsal, so each laptop's calibration and settings match its job.

| Laptop | Role | Node address | Why this laptop |
| --- | --- | --- | --- |
| ASUS TUF | A, the sender | 1 (default for `send`, `ping`, `traceroute`) | Its operator types most commands and does the clap |
| ASUS ROG Strix | B, the receiver, connected to the projector | 2 (default for `recv`) | Shows the dashboard all demo; HDMI and a big screen |
| MacBook Pro M5 | C, Eve in Act 6, then the relay in Act 7 | 250 as Eve, 3 as the relay (defaults) | Its three-mic array hears both other laptops best, which Eve and the relay both need |

You can swap the two ASUS laptops. Every laptop must use the same `--baud` and `--fec` options (the defaults are fine).

## Placement

```
Acts 1-6: the TUF sends to the ROG, the Mac listens in

                    +------------------------+
                    |      MacBook Pro       |
                    |   C . Eve . node 250   |
                    | screen to the audience |
                    +------------------------+
                       ^                  ^
                       :  hears every     :
                       :  frame: sound    :
                       :  is broadcast    :
+---------------------+                    +-------------------------+
|      ASUS TUF       |   0.5-1 m apart    |     ASUS ROG Strix      |
| A . sender . node 1 |<------------------>| B . receiver . node 2   |
| types most commands |  speakers facing   | on the projector (HDMI) |
+---------------------+                    +-------------------------+


Act 7: in a row, the Mac relays between the two ASUS laptops

+----------------+  hop 1  +---------------------+  hop 2  +----------------+
|    ASUS TUF    | ------> |     MacBook Pro     | ------> | ASUS ROG Strix |
|   A . node 1   |         | R . relay . node 3  |         |   B . node 2   |
| --ignore 2     |         | forwards each frame |         | --ignore 1     |
+----------------+         +---------------------+         +----------------+
        :                                                          :
        :............................  X  ...........................:
          --ignore: they act as if they cannot hear each other
                          (an emulated wall)
```

For Acts 1-6, put the TUF and the ROG 0.5-1 m apart with their speakers facing, and keep the Mac nearby. For Act 7, line up all three with the Mac in the middle.

## Get the fixed code onto all three laptops

All three laptops must run the same code. Commit and push the fixes from your laptop first (skip this if `git status` shows nothing to commit), then copy from GitHub.

1. On your laptop (the one with `X:\Projects\AcousticNet`), in a terminal:

```
cd X:\Projects\AcousticNet
git add -A
git commit -m "Fix sim-demo linger and BER wait, update README"
git push
```

2. On each of the other two laptops:

```
git clone https://github.com/g-a-varun/AcousticNet.git
cd AcousticNet
```

Already cloned before? Run `git pull` inside the folder instead. No git on a laptop: on GitHub use Code → Download ZIP and unzip it. If the repo is private, add your teammates as collaborators or copy the folder by pendrive.

3. Run every command from inside this folder (the one holding `README.md`). The code folder inside must stay named `acousticnet`, all lowercase.

## One-time setup: the two ASUS laptops (Windows)

The biggest risk on these laptops is ASUS's own noise cancellation: it treats a steady tone as noise and erases it. Do every step on both laptops.

1. Install Python 3.12 or 3.13 from python.org and tick "Add python.exe to PATH". On Windows, type `py` wherever the README says `python`.
2. In the project folder: `py -m pip install -r requirements.txt`
3. Armoury Crate → Device → System → Audio: switch AI Noise Cancelation off for both the microphone (input) and the speakers (output).
4. If Dolby Access or Dolby Atmos is installed (common on the ROG Strix), switch it off for the demo. If NVIDIA Broadcast is installed, make sure the default microphone is the laptop's own mic, not "NVIDIA Broadcast".
5. Settings → System → Sound:
   - Input (Microphone Array): Audio enhancements off, input volume about 75.
   - Output (Speakers): Audio enhancements off, system volume about 70 %.
6. Control Panel → Sound → Communications tab → "Do nothing", so Windows doesn't lower the volume.
7. Settings → Privacy & security → Microphone: microphone access on, and "Let desktop apps access your microphone" on.
8. Armoury Crate operating mode: Silent or Performance, not Turbo. Turbo fans are loud right next to the microphone, and the stack needs very little CPU. Keep the charger plugged in.
9. `py -m acousticnet selftest` must end with **READY**. A "tone faded while it was still playing" warning means step 3, 4 or 5 is still on.

The ROG Strix is the one on the projector: after you plug in HDMI, Windows often moves the sound output to the projector. Set Output back to the laptop's Speakers and run `selftest` again, otherwise its ACKs come out of the projector or nowhere.

## One-time setup: the MacBook Pro

On the Mac the usual blocker is microphone permission for Terminal: without it the mic records pure silence and nothing works.

1. Install Python 3.12 or 3.13 from python.org (or `brew install python@3.12`). The first `git` command may offer to install the Command Line Tools; accept.
2. In Terminal, inside the project folder, make a virtual environment once:

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. In every new Terminal window, run `source .venv/bin/activate` first. After that, `python -m acousticnet …` works exactly as written in the README.
4. Run `python -m acousticnet selftest`. macOS asks whether Terminal may use the microphone: allow it, then run `selftest` again, because the first run recorded while the prompt was up. No prompt and a "pure silence" warning: System Settings → Privacy & Security → Microphone → Terminal on, then quit and reopen Terminal.
5. System Settings → Sound: Output = MacBook Pro Speakers at about 60–70 %; Input = MacBook Pro Microphone, input volume about 70 %.
6. Mic Mode must be Standard, not Voice Isolation. It is Standard by default; Control Centre → Mic Mode shows it while an app is using the mic.
7. macOS has no airplane mode: switch Wi-Fi and Bluetooth off in Control Centre. Keep the charger in, and run `caffeinate -d` in a spare Terminal tab so the screen never sleeps.
8. To stop a command, press Control-C (not Command-C).

## Day-before rehearsal

Do this in the room you will demo in, if you can: every room echoes differently.

- [ ] All three laptops: `selftest` ends with READY.
- [ ] TUF ↔ ROG: ROG runs `py -m acousticnet recv`; TUF runs `py -m acousticnet ping`. All 3 pings answer.
- [ ] Mac ↔ ROG: leave `recv` running on the ROG; Mac runs `python -m acousticnet ping --addr 3`.
- [ ] TUF ↔ Mac: Mac runs `python -m acousticnet recv --addr 3`; TUF runs `py -m acousticnet ping --to 3`.
- [ ] Plug the ROG into a projector or monitor, set its sound output back to Speakers, and run `selftest` again.
- [ ] Run the whole demo script below twice, including the clap, the noise phone, Eve and the relay. Note which acts work reliably.
- [ ] On the ROG, run each backup once: `py -m acousticnet sim-demo --scenario security --speed 2` (and `speed`, `relay`).
- [ ] Record a phone video of one complete run as the last-resort backup.
- [ ] Decide who says what: the README's script assumes Person 1 types on the TUF, Person 2 narrates the ROG's dashboard, Person 3 runs the Mac and the noise phone.

## Demo script

Acts 1–4 take about 8 minutes and each later act about 3–4. For a 10-minute slot, do Acts 1–3 and then Act 6. `send` and `replay` stay open after they finish, so press Ctrl+C (Control-C on the Mac) before every new command on the same laptop.

| Act | Laptop | Type or do | Point out |
| --- | --- | --- | --- |
| 1. No radio | All three | Airplane mode on (Mac: Wi-Fi and Bluetooth off). ROG: `ping google.com` | It fails: sound is the only link left |
| 2. Send a file | ROG | `py -m acousticnet recv` | Dashboard on the projector: teal lane = 0, amber lane = 1, one column per bit |
|  | TUF | `py -m acousticnet send demo_files/secret_message.txt --rate base` | The chirp before each frame, "RX DATA n OK", one ACK per 4 frames (Block-ACK) |
| 3. The clap | TUF | Ctrl+C, then the same `send` again |  |
|  | Anyone | Clap 2–3 times next to the ROG's mic right after "RX DATA 1 OK" | DATA 2 turns red (CRC FAIL), only DATA 2 is resent, ends with "SHA-256 matches" |
| 4. Ping | TUF | Ctrl+C, then `py -m acousticnet ping` | Round trip about 3.5–4 s: 2.8 s is airtime, the rest is sound-card latency |
| 5. Adaptive speed | TUF | Ctrl+C, then `py -m acousticnet send demo_files/secret_message.txt` | The probe picks MCS 4 (800 bit/s); 16 tone lanes instead of 2 |
|  | Phone | White noise 20–30 cm from the ROG's mic, then stop it | The speed steps down ("frames lost"), then jumps back up |
| 6. Eve | Mac | `python -m acousticnet sniff`, screen turned to the audience | Eve hears every frame, whoever it is for |
|  | ROG | Ctrl+C, then `py -m acousticnet recv` |  |
|  | TUF | Ctrl+C, then `py -m acousticnet send demo_files/exam_answers.txt` | The Mac shows "Eve STOLE exam_answers.txt" with the full text |
|  | ROG | Ctrl+C, then `py -m acousticnet recv --key vit-cn-lab` |  |
|  | TUF | Ctrl+C, then `py -m acousticnet send demo_files/exam_answers.txt --key vit-cn-lab` | ROG: "Encrypted + authenticated"; Mac: only random bytes, plus a `SNIFF_CAPTURE` line with the recording's path |
|  | Mac | Wait 20 s after the TUF prints "Done". Ctrl+C, then `ls -t sniffed`: the first `capture_1_to_2_….json` listed is the encrypted transfer. Then `python -m acousticnet replay sniffed/<that file name>` | ROG: "replay attack refused" |
|  | Mac | Ctrl+C, then `python -m acousticnet send demo_files/forged_message.txt --addr 250 --key password123` | ROG: "authentication failed" |
| 7. Relay | All three | Line them up: TUF — Mac — ROG |  |
|  | Mac | Ctrl+C, then `python -m acousticnet relay` |  |
|  | ROG | Ctrl+C, then `py -m acousticnet recv --routed --ignore 1` | `--ignore` makes the TUF and the ROG pretend they can't hear each other, like a wall |
|  | TUF | Ctrl+C, then `py -m acousticnet traceroute --to 2 --ignore 2` | Prints `1 -> 3 -> 2`; routes can take up to a minute |
|  | TUF | `py -m acousticnet send demo_files/hello.txt --routed --ignore 2` | The file reaches the ROG through the Mac; the routing panel shows "node 1 via node 3, 2 hops" |

The 20-second wait in Act 6 matters. Right after a transfer, the ROG keeps re-answering its sender for 20 s in case its last ACK was lost, so a replay inside that window is simply re-acknowledged. A real attacker replays later, and that replay is refused.

## If something goes wrong

Give any live act two tries, then switch to its simulated version on the ROG so the projector keeps showing something.

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `selftest`: "tone faded while it was still playing" (ASUS) | Armoury Crate AI Noise Cancelation, Dolby or Audio enhancements still on | Switch them off (ASUS setup steps 3–5), run `selftest` again |
| `selftest`: "records pure silence" (Mac) | Terminal has no microphone permission | Privacy & Security → Microphone → Terminal on; reopen Terminal |
| The TUF never gets ACKs once the ROG is on the projector | Windows moved the ROG's sound output to HDMI | ROG: Sound → Output → Speakers, then `selftest` |
| `python` not found | Mac: the virtual environment isn't active. Windows: the command there is `py` | Mac: `source .venv/bin/activate`; Windows: type `py` |
| `No module named acousticnet` | Terminal is not in the project folder | `cd` into the folder that holds `README.md` |
| Frames heard, but CRC fails often | Echo or noise in the room | Move to 0.5 m; or add `--rate 0`, or `--baud 50` on every laptop |
| The replay says "ACCEPTED" | It ran within 20 s of the encrypted transfer | Wait 20 s and replay again: now it is refused |
| Relay: "no route to node 2" for a long time | Adverts collided, or the Mac can't hear one side | Wait up to 60 s; check `ping` TUF ↔ Mac and Mac ↔ ROG; move the Mac |

Backups, all on the ROG (add `--speed 2` to halve the time; the dashboard has a noise button instead of the clap):

- Core demo: `py -m acousticnet sim-demo`
- Adaptive speed: `py -m acousticnet sim-demo --scenario speed`
- Eve: `py -m acousticnet sim-demo --scenario security`
- Relay: `py -m acousticnet sim-demo --scenario relay`
- Nothing works: play the backup video and present the graphs.

## Sources

- [How to enable AI Noise Cancelation on an ROG headset, laptop or motherboard](https://rog.asus.com/us/articles/guides/how-to-enable-ai-noise-cancelation-on-an-rog-headset-laptop-or-motherboard/) (ASUS ROG): where the setting lives in Armoury Crate.
- [Use Mic Mode on Mac](https://support.apple.com/en-au/guide/mac-help/mchle82b42f0/13.0/mac/13.0) (Apple Support): Standard vs Voice Isolation.
- Everything else comes from the project's own README (sections 7–8) and from running the code.
