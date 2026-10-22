"""
theory.py - textbook throughput formulas for random-access MAC protocols.

Notation (Forouzan ch. 12, Kurose ch. 6, Kleinrock & Tobagi 1975):
    G  = offered load   = frames attempted per frame-time
    S  = throughput     = frames successfully delivered per frame-time  (0..1)
    N  = number of stations
    a  = (sensing delay) / (frame time)   - for CSMA

Classic formulas assume INFINITELY many stations:
    pure ALOHA       S = G e^(-2G)          peak 1/(2e) = 18.4 %  at G = 0.5
    slotted ALOHA    S = G e^(-G)           peak 1/e    = 36.8 %  at G = 1
With only N stations (our lab: N = 3) the exact formulas are:
    slotted ALOHA    S = G (1 - G/N)^(N-1)        peak (1-1/N)^(N-1)      N=3: 44.4 %
    pure ALOHA       S = G (1 - G/N)^(2(N-1))     peak at G = N/(2N-1)    N=3: 24.6 %
(a frame survives if each of the other N-1 stations stays silent for 1 slot, or for the
 2-frame "vulnerable period" in pure ALOHA).  Comparing 3 laptops with the infinite-station
numbers would make the experiment look like it "beats theory" - it doesn't.

Non-persistent CSMA (Kleinrock & Tobagi):
    S = G e^(-aG) / ( G(1 + 2a) + e^(-aG) )
For radio, a is tiny (speed of light).  For laptops talking through sound, a is dominated by
AUDIO LATENCY (buffers in the sound card and OS: ~50-300 ms), not by the speed of sound
(1 m = 3 ms).  That is a key finding of this project.
"""
import numpy as np


def aloha_pure(G):
    G = np.asarray(G, dtype=float)
    return G * np.exp(-2 * G)


def aloha_slotted(G):
    G = np.asarray(G, dtype=float)
    return G * np.exp(-G)


def aloha_slotted_finite(G, N):
    G = np.asarray(G, dtype=float)
    return np.where(G <= N, G * np.clip(1 - G / N, 0, 1) ** (N - 1), 0.0)


def aloha_pure_finite(G, N):
    G = np.asarray(G, dtype=float)
    return np.where(G <= N, G * np.clip(1 - G / N, 0, 1) ** (2 * (N - 1)), 0.0)


def csma_nonpersistent(G, a):
    G = np.asarray(G, dtype=float)
    e = np.exp(-a * G)
    return G * e / (G * (1 + 2 * a) + e)


def peak(fn, *args, gmax=10.0):
    """(G_at_peak, S_peak) found numerically"""
    G = np.linspace(1e-3, gmax, 20001)
    S = fn(G, *args)
    i = int(np.argmax(S))
    return float(G[i]), float(S[i])
