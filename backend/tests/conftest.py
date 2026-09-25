"""Test fixtures for the percussion pipeline.

``backend/`` is a flat package-less module directory (``worker.py`` adds each
module to the Modal image by name), so the tests put it on ``sys.path`` rather
than importing through a package.
"""

import os
import sys
import tempfile

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


def _claim_numba_cache_dir():
    """A numba cache directory no other live process is using.

    librosa JIT-compiles with numba's on-disk cache (cache=True), which by
    default lives inside the installed librosa package. On a cold cache, the
    pytest-xdist workers all compile the beat tracker into those same files at
    once and segfault (it happened on every fresh CI runner). So each test
    process claims its own slot, holding an exclusive flock on it for its
    lifetime. Slots persist, so later runs start warm; a second pytest run in
    the same container (another agent, say) simply claims other slots.
    """
    base = os.path.join(
        tempfile.gettempdir(), "rap-flow-numba-cache", "py%d%d" % sys.version_info[:2]
    )
    if fcntl is not None:
        os.makedirs(base, exist_ok=True)
        for slot in range(256):
            path = os.path.join(base, "slot-%d" % slot)
            lock = open(path + ".lock", "w")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                lock.close()
                continue
            return path, lock  # the open handle keeps the slot ours
    return tempfile.mkdtemp(prefix="rap-flow-numba-"), None


# Unconditional on purpose: xdist workers inherit the controller's
# environment, so "only if unset" would put them all back in one directory.
# Must run before anything imports numba.
os.environ["NUMBA_CACHE_DIR"], _NUMBA_CACHE_LOCK = _claim_numba_cache_dir()

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import soundfile as sf  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


SR = 44100


def _noise_burst(n, sr, decay_s, lowpass=None, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    x = rng.standard_normal(n) * np.exp(-t / decay_s)
    if lowpass:
        import scipy.signal
        sos = scipy.signal.butter(4, lowpass, btype="low", fs=sr, output="sos")
        x = scipy.signal.sosfilt(sos, x)
    return x


def synth_drums(path, pattern, duration_s, sr=SR):
    """Write a synthetic ``drums.wav`` with a known kick/snare pattern.

    ``pattern`` is a list of ``(time_seconds, "kick"|"snare")``. Kicks are a
    decaying 55 Hz sine, snares a band-limited noise burst, so the band-split
    transcriber in :mod:`backbone` has something unambiguous to classify.
    """
    y = np.zeros(int(duration_s * sr), dtype=np.float64)
    for t, kind in pattern:
        idx = int(t * sr)
        if kind == "kick":
            n = int(0.18 * sr)
            tt = np.arange(n) / sr
            hit = np.sin(2 * np.pi * 55.0 * tt) * np.exp(-tt / 0.055)
        else:
            n = int(0.16 * sr)
            hit = _noise_burst(n, sr, 0.045, seed=idx)
            tt = np.arange(n) / sr
            hit = hit + 0.6 * np.sin(2 * np.pi * 220.0 * tt) * np.exp(-tt / 0.04)
        end = min(len(y), idx + n)
        if end > idx:
            y[idx:end] += hit[:end - idx]
    peak = float(np.max(np.abs(y))) or 1.0
    sf.write(path, (y / peak * 0.9), sr)
    return path


def synth_instrumental(path, tempo_bpm, duration_s, sr=SR):
    """Write a metronomic instrumental so ``rhythm.track_beats`` locks on."""
    beat = 60.0 / tempo_bpm
    y = np.zeros(int(duration_s * sr), dtype=np.float64)
    t = 0.0
    i = 0
    while t < duration_s - 0.2:
        idx = int(t * sr)
        n = int(0.09 * sr)
        tt = np.arange(n) / sr
        # Accent the downbeat so estimate_downbeat_offset has a phase to find.
        amp = 1.0 if i % 4 == 0 else 0.55
        hit = amp * np.sin(2 * np.pi * 180.0 * tt) * np.exp(-tt / 0.02)
        end = min(len(y), idx + n)
        y[idx:end] += hit[:end - idx]
        t += beat
        i += 1
    # A quiet pad so the stem is not pure silence between hits.
    tt = np.arange(len(y)) / sr
    y += 0.03 * np.sin(2 * np.pi * 110.0 * tt)
    peak = float(np.max(np.abs(y))) or 1.0
    sf.write(path, (y / peak * 0.8), sr)
    return path


@pytest.fixture
def events():
    """A small syllable-event stream: voiced nuclei plus two consonants."""
    out = []
    for i in range(12):
        out.append({
            "t": 0.5 + i * 0.1873,
            "strength": 0.3 + 0.05 * (i % 6),
            "f0": 140.0,
            "periodicity": 0.8,
            "dur": 0.15,
            "kind": "nucleus",
            "subtype": "voiced",
            "stress": 0.2 + 0.07 * (i % 9),
        })
    out.append({
        "t": 1.21, "strength": 0.5, "f0": 0.0, "periodicity": 0.1, "dur": 0.09,
        "kind": "transient", "subtype": "sibilant", "stress": 0.4,
    })
    out.append({
        "t": 1.93, "strength": 0.7, "f0": 0.0, "periodicity": 0.05, "dur": 0.02,
        "kind": "transient", "subtype": "plosive", "stress": 0.6,
    })
    out.sort(key=lambda e: e["t"])
    return out
