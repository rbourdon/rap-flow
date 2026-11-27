"""Offline generator for the ``knock`` class: a knuckle on a solid wooden desk.

The flow layer plays one of these per syllable, so the sound has two jobs: read
as a hand tapping along rather than as a drum, and start *instantly*, because
any rise time in the sample is lateness added to every hit. Each one-shot peaks
within 2 ms of its first sample.

It is modal synthesis, so everything here is generated from scratch (CC0):

* a thick wooden top is a handful of heavily damped modes, low ones ringing a
  little longer than high ones (the "thock");
* the knuckle is a short half-sine force pulse. A harder knock is a shorter
  contact, which is what makes it brighter, not just louder, so the three
  velocity layers differ in tone the way real knocks do;
* a few milliseconds of band-passed noise stand in for skin on wood.

Round-robin variants move the modes and their balance slightly, as hitting a
slightly different spot does. Re-run to regenerate::

    python kits/generate_knock.py
"""

import os

import numpy as np
import scipy.signal
import soundfile as sf

SR = 44100
OUT_DIR = os.path.join(os.path.dirname(__file__), "default", "knock")

# (frequency Hz, decay time s, level) of the desk top's modes.
MODES = [
    (112.0, 0.030, 0.35), (187.0, 0.028, 0.80), (268.0, 0.024, 1.00),
    (371.0, 0.020, 0.90), (498.0, 0.017, 0.80), (655.0, 0.014, 0.70),
    (842.0, 0.011, 0.60), (1120.0, 0.009, 0.50), (1490.0, 0.007, 0.40),
    (2050.0, 0.005, 0.30), (2900.0, 0.004, 0.20),
]

# layer -> (contact time s, skin-noise level): soft, medium, hard.
LAYERS = {1: (0.0018, 0.08), 2: (0.0011, 0.14), 3: (0.0007, 0.22)}
VARIANTS = 4
DRIVE = 2.4


def knock(rng, contact_s, skin, dur_s=0.26):
    n = int(dur_s * SR)
    t = np.arange(n) / SR
    body = np.zeros(n)
    for freq, decay, level in MODES:
        f = freq * (1.0 + rng.uniform(-0.025, 0.025))
        a = level * (1.0 + rng.uniform(-0.2, 0.2))
        # Sine phase, so every mode starts from rest and the sum cannot click.
        body += a * np.sin(2 * np.pi * f * t) * np.exp(-t / (decay * (1.0 + rng.uniform(-0.1, 0.1))))
    pulse = np.sin(np.pi * np.arange(max(2, int(contact_s * SR))) / max(2, int(contact_s * SR))) ** 2
    out = scipy.signal.fftconvolve(body, pulse)[:n]
    out /= np.max(np.abs(out))

    noise = rng.standard_normal(n) * np.exp(-t / 0.0025)
    sos = scipy.signal.butter(2, [1400.0, 5200.0], btype="band", fs=SR, output="sos")
    noise = scipy.signal.sosfilt(sos, noise)
    out += skin * noise / np.max(np.abs(noise))

    # A knock is nearly all peak: at the same peak level as a snare it carries
    # far less energy, so in a mix it is either quiet or leaning on the limiter.
    # Soft-clipping the first couple of milliseconds trades about 4 dB of that
    # peak for body, which is roughly what a close microphone's preamp does.
    out = np.tanh(DRIVE * out / np.max(np.abs(out)))

    out *= np.minimum(1.0, (n - np.arange(n)) / (0.02 * SR))  # fade the tail out
    return out / np.max(np.abs(out)) * 0.97


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    rng = np.random.default_rng(20240607)
    for layer, (contact_s, skin) in LAYERS.items():
        for variant in range(1, VARIANTS + 1):
            mono = knock(rng, contact_s, skin).astype(np.float32)
            path = os.path.join(OUT_DIR, f"v{layer}_rr{variant}.flac")
            sf.write(path, np.column_stack((mono, mono)), SR, format="FLAC", subtype="PCM_16")
            print(path)


if __name__ == "__main__":
    main()
