"""Offline generator for the bundled *default* drum kit.

Real, permissively-licensed samples can be dropped into ``kits/<name>/`` at any
time (see ``kits/README.md`` for the folder format); the sampler scans whatever
is on disk. When no real samples are available, this script renders a set of
high-quality placeholder one-shots **once, offline** so the pipeline ships a
working kit out of the box. Re-run it to regenerate the placeholders::

    python kits/generate_default_kit.py

The output layout matches what :mod:`sampler` expects::

    kits/default/<drum_class>/v<layer>_rr<variant>.wav

with 2 velocity layers x 2 round-robins for kick/snare/hat_closed and a single
one-shot for the remaining classes. Everything here is synthesized from scratch
(no sampled material), so the bundled kit is CC0 / public domain.
"""

import os

import numpy as np
import soundfile as sf
import scipy.signal

SR = 44100
KIT_ROOT = os.path.join(os.path.dirname(__file__), "default")


def _write(drum_class, layer, variant, mono):
    mono = mono.astype(np.float32)
    peak = float(np.max(np.abs(mono))) or 1.0
    mono = mono / peak * 0.97
    stereo = np.column_stack((mono, mono))
    out_dir = os.path.join(KIT_ROOT, drum_class)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"v{layer}_rr{variant}.wav")
    sf.write(path, stereo, SR, subtype="PCM_16")
    return path


def _noise(n, rng, band=None):
    x = rng.standard_normal(n)
    if band is not None:
        sos = scipy.signal.butter(4, band, btype="bandpass", fs=SR, output="sos")
        x = scipy.signal.sosfiltfilt(sos, x)
    return x


def kick(rng, bright):
    dur = 0.32
    n = int(SR * dur)
    t = np.arange(n) / SR
    base = 52.0 + rng.uniform(-2, 2)
    f_start = base * (3.4 + 0.4 * bright)
    freq = base + (f_start - base) * np.exp(-t * 16)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * (9 + 2 * (1 - bright)))
    sub = np.sin(2 * np.pi * base * t) * np.exp(-t * 7) * 0.5
    click = _noise(n, rng, band=[1500, 5000]) * np.exp(-t * 130) * (0.12 + 0.14 * bright)
    return body * 0.9 + sub + click


def snare(rng, bright):
    dur = 0.24
    n = int(SR * dur)
    t = np.arange(n) / SR
    base = 185.0 + rng.uniform(-6, 6)
    body = (np.sin(2 * np.pi * base * t) + 0.7 * np.sin(2 * np.pi * base * 1.5 * t))
    body *= np.exp(-t * 32)
    hi = min(9000, SR / 2 - 200)
    noise = _noise(n, rng, band=[1500, hi]) * np.exp(-t * (16 + 6 * (1 - bright)))
    return body * 0.45 + noise * (0.7 + 0.25 * bright)


def hat_closed(rng, bright):
    dur = 0.07
    n = int(SR * dur)
    t = np.arange(n) / SR
    lo = 6000
    hi = min(17000, SR / 2 - 200)
    noise = _noise(n, rng, band=[lo, hi])
    return noise * np.exp(-t * (70 - 10 * bright))


def hat_open(rng):
    dur = 0.4
    n = int(SR * dur)
    t = np.arange(n) / SR
    hi = min(17000, SR / 2 - 200)
    noise = _noise(n, rng, band=[6000, hi])
    return noise * np.exp(-t * 7)


def tom(rng, base):
    dur = 0.3
    n = int(SR * dur)
    t = np.arange(n) / SR
    f_start = base * 1.6
    freq = base + (f_start - base) * np.exp(-t * 14)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    body = np.sin(phase) * np.exp(-t * 9)
    noise = _noise(n, rng, band=[base, base * 4]) * np.exp(-t * 22) * 0.12
    return body * 0.9 + noise


def crash(rng):
    dur = 1.6
    n = int(SR * dur)
    t = np.arange(n) / SR
    hi = min(18000, SR / 2 - 200)
    noise = _noise(n, rng, band=[3000, hi])
    shimmer = _noise(n, rng, band=[8000, hi]) * 0.4
    env = np.exp(-t * 2.4)
    attack = np.minimum(1.0, t / 0.005)
    return (noise + shimmer) * env * attack


def ride(rng):
    dur = 0.9
    n = int(SR * dur)
    t = np.arange(n) / SR
    hi = min(14000, SR / 2 - 200)
    ping = (np.sin(2 * np.pi * 520 * t) + 0.6 * np.sin(2 * np.pi * 1150 * t))
    ping *= np.exp(-t * 9) * 0.5
    wash = _noise(n, rng, band=[5000, hi]) * np.exp(-t * 5) * 0.35
    return ping + wash


def main():
    rng = np.random.default_rng(1234)
    # kick / snare / hat_closed: 2 velocity layers x 2 round-robins.
    for layer, bright in ((1, 0.0), (2, 1.0)):
        for variant in (1, 2):
            _write("kick", layer, variant, kick(rng, bright))
            _write("snare", layer, variant, snare(rng, bright))
            _write("hat_closed", layer, variant, hat_closed(rng, bright))
    # Single-sample classes.
    _write("hat_open", 1, 1, hat_open(rng))
    _write("tom_low", 1, 1, tom(rng, 90.0))
    _write("tom_mid", 1, 1, tom(rng, 140.0))
    _write("tom_high", 1, 1, tom(rng, 200.0))
    _write("crash", 1, 1, crash(rng))
    _write("ride", 1, 1, ride(rng))
    print(f"Wrote default kit to {KIT_ROOT}")


if __name__ == "__main__":
    main()
