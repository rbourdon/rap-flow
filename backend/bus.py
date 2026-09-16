"""Percussion bus DSP — the glue that puts the kit *inside* the record.

The old render summed peak-normalized one-shots straight onto a mastered
instrumental, ducked the low band, normalized to -14 LUFS and, if the result
peaked, multiplied the **whole mix** down by the overshoot. Dry samples at
native pitch sitting on top of a finished record is exactly what "the drums
sound bolted on" means, and a single gain multiply is not a limiter — it
quietens everything the moment one drum peaks.

This module is the missing sound layer, in the order it is applied by
:func:`pipeline.sample_render`:

1. :func:`transient_envelope` — per-class attack emphasis / decay shortening,
   applied to the one-shot buffer itself (cheap, phase-safe).
2. :func:`saturate` — ``tanh`` soft clip with output gain compensation.
3. :func:`room_send` — a short, **fixed-seed** early-reflection IR. The
   pipeline is content-addressed and cache-reused, so a random IR would make
   identical inputs produce different renders.
4. :func:`layer_gains` — equal-power flow/bed balance.
5. :func:`glue_compress` — feed-forward peak compressor on the summed bus.
6. :func:`peaking_eq` — the static carve under the kit's kick in the bed.
7. :func:`true_peak_limit` — 4x oversampled true-peak limiter with lookahead.

Everything here is offline numpy/scipy DSP: no new models, no new services.
"""

import logging

import numpy as np
import scipy.ndimage
import scipy.signal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Per-class transient shaping
# ---------------------------------------------------------------------------

# ``attack_gain`` is the multiplier at t=0, decaying over ``attack_ms``;
# ``decay_ms`` (when set) is the -26 dB point of an added exponential decay,
# which is how a long sampled hat is made to read as a tight one.
TRANSIENT_SHAPES = {
    "kick":       {"attack_ms": 8.0, "attack_gain": 1.35, "decay_ms": None},
    "snare":      {"attack_ms": 8.0, "attack_gain": 1.45, "decay_ms": None},
    "hat_closed": {"attack_ms": 6.0, "attack_gain": 1.60, "decay_ms": 140.0},
    "hat_open":   {"attack_ms": 8.0, "attack_gain": 1.30, "decay_ms": 420.0},
    "ride":       {"attack_ms": 8.0, "attack_gain": 1.40, "decay_ms": 520.0},
    "crash":      {"attack_ms": 10.0, "attack_gain": 1.20, "decay_ms": None},
    "tom_low":    {"attack_ms": 8.0, "attack_gain": 1.25, "decay_ms": None},
    "tom_mid":    {"attack_ms": 8.0, "attack_gain": 1.25, "decay_ms": None},
    "tom_high":   {"attack_ms": 8.0, "attack_gain": 1.25, "decay_ms": None},
}

_DEFAULT_SHAPE = {"attack_ms": 8.0, "attack_gain": 1.25, "decay_ms": None}


def transient_envelope(n_samples: int, sr: int, drum_class: str):
    """Return the per-class shaping envelope for an ``n_samples`` one-shot."""
    shape = TRANSIENT_SHAPES.get(drum_class, _DEFAULT_SHAPE)
    if n_samples <= 0:
        return np.ones(0, dtype=np.float32)
    t = np.arange(n_samples, dtype=np.float64) / float(sr)
    env = np.ones(n_samples, dtype=np.float64)

    attack_gain = float(shape.get("attack_gain", 1.0))
    attack_ms = float(shape.get("attack_ms", 0.0))
    if attack_gain != 1.0 and attack_ms > 0:
        tau = (attack_ms / 1000.0) / 3.0
        env *= 1.0 + (attack_gain - 1.0) * np.exp(-t / tau)

    decay_ms = shape.get("decay_ms")
    if decay_ms:
        tau_d = (float(decay_ms) / 1000.0) / 3.0
        env *= np.exp(-t / tau_d)

    return env.astype(np.float32)


def shape_sample(sample, sr: int, drum_class: str):
    """Apply :func:`transient_envelope` to a stereo one-shot buffer."""
    if sample is None or sample.size == 0:
        return sample
    env = transient_envelope(len(sample), sr, drum_class)
    return (sample * env[:, np.newaxis]).astype(np.float32)


# ---------------------------------------------------------------------------
# 2. Saturation
# ---------------------------------------------------------------------------

def _rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if x.size else 0.0


def saturate(x, drive: float = 1.6):
    """``tanh(x*drive)/tanh(drive)`` with output gain matched to input RMS.

    Gain compensation matters: without it the drive control doubles as a volume
    control and every other level in the chain has to be re-dialled.
    """
    if x is None or x.size == 0 or drive is None or drive <= 0:
        return x
    drive = float(drive)
    in_rms = _rms(x)
    out = np.tanh(x * drive) / np.tanh(drive)
    out_rms = _rms(out)
    if in_rms > 0 and out_rms > 0:
        out = out * (in_rms / out_rms)
    return out.astype(np.float32)


# ---------------------------------------------------------------------------
# 3. Room send
# ---------------------------------------------------------------------------

# Fixed seed: identical inputs must produce an identical render, because the
# workflow caches renders by a content-derived key.
ROOM_SEED = 20260915


def room_ir(sr: int, rt60: float = 0.180, predelay: float = 0.012,
            seed: int = ROOM_SEED):
    """Synthesize a short early-reflection IR (deterministic for ``seed``).

    Exponentially decaying noise, thinned out so it reads as a handful of early
    reflections rather than a reverb wash, with a 12 ms pre-delay to keep the
    dry attack in front. Energy-normalized so the wet level is the only control.
    """
    rng = np.random.default_rng(seed)
    n = max(8, int(sr * (predelay + rt60 * 1.5)))
    t = np.arange(n, dtype=np.float64) / float(sr)
    pre = int(sr * predelay)

    decay = 10.0 ** (-3.0 * np.maximum(0.0, t - predelay) / max(rt60, 1e-3))
    decay[:pre] = 0.0

    ir = rng.standard_normal((n, 2))
    # Thin the tail: reflection density falls off with time, which is what makes
    # this read as a small room instead of a plate.
    density = np.clip(1.0 - np.maximum(0.0, t - predelay) / max(rt60, 1e-3), 0.12, 1.0)
    mask = rng.random((n, 2)) < density[:, np.newaxis]
    ir = ir * mask * decay[:, np.newaxis]

    energy = float(np.sqrt(np.sum(np.square(ir))))
    if energy > 0:
        ir = ir / energy
    return ir.astype(np.float32)


def room_send(x, sr: int, wet: float = 0.14, ir=None):
    """Mix a convolved room send into ``x`` at ``wet`` level."""
    if x is None or x.size == 0 or not wet:
        return x
    if ir is None:
        ir = room_ir(sr)
    out = np.array(x, dtype=np.float32, copy=True)
    for ch in range(min(x.shape[1], ir.shape[1])):
        conv = scipy.signal.fftconvolve(x[:, ch], ir[:, ch], mode="full")[:len(x)]
        out[:, ch] += (float(wet) * conv).astype(np.float32)
    return out


# ---------------------------------------------------------------------------
# 4. Layer balance
# ---------------------------------------------------------------------------

def layer_gains(balance: float):
    """Equal-power flow/bed gains for ``balance`` in [0, 1].

    0 = all syllable flow, 1 = all beat backbone. Equal power means the *total*
    percussion level stays put across the sweep, so the control changes the
    balance and nothing else.
    """
    b = float(np.clip(balance if balance is not None else 0.5, 0.0, 1.0))
    angle = b * np.pi / 2.0
    return float(np.cos(angle)), float(np.sin(angle))


# ---------------------------------------------------------------------------
# 5. Bus glue compressor
# ---------------------------------------------------------------------------

def glue_compress(x, sr: int, threshold_db: float = -14.0, ratio: float = 3.0,
                  attack_ms: float = 8.0, release_ms: float = 120.0,
                  makeup: bool = True):
    """Feed-forward peak compressor on the summed percussion bus.

    The gain envelope is computed on 1 ms blocks and interpolated back up, which
    keeps the asymmetric attack/release loop cheap on a full-length track while
    staying well inside the 8 ms attack it is asked for.
    """
    if x is None or x.size == 0 or ratio is None or ratio <= 1.0:
        return x

    peak = np.max(np.abs(x), axis=1) if x.ndim > 1 else np.abs(x)
    block = max(1, int(sr * 0.001))
    nb = len(peak) // block
    if nb < 2:
        return x

    blocks = peak[:nb * block].reshape(nb, block).max(axis=1)
    thr = 10.0 ** (float(threshold_db) / 20.0)
    over_db = 20.0 * np.log10(np.maximum(blocks, 1e-12) / max(thr, 1e-12))
    target = np.where(over_db > 0.0, -over_db * (1.0 - 1.0 / float(ratio)), 0.0)

    a_att = float(np.exp(-block / max(sr * attack_ms / 1000.0, 1e-9)))
    a_rel = float(np.exp(-block / max(sr * release_ms / 1000.0, 1e-9)))
    gr_db = np.empty(nb, dtype=np.float64)
    g = 0.0
    for i in range(nb):
        tgt = target[i]
        coeff = a_att if tgt < g else a_rel
        g = coeff * g + (1.0 - coeff) * tgt
        gr_db[i] = g

    gain = 10.0 ** (gr_db / 20.0)
    gain_full = np.interp(
        np.arange(len(peak), dtype=np.float64) / block,
        np.arange(nb, dtype=np.float64),
        gain,
    )
    out = x * gain_full[:, np.newaxis] if x.ndim > 1 else x * gain_full

    if makeup:
        in_rms, out_rms = _rms(x), _rms(out)
        if in_rms > 0 and out_rms > 0:
            out = out * (in_rms / out_rms)
    return out.astype(np.float32)


# ---------------------------------------------------------------------------
# 6. Peaking EQ (the static carve under the kit's kick)
# ---------------------------------------------------------------------------

def peaking_eq(x, sr: int, freq: float, gain_db: float, q: float = 1.4):
    """RBJ peaking EQ, applied along the sample axis."""
    if x is None or x.size == 0 or not gain_db:
        return x
    freq = float(np.clip(freq, 20.0, sr / 2.0 - 20.0))
    A = 10.0 ** (float(gain_db) / 40.0)
    w0 = 2.0 * np.pi * freq / sr
    alpha = np.sin(w0) / (2.0 * max(q, 1e-3))
    cosw = np.cos(w0)
    b = np.array([1 + alpha * A, -2 * cosw, 1 - alpha * A])
    a = np.array([1 + alpha / A, -2 * cosw, 1 - alpha / A])
    sos = np.concatenate([b / a[0], [1.0], a[1:] / a[0]])[np.newaxis, :]
    return scipy.signal.sosfilt(sos, x, axis=0).astype(np.float32)


def dominant_frequency(sample, sr: int, fmin: float = 35.0, fmax: float = 140.0):
    """Dominant frequency of a one-shot's body, used to place the kick carve."""
    if sample is None or sample.size == 0:
        return None
    mono = sample.mean(axis=1) if sample.ndim > 1 else sample
    n = min(len(mono), int(sr * 0.120))
    if n < 64:
        return None
    seg = mono[:n] * np.hanning(n)
    spec = np.abs(np.fft.rfft(seg, n=max(4096, n)))
    freqs = np.fft.rfftfreq(max(4096, n), 1.0 / sr)
    band = (freqs >= fmin) & (freqs <= fmax)
    if not np.any(band) or not np.any(spec[band]):
        return None
    return float(freqs[band][int(np.argmax(spec[band]))])


# ---------------------------------------------------------------------------
# 7. True-peak limiter
# ---------------------------------------------------------------------------

def _one_sided_min(x, span: int, forward: bool):
    """``min(x[n : n+span])`` (forward) or ``min(x[n-span : n])`` (backward)."""
    size = span + 1
    if size % 2 == 0:
        size += 1  # scipy's origin offsets are only well-defined for odd sizes
    origin = -(size // 2) if forward else (size // 2)
    return scipy.ndimage.minimum_filter1d(
        x, size=size, origin=origin, mode="nearest"
    )


def _running_min(x, back: int, fwd: int):
    """``out[n] = min(x[n-back : n+fwd])``, O(n) via two one-sided minima.

    The union of the backward and forward windows is the full window, so the
    element-wise minimum of the two one-sided results is exact.
    """
    out = x
    if fwd > 0:
        out = np.minimum(out, _one_sided_min(x, fwd, forward=True))
    if back > 0:
        out = np.minimum(out, _one_sided_min(x, back, forward=False))
    return out


def true_peak_gain(x, sr: int, ceiling_dbtp: float = -1.0,
                   lookahead_ms: float = 1.5, release_ms: float = 50.0,
                   oversample: int = 4):
    """Gain-reduction envelope that holds ``x`` under ``ceiling_dbtp``.

    Returned separately from :func:`true_peak_limit` so the *same* envelope can
    be applied to the mix and to the stems it is made of. The frontend player
    sums the percussion and instrumental stems at unity and expects that sum to
    be the mix, so any gain that is not applied identically to all three would
    break that relationship.
    """
    if x is None or x.size == 0:
        return np.ones(0, dtype=np.float64)

    ceiling = 10.0 ** (float(ceiling_dbtp) / 20.0)
    xf = np.asarray(x, dtype=np.float64)
    mono = np.max(np.abs(xf), axis=1) if xf.ndim > 1 else np.abs(xf)

    # True-peak estimate: the inter-sample peaks an oversampled reconstruction
    # would reveal. Taking the max per original sample keeps the envelope at the
    # base rate.
    os_factor = max(1, int(oversample))
    if os_factor > 1:
        up = np.abs(scipy.signal.resample_poly(mono, os_factor, 1))
        usable = (len(up) // os_factor) * os_factor
        tp = up[:usable].reshape(-1, os_factor).max(axis=1)
        if len(tp) < len(mono):
            tp = np.concatenate([tp, mono[len(tp):]])
        tp = np.maximum(tp[:len(mono)], mono)
    else:
        tp = mono

    need = np.minimum(1.0, ceiling / np.maximum(tp, 1e-12))
    if float(np.min(need)) >= 1.0:
        return np.ones(len(mono), dtype=np.float64)

    look = max(1, int(sr * lookahead_ms / 1000.0))
    rel = max(1, int(sr * release_ms / 1000.0))
    gain = _running_min(need, back=rel, fwd=look)
    # Short smoothing so the gain changes don't click, then clamp back under
    # `need` so smoothing can never reintroduce an overshoot.
    smooth_n = max(3, int(sr * 0.0005))
    kernel = np.ones(smooth_n) / smooth_n
    return np.minimum(np.convolve(gain, kernel, mode="same"), need)


def true_peak_limit(x, sr: int, ceiling_dbtp: float = -1.0,
                    lookahead_ms: float = 1.5, release_ms: float = 50.0,
                    oversample: int = 4):
    """4x oversampled true-peak limiter with lookahead and a release envelope.

    This replaces the old "if the mix peaks, multiply the whole mix down"
    behaviour: gain reduction is applied only where the true peak actually
    exceeds the ceiling, and it recovers over ``release_ms``. The output is
    hard-clamped to the ceiling as a last resort, so the returned buffer is
    guaranteed to contain no sample above it.
    """
    if x is None or x.size == 0:
        return x
    ceiling = 10.0 ** (float(ceiling_dbtp) / 20.0)
    xf = np.asarray(x, dtype=np.float64)
    gain = true_peak_gain(xf, sr, ceiling_dbtp, lookahead_ms, release_ms,
                          oversample)
    out = xf * gain[:, np.newaxis] if xf.ndim > 1 else xf * gain
    return np.clip(out, -ceiling, ceiling).astype(np.float32)
