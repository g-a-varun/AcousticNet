"""
plot_results.py - turns every CSV in results/ into the report figures in results/figures/.

    python -m acousticnet plot        (or: python experiments/plot_results.py)

Simulation CSVs (results/sim/*.csv) are always plotted.  As soon as you run the hardware
experiments, their CSVs (results/measured/*.csv) are added to the SAME figures as big filled
dots labelled "measured on laptops", so you can put theory, simulation and reality side by side.

Colours: a validated colour-blind-safe categorical palette, always in the same order:
    slot 1 blue   = Pure ALOHA / Stop-and-Wait / 50 baud / parity
    slot 2 orange = Slotted ALOHA / Go-Back-N / 100 baud / Internet checksum
    slot 3 aqua   = CSMA / Selective Repeat / 200 baud / CRC-8
    slot 4 yellow = CRC-16 (error-detection figure) / fastest speed MCS4 (speed figures)
In the speed figures blue = 2-FSK (dashed = with FEC), orange = 4-FSK, aqua = 16-FSK and
near-black = adaptive speed, our own scheme.  Grey dashed lines are textbook theory.
"""
import csv
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from acousticnet import theory as th          # noqa: E402

BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
GREY, INK, INK2, GRID, SURFACE = "#8a8984", "#0b0b0b", "#52514e", "#e7e6e1", "#fcfcfb"


def _read(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in list(r.items()):
            try:
                r[k] = float(v)
            except (TypeError, ValueError):
                pass
    return rows


def _style(plt):
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": "#c9c8c2", "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 11.5, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 10, "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 9,
        "ytick.labelsize": 9, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 2.0,
        "lines.markersize": 6, "legend.frameon": False, "legend.fontsize": 8.5, "font.size": 10,
        "figure.dpi": 110, "savefig.dpi": 160, "savefig.bbox": "tight",
    })


def fig_mac(plt, out):
    sim = _read(os.path.join(ROOT, "results", "sim", "mac_sim.csv"))
    emu = _read(os.path.join(ROOT, "results", "sim", "mac_emulated.csv"))
    meas = _read(os.path.join(ROOT, "results", "measured", "mac_measured.csv"))
    if not sim:
        print("  (no mac_sim.csv - run `python -m acousticnet simulate mac`)")
        return
    lab = [r for r in sim if r["scenario"] == "lab_N3"]
    val = [r for r in sim if r["scenario"] == "validate_N50"]
    T = lab[0]["frame_s"]
    a_lab = float(np.mean([r["a"] for r in lab if r["protocol"] == "csma"]))
    fig, axes = plt.subplots(1, 3, figsize=(14.5, 4.6), sharey=True)
    G = np.linspace(0.001, 3, 400)
    spec = [
        ("aloha", "Pure ALOHA", BLUE, "G_tx", "S", th.aloha_pure(G), th.aloha_pure_finite(G, 3),
         "textbook, infinite stations (peak 18.4 %)", "theory, 3 stations (peak 24.6 %)"),
        ("slotted", "Slotted ALOHA (per slot)", ORANGE, "G_slot", "S_slot", th.aloha_slotted(G),
         th.aloha_slotted_finite(G, 3), "textbook, infinite stations (peak 36.8 %)",
         "theory, 3 stations (peak 44.4 %)"),
        ("csma", "Non-persistent CSMA", AQUA, "G_attempts", "S", th.csma_nonpersistent(G, 0.01),
         th.csma_nonpersistent(G, a_lab), "textbook, radio-like delay (a = 0.01)",
         f"theory, laptop latency, many stations (a = {a_lab:.2f})"),
    ]
    for ax, (proto, title, col, gx, sy, inf, fin, inf_lbl, fin_lbl) in zip(axes, spec):
        ax.plot(G, inf, color=GREY, ls="--", lw=1.6, label=inf_lbl)
        ax.plot(G, fin, color=col, ls="--", lw=1.6, label=fin_lbl)
        rows = sorted([r for r in lab if r["protocol"] == proto], key=lambda r: r[gx])
        ax.plot([r[gx] for r in rows], [r[sy] for r in rows], color=col, marker="o", lw=2,
                label="simulation: 3 laptops, real timings")
        v = [r for r in val if r["protocol"] == proto]
        ax.plot([r[gx] for r in v], [r[sy] for r in v], ls="none", marker="o", mfc=SURFACE, mec=GREY,
                mew=1.4, ms=7, label="simulation: 50 stations, no latency (checks the simulator)")
        if proto == "csma":
            z = sorted([r for r in sim if r["scenario"] == "latency_N3_0ms"], key=lambda r: r["G_attempts"])
            if z:
                ax.plot([r["G_attempts"] for r in z], [r["S"] for r in z], color=col, ls=":", marker="^",
                        mfc=SURFACE, mew=1.4, label="simulation: same 3 laptops with ZERO audio latency")
        e = [r for r in emu if r.get("protocol") == proto]
        if e:
            ex = [r.get(gx, r.get("G_tx")) for r in e]
            ey = [r.get(sy, r.get("S")) for r in e]
            ax.plot(ex, ey, ls="none", marker="s", mfc=SURFACE, mec=col, mew=1.6, ms=8,
                    label="emulated room (real code over virtual air)")
        m = [r for r in meas if r.get("protocol") == proto]
        if m:
            mx = [r.get(gx, r.get("G_tx")) for r in m]
            my = [r.get(sy, r.get("S")) for r in m]
            ax.plot(mx, my, ls="none", marker="o", color=col, mec=INK, mew=1.2, ms=10,
                    label="MEASURED on laptops", zorder=5)
        ax.set_title(title)
        ax.set_xlabel("offered load G (frames per frame-time)" if proto != "slotted" else "offered load G (frames per slot)")
        ax.set_xlim(0, 3)
        ax.set_ylim(0, 0.9)
        if proto == "csma":
            ax.legend(loc="lower right", fontsize=7.8, labelspacing=0.3, borderaxespad=0.2, handlelength=1.8)
        else:
            ax.legend(loc="upper right")
    axes[0].set_ylabel("throughput S (useful fraction of airtime)")
    fig.suptitle("Three laptops sharing one acoustic channel: finite-station theory is the right yardstick, "
                 "and audio latency is CSMA's handicap", x=0.01, ha="left", fontsize=12.5, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig1_mac_throughput.png"))
    plt.close(fig)
    print("  fig1_mac_throughput.png")

    # --- CSMA vs latency ---
    lat = [r for r in sim if str(r["scenario"]).startswith("latency_")]
    if lat:
        fig, ax = plt.subplots(figsize=(7.6, 4.4))
        x = np.linspace(0, max(r["rt_ms"] for r in lat), 200)
        theo = [th.peak(th.csma_nonpersistent, (xx / 1000 + 0.03) / T)[1] for xx in x]
        ax.axvspan(100, 300, color=AQUA, alpha=0.10, lw=0)
        ax.text(200, 0.04, "typical laptop\naudio latency", ha="center", va="bottom", color=INK2, fontsize=9)
        ax.plot(x, theo, color=GREY, ls="--", lw=1.6, label="Kleinrock-Tobagi theory (many stations), peak S")
        for n_st, mk, mfc in ((3, "o", AQUA), (10, "s", SURFACE)):
            sub = [r for r in lat if str(r["scenario"]).startswith(f"latency_N{n_st}_")]
            rts = sorted({r["rt_ms"] for r in sub})
            smax = [max(r["S"] for r in sub if r["rt_ms"] == rt) for rt in rts]
            ax.plot(rts, smax, color=AQUA, marker=mk, mfc=mfc, mew=1.5, ls="-" if n_st == 3 else "--",
                    label=f"simulation, {n_st} laptops, best S over the load sweep")
        ax.axhline(th.peak(th.aloha_slotted_finite, 3)[1], color=ORANGE, lw=1.2, ls=":",
                   label="slotted ALOHA, 3 stations (44.4 %)")
        ax.axvline(2 / 343 * 1000, color=INK2, lw=1, ls=":")
        ax.text(2 / 343 * 1000 + 8, 0.05, "sound travelling 2 m takes only 6 ms", color=INK2, fontsize=8.5)
        ax.set_xlabel("round-trip audio latency of each laptop (ms)")
        ax.set_ylabel("best achievable throughput S")
        ax.set_ylim(0, 0.95)
        ax.set_title("CSMA gets worse as sound-card latency grows: the laptop 'hears' the channel late")
        ax.legend(loc="upper right")
        fig.savefig(os.path.join(out, "fig2_csma_latency.png"))
        plt.close(fig)
        print("  fig2_csma_latency.png")


def fig_arq(plt, out):
    rows = _read(os.path.join(ROOT, "results", "sim", "arq_sim.csv"))
    if not rows:
        print("  (no arq_sim.csv)")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.5))
    names = {"sw": ("Stop-and-Wait", BLUE), "gbn": ("Go-Back-N", ORANGE), "sr": ("Selective Repeat", AQUA)}
    for mode, w in (("sw", 1), ("gbn", 4), ("sr", 4), ("gbn", 8), ("sr", 8)):
        rr = sorted([r for r in rows if r["experiment"] == "loss" and r["mode"] == mode and r["window"] == w],
                    key=lambda r: r["loss"])
        name, col = names[mode]
        lbl = name + ("" if mode == "sw" else f", window {w}")
        a1.plot([100 * r["loss"] for r in rr], [r["goodput_bps"] for r in rr], color=col,
                ls="-" if w != 8 else "--", marker="o" if w != 8 else "s", label=lbl)
    a1.set_xlabel("frames lost (%), data and ACKs alike")
    a1.set_ylabel("file goodput (bit/s)")
    a1.set_title("Under loss, Selective Repeat wins; Go-Back-N with a big window loses most")
    a1.legend()
    for ber, col in ((1e-3, BLUE), (3e-3, ORANGE)):
        rr = sorted([r for r in rows if r["experiment"] == "chunk" and abs(r["ber"] - ber) < 1e-9],
                    key=lambda r: r["chunk"])
        a2.plot([r["chunk"] for r in rr], [r["goodput_bps"] for r in rr], color=col, marker="o",
                label=f"bit error rate {ber:g}")
    a2.axvline(32, color=INK2, ls=":", lw=1)
    a2.text(33, a2.get_ylim()[1] * 0.95, "our default: 32 bytes", color=INK2, fontsize=9, va="top")
    a2.set_xscale("log", base=2)
    a2.set_xticks([8, 16, 32, 64, 128])
    a2.set_xticklabels(["8", "16", "32", "64", "128"])
    a2.set_xlabel("payload bytes per frame")
    a2.set_ylabel("file goodput (bit/s)")
    a2.set_title("Frame size has a sweet spot (Selective Repeat, window 4)")
    a2.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig3_arq_goodput.png"))
    plt.close(fig)
    print("  fig3_arq_goodput.png")


def fig_phy(plt, out):
    rows = _read(os.path.join(ROOT, "results", "sim", "phy_sim.csv"))
    if not rows:
        print("  (no phy_sim.csv)")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)
    cols = {50: BLUE, 100: ORANGE, 200: AQUA}
    for exp, ax, xk, xl in (("noise", a1, "snr_db", "in-band signal-to-noise ratio (dB)"),
                            ("echo", a2, "drr_db", "direct-to-echo ratio (dB, lower = more echo)")):
        for baud in (50, 100, 200):
            for fec in (False, True):
                rr = sorted([r for r in rows if r["experiment"] == exp and r["baud"] == baud
                             and str(r["fec"]) == str(fec)], key=lambda r: r[xk])
                ax.plot([r[xk] for r in rr], [100 * r["fsr"] for r in rr], color=cols[baud],
                        ls="--" if fec else "-", marker="s" if fec else "o",
                        label=f"{baud} baud" + (", Hamming FEC" if fec else ""))
        ax.set_xlabel(xl)
        ax.set_ylim(-3, 103)
    a2.invert_xaxis()
    a1.set_ylabel("frames received correctly (%)")
    a1.set_title("Noise (mild echo): slower and FEC-coded frames survive more noise")
    a2.set_title("Echo (SNR 20 dB): heavy echo smears fast symbols together")
    h, l = a1.get_legend_handles_labels()
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.legend(h, l, loc="lower center", ncol=6, bbox_to_anchor=(0.5, 0.0))
    fig.savefig(os.path.join(out, "fig4_phy_robustness.png"))
    plt.close(fig)
    print("  fig4_phy_robustness.png")


def fig_errdetect(plt, out):
    rows = _read(os.path.join(ROOT, "results", "sim", "errdetect_sim.csv"))
    if not rows:
        print("  (no errdetect_sim.csv)")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.5), sharey=True)
    dets = ["parity (1 bit)", "Internet checksum (16 bit)", "CRC-8", "CRC-16"]
    cols = [BLUE, ORANGE, AQUA, YELLOW]
    trials = rows[0]["trials"]
    floor = 0.5 / trials
    for ax, exp, xl in ((a1, "burst", "burst length (bits)"), (a2, "random", "number of random bit flips")):
        for det, col in zip(dets, cols):
            rr = sorted([r for r in rows if r["experiment"] == exp and r["detector"] == det], key=lambda r: r["size"])
            x = [r["size"] for r in rr]
            y = [max(r["undetected_rate"], floor) for r in rr]
            ax.plot(x, y, color=col, marker="o", label=det)
            zeros = [(xx, floor) for xx, r in zip(x, rr) if r["undetected"] == 0]
            if zeros:
                ax.plot(*zip(*zeros), ls="none", marker="o", mfc=SURFACE, mec=col, mew=1.6, ms=7)
        ax.axhline(floor, color=GREY, lw=0.8, ls=":")
        ax.set_yscale("log")
        ax.set_xlabel(xl)
    a1.axvline(16.5, color=INK2, lw=1, ls=":")
    a1.text(16.8, 0.5, "CRC-16 catches every\nburst up to 16 bits", fontsize=8.5, color=INK2, va="top")
    a1.text(1.5, floor * 1.25, f"hollow = 0 missed in {int(trials):,} tries", fontsize=8.5, color=INK2)
    a1.set_ylabel("fraction of corrupted frames NOT detected")
    a1.set_title("Burst errors (what a clap or a cough does)")
    a2.set_title("Scattered bit errors (what hiss does)")
    h, l = a1.get_legend_handles_labels()
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    fig.legend(h, l, loc="lower center", ncol=4, bbox_to_anchor=(0.5, 0.0))
    fig.savefig(os.path.join(out, "fig5_error_detection.png"))
    plt.close(fig)
    print("  fig5_error_detection.png")


MCS_STYLE = {0: ("MCS0  2-FSK + FEC, 57 bit/s", BLUE, "--", "s"),
             1: ("MCS1  2-FSK, 100 bit/s", BLUE, "-", "o"),
             2: ("MCS2  4-FSK, 200 bit/s", ORANGE, "-", "o"),
             3: ("MCS3  16-FSK, 400 bit/s", AQUA, "-", "o"),
             4: ("MCS4  16-FSK x2 baud, 800 bit/s", YELLOW, "-", "D")}


def fig_speeds(plt, out, phy_csv=None, rate_csv=None):
    """fig7: the speed ladder (frame success per MCS) + adaptive speed vs fixed speeds"""
    rows = [r for r in _read(phy_csv or os.path.join(ROOT, "results", "sim", "phy_sim.csv"))
            if r.get("experiment") == "mcs"]
    rate = _read(rate_csv or os.path.join(ROOT, "results", "sim", "rate_sim.csv"))
    if not rows:
        print("  (no 'mcs' rows in phy_sim.csv - run `python -m acousticnet simulate phy`)")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.6))
    for m in range(5):
        rr = sorted([r for r in rows if int(r["mcs"]) == m], key=lambda r: r["snr_db"])
        lbl, col, ls, mk = MCS_STYLE[m]
        a1.plot([r["snr_db"] for r in rr], [100 * r["fsr"] for r in rr], color=col, ls=ls, marker=mk, label=lbl)
    a1.set_xlabel("in-band signal-to-noise ratio (dB)")
    a1.set_ylabel("frames received correctly (%)")
    a1.set_ylim(-3, 103)
    a1.set_xlim(-6.5, 15.5)
    a1.set_title("The speed ladder: 2-, 4- and 16-FSK need the same signal;\n"
                 "only doubling the tone rate (MCS4) costs about 3 dB")
    a1.legend(loc="lower right")
    env = [r for r in rate if r.get("experiment") == "envelope"]
    if env:
        series = [("fixed MCS1 (2-FSK)", BLUE, "-", "o", "fixed speed MCS1"),
                  ("fixed MCS3 (16-FSK)", AQUA, "-", "o", "fixed speed MCS3"),
                  ("fixed MCS4 (fastest)", YELLOW, "-", "D", "fixed speed MCS4 (fastest)"),
                  ("adaptive", INK, "-", "o", "ADAPTIVE speed (ours)")]
        for name, col, ls, mk, lbl in series:
            rr = sorted([r for r in env if r["strategy"] == name], key=lambda r: r["snr_db"])
            a2.plot([r["snr_db"] for r in rr], [r["goodput_bps"] for r in rr], color=col, ls=ls, marker=mk,
                    lw=2.6 if name == "adaptive" else 1.8, ms=7 if name == "adaptive" else 5.5,
                    label=lbl, zorder=4 if name == "adaptive" else 2)
        a2.set_xlabel("in-band signal-to-noise ratio (dB)")
        a2.set_ylabel("file goodput (bit/s), 1.5 KB file")
        a2.set_title("No fixed speed wins everywhere;\nadaptive speed stays at (or near) the top")
        a2.legend(loc="lower right")
        a2.set_ylim(bottom=0)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig7_speed_ladder.png"))
    plt.close(fig)
    print("  fig7_speed_ladder.png")


def fig_walk(plt, out, rate_csv=None):
    """fig8: the receiving laptop is carried away and back"""
    rows = _read(rate_csv or os.path.join(ROOT, "results", "sim", "rate_sim.csv"))
    snr = sorted([r for r in rows if r.get("experiment") == "walk_snr"], key=lambda r: r["t"])
    if not snr:
        print("  (no rate_sim.csv - run `python -m acousticnet simulate rate`)")
        return
    fig, (a0, a1) = plt.subplots(2, 1, figsize=(10.5, 6.6), sharex=True, gridspec_kw={"height_ratios": [1, 1.6]})
    t_end = max(320.0, max((r["t"] for r in rows if r.get("experiment") == "walk_result"
                            and r["strategy"] != "fixed MCS1 (2-FSK)"), default=300) + 30)
    snr = [r for r in snr if r["t"] <= t_end]
    t = [r["t"] for r in snr]
    a0.fill_between(t, [r["snr_db"] for r in snr], -50, color=GREY, alpha=0.18, lw=0, step="post")
    a0.step(t, [r["snr_db"] for r in snr], where="post", color=GREY, lw=1.4,
            label="signal-to-noise ratio (dB, left axis)")
    a0.set_ylabel("SNR (dB)")
    lo = min(r["snr_db"] for r in snr)
    a0.set_ylim(min(0, lo - 2), 42)
    tw = a0.twinx()
    tw.grid(False)
    tw.spines["right"].set_visible(True)
    ms = sorted([r for r in rows if r.get("experiment") == "walk_mcs" and r["strategy"] == "adaptive"],
                key=lambda r: r["t"])
    if ms:
        tw.step([r["t"] for r in ms], [r["mcs"] for r in ms], where="post", color=INK, lw=1.8,
                label="speed chosen by adaptive (MCS, right axis)")
    tw.set_ylim(-0.4, 5.9)
    tw.set_yticks(range(5))
    tw.set_ylabel("MCS")
    a0.set_title("A noisy fan next to the receiver switches on and off every 45 s:\n"
                 "adaptive speed drops to a safer speed while it runs, and climbs back after")
    h0, l0 = a0.get_legend_handles_labels()
    h1, l1 = tw.get_legend_handles_labels()
    a0.legend(h0 + h1, l0 + l1, loc="upper right", ncol=2)
    size = [r["bytes"] for r in rows if r.get("experiment") == "walk_size"]
    total_kb = (size[0] if size else max((r["bytes"] for r in rows if r.get("experiment") == "walk"), default=0)) / 1000
    result = {r["strategy"]: r for r in rows if r.get("experiment") == "walk_result"}
    # (fixed MCS1 is in the CSV too, but at 100 bit/s it needs ~800 s and would squash the plot)
    series = [("fixed MCS3 (16-FSK)", AQUA, "fixed MCS3"),
              ("fixed MCS4 (fastest)", YELLOW, "fixed MCS4 (fastest)"), ("adaptive", INK, "ADAPTIVE (ours)")]
    for name, col, lbl in series:
        rr = sorted([r for r in rows if r.get("experiment") == "walk" and r["strategy"] == name], key=lambda r: r["t"])
        if not rr:
            continue
        x = [0] + [r["t"] for r in rr]
        y = [0] + [r["bytes"] / 1000 for r in rr]
        done = bool(result[name]["goodput_bps"]) if name in result else y[-1] >= total_kb - 1e-6
        a1.step(x, y, where="post", color=col, lw=2.6 if name == "adaptive" else 1.8,
                label=lbl + (f": done after {x[-1]:.0f} s" if done else ": GAVE UP (link failed)"),
                zorder=4 if name == "adaptive" else 2)
        a1.plot(x[-1], y[-1], marker="o" if done else "X", color=col, ms=7 if done else 10, mec=INK if not done else col)
    a1.set_xlabel("time (s)")
    a1.set_ylabel(f"file received (KB of {total_kb:.0f} KB)")
    a1.legend(loc="lower right")
    a1.set_xlim(0, t_end)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig8_adaptive_walk.png"))
    plt.close(fig)
    print("  fig8_adaptive_walk.png")


def fig_routing(plt, out):
    """fig9: distance-vector convergence and count-to-infinity"""
    rows = _read(os.path.join(ROOT, "results", "sim", "routing_sim.csv"))
    if not rows:
        print("  (no routing_sim.csv - run `python -m acousticnet simulate routing`)")
        return
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(15, 4.6), gridspec_kw={"width_ratios": [1, 1.15, 1]})
    conv = [r for r in rows if r["experiment"] == "converge"]
    n = [int(str(r["topology"]).split()[-1]) for r in conv]
    a1.plot(n, [k - 1 for k in n], color=GREY, ls="--", lw=1.6, label="theory: N - 1 rounds (one hop per advert)")
    a1.plot(n, [r["round"] for r in conv], color=BLUE, ls="none", marker="o", ms=8,
            label="measured with our routing code")
    a1.set_xlabel("laptops in a line (N)")
    a1.set_ylabel("advert rounds")
    a1.set_title("Cold start: rounds until all\nlaptops can reach each other")
    a1.legend(loc="upper left")
    for pr, col, ls, mk, lbl in (("False", ORANGE, "--", "s", "poison reverse OFF"),
                                 ("True", BLUE, "-", "o", "poison reverse ON")):
        rr = sorted([r for r in rows if r["experiment"] == "line" and str(r["poison_reverse"]) == pr],
                    key=lambda r: r["round"])
        a2.plot([r["round"] for r in rr], [r["cost"] for r in rr], color=col, ls=ls, marker=mk, ms=5, label=lbl)
    a2.axhline(16, color=INK2, lw=1, ls=":")
    a2.text(15.5, 15.6, "16 = infinity (unreachable), as in RIP", color=INK2, fontsize=8.5, va="top", ha="right")
    a2.set_xlabel("advert rounds after B is switched off")
    a2.set_ylabel("cheapest route to B still believed (hops)")
    a2.set_ylim(0, 17.5)
    a2.set_title("A - R - B, B dies: without poison reverse\nA and R fool each other for 14 rounds")
    a2.legend(loc="center right")
    stats = {(r["topology"], str(r["poison_reverse"])): r for r in rows if str(r["experiment"]).endswith("_stats")}
    x = np.arange(2)
    for k, (pr, col, lbl) in enumerate((("True", BLUE, "poison reverse ON"), ("False", ORANGE, "poison reverse OFF"))):
        vals = [stats[(t, pr)]["round"] for t in ("line", "triangle")]
        frac = [stats[(t, pr)]["cost"] for t in ("line", "triangle")]
        bars = a3.bar(x + (k - 0.5) * 0.36, vals, 0.34, color=col, label=lbl)
        for bar, v, f in zip(bars, vals, frac):
            a3.text(bar.get_x() + bar.get_width() / 2, v + 0.3, f"{v:.1f}\n({int(round(f * 50))}/50 count up)",
                    ha="center", va="bottom", fontsize=8, color=INK2)
    a3.set_xticks(x)
    a3.set_xticklabels(["loop of 2 laptops\n(A - R - B line)", "loop of 3 laptops\n(triangle)"])
    a3.set_ylabel("rounds until everyone knows (mean of 50)")
    a3.set_ylim(0, 19)
    a3.set_title("Poison reverse fixes 2-laptop loops,\nnot 3-laptop loops")
    a3.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig9_routing.png"))
    plt.close(fig)
    print("  fig9_routing.png")


def fig_ber_measured(plt, out):
    rows = _read(os.path.join(ROOT, "results", "measured", "ber_measured.csv"))
    rows = [r for r in rows if isinstance(r.get("distance_m"), float)]
    if not rows:
        print("  (no measured BER yet - run ber-tx / ber-rx on two laptops to create fig6)")
        return
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.3))
    groups = sorted({(int(r["baud"]), str(r["fec"])) for r in rows})
    palette = [BLUE, ORANGE, AQUA, YELLOW]
    for (baud, fec), col in zip(groups, palette):
        rr = sorted([r for r in rows if int(r["baud"]) == baud and str(r["fec"]) == fec], key=lambda r: r["distance_m"])
        lbl = f"{baud} baud" + (", FEC" if fec == "True" else "")
        a1.plot([r["distance_m"] for r in rr], [max(r["ber"], 1e-5) for r in rr], color=col, marker="o", label=lbl)
        a2.plot([r["distance_m"] for r in rr], [100 * r["frame_success_rate"] for r in rr], color=col, marker="o", label=lbl)
    a1.set_yscale("log")
    if any(r["ber"] == 0 for r in rows):          # log axis can't show 0: say so instead of hiding it
        a1.axhline(1e-5, color=GREY, linestyle=":", linewidth=1)
        a1.text(0.01, 1.15e-5, "no bit errors at all (drawn at 1e-5)", transform=a1.get_yaxis_transform(),
                color=INK2, fontsize=8)
    a1.set_xlabel("distance between laptops (m)")
    a1.set_ylabel("bit error rate (frames that were heard)")
    a1.set_title("Measured: bit error rate vs distance")
    a2.set_xlabel("distance between laptops (m)")
    a2.set_ylabel("frames received correctly (%)")
    a2.set_title("Measured: frame success vs distance")
    a1.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out, "fig6_ber_distance_measured.png"))
    plt.close(fig)
    print("  fig6_ber_distance_measured.png")


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _style(plt)
    out = os.path.join(ROOT, "results", "figures")
    os.makedirs(out, exist_ok=True)
    print("drawing figures ->", out)
    fig_mac(plt, out)
    fig_arq(plt, out)
    fig_phy(plt, out)
    fig_errdetect(plt, out)
    fig_ber_measured(plt, out)
    fig_speeds(plt, out)
    fig_walk(plt, out)
    fig_routing(plt, out)


if __name__ == "__main__":
    main()
