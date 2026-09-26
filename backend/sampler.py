"""Velocity-layered, round-robin real-sample drum playback engine.

This is the *sound* layer of the new percussion pipeline. It takes a drum score
(a list of ``{t, midi_note, velocity, drum_class}`` events produced by the
``groove`` stage) and renders it to audio by playing back real one-shot samples
from a kit on disk. It replaces the old synthesis / stem-slicing / per-hit
pitch-shifting code path entirely: samples play at their native pitch (the only
resampling is the tiny pseudo-round-robin varispeed).

Kit layout on disk::

    kits/<kit_name>/<drum_class>/v<layer>_rr<variant>.[wav|flac]

e.g. ``kits/default/kick/v1_rr1.wav``, ``kits/default/kick/v3_rr2.wav``. The
nine drum classes are ``kick, snare, hat_closed, hat_open, tom_low, tom_mid,
tom_high, crash, ride``. ``v<layer>`` is the velocity layer (``v1`` softest);
``rr<variant>`` is a round-robin variant. The loader scans the directory, so any
conforming folder works and real samples can be dropped in over the placeholder
one-shots.

Playback rules:
  * **Velocity layers** — the MIDI velocity picks the nearest layer (by its
    nominal velocity), and overall loudness is then fine-scaled with gain. When a
    class only has a single layer, soft hits are additionally darkened with a
    gentle one-pole lowpass so a quiet hit doesn't just sound like a turned-down
    loud hit.
  * **Round-robin** — the ``rr`` variants of a class are cycled and the same
    variant is never used twice in a row. With only one variant, a ±3% random
    varispeed (resampling, not phase vocoding) stands in as pseudo-round-robin so
    no two consecutive hits are bit-identical.
  * **Choke groups** — a ``hat_closed`` or ``kick`` event chokes any still
    ringing ``hat_open`` with a fast 10 ms fade of its tail.
"""

import os
import glob
import logging

import numpy as np
import soundfile as sf
import scipy.signal

logger = logging.getLogger(__name__)


# The nine drum classes the sampler understands, and the General MIDI note each
# maps to (used for MIDI export and for mapping a drum score's midi_note back to
# a class when only the note is known).
DRUM_CLASSES = [
    "kick", "snare", "hat_closed", "hat_open",
    "tom_low", "tom_mid", "tom_high", "crash", "ride",
]

CLASS_TO_MIDI = {
    "kick": 36,
    "snare": 38,
    "hat_closed": 42,
    "hat_open": 46,
    "tom_low": 45,
    "tom_mid": 48,
    "tom_high": 50,
    "crash": 49,
    "ride": 51,
}

MIDI_TO_CLASS = {v: k for k, v in CLASS_TO_MIDI.items()}

# Classes whose onset chokes a ringing open hat.
_CHOKE_TRIGGERS = ("hat_closed", "kick")

DEFAULT_KIT_DIR = os.path.join(os.path.dirname(__file__), "kits", "default")


def _to_stereo(mono):
    """Duplicate a mono buffer into an ``(n, 2)`` stereo buffer."""
    return np.column_stack((mono, mono))


def _normalize_peak(sample, target=0.98):
    """Peak-normalize a sample so loudness is driven purely by playback gain."""
    peak = float(np.max(np.abs(sample))) if sample.size else 0.0
    if peak > 0:
        sample = sample / peak * target
    return sample


# A sample's attack is its first frame above this fraction of its peak (-40 dB).
_ONSET_THRESHOLD = 0.01


def _onset_lead(sample):
    """Frames of lead-in before a one-shot's attack.

    Real multi-sampled kits are cut loosely: the bundled Salamander samples
    start 0-17 ms before the hit, by a different amount per velocity layer and
    round-robin variant. Placed from their first frame, every hit landed late
    and cycling the variants jittered it. :func:`render_drum_score` places each
    one-shot this many frames *early* instead, so the attack lands on the note.

    The sample itself is left alone on purpose. Trimming it (the first version
    of this fix) also moved the transient shaper in :mod:`bus`, whose settings
    were tuned by ear on these untrimmed samples: its attack boost and hat decay
    had been landing on the lead-in, and trimmed they hit the real attack, up to
    +5 dB on the closed hats. Shifting the placement changes *when* a hit sounds,
    not how it sounds.
    """
    env = np.max(np.abs(sample), axis=1) if sample.size else np.zeros(0)
    if not env.size or float(env.max()) <= 0:
        return 0
    return int(np.argmax(env > float(env.max()) * _ONSET_THRESHOLD))


def _load_audio(path, target_sr):
    """Load a WAV or FLAC as stereo float32 at ``target_sr`` (peak-normalized)."""
    data, file_sr = sf.read(path, dtype="float32")
    if data.ndim == 1:
        data = _to_stereo(data)
    elif data.shape[1] == 1:
        data = _to_stereo(data[:, 0])
    elif data.shape[1] > 2:
        data = data[:, :2]
    if file_sr != target_sr:
        # Varispeed-style resample to the render rate. This runs once per sample
        # at load, not per hit.
        from fractions import Fraction
        frac = Fraction(target_sr, file_sr).limit_denominator(1000)
        up, down = frac.numerator, frac.denominator
        data = np.column_stack([
            scipy.signal.resample_poly(data[:, ch], up, down)
            for ch in range(data.shape[1])
        ]).astype(np.float32)
    return _normalize_peak(data)


def _one_pole_lowpass(sample, sr, cutoff_hz):
    """Gentle one-pole lowpass used to darken soft single-layer hits."""
    if cutoff_hz <= 0 or cutoff_hz >= sr / 2:
        return sample
    dt = 1.0 / sr
    rc = 1.0 / (2 * np.pi * cutoff_hz)
    a = dt / (rc + dt)
    out = np.empty_like(sample)
    for ch in range(sample.shape[1]):
        out[:, ch] = scipy.signal.lfilter([a], [1.0, -(1.0 - a)], sample[:, ch])
    return out


def _varispeed(sample, ratio):
    """Resample (varispeed) a stereo sample by ``ratio`` (length *= 1/ratio)."""
    from fractions import Fraction
    frac = Fraction(1.0 / ratio).limit_denominator(400)
    up, down = frac.numerator, frac.denominator
    if up <= 0 or down <= 0:
        return sample
    return np.column_stack([
        scipy.signal.resample_poly(sample[:, ch], up, down)
        for ch in range(sample.shape[1])
    ]).astype(np.float32)


class DrumKit:
    """A loaded kit: velocity layers and round-robin variants per drum class."""

    def __init__(self, sr):
        self.sr = sr
        # class -> list of layers (soft->loud); each layer is a list of variant
        # sample arrays (stereo float32 at self.sr).
        self.layers = {}

    @classmethod
    def load(cls, kit_dir, sr):
        """Scan ``kit_dir`` and load every ``v<layer>_rr<variant>.[wav|flac]``."""
        kit = cls(sr)
        for drum_class in DRUM_CLASSES:
            class_dir = os.path.join(kit_dir, drum_class)
            if not os.path.isdir(class_dir):
                continue
            # Group files by layer number, then order variants within a layer.
            by_layer = {}
            for path in sorted(glob.glob(os.path.join(class_dir, "v*_rr*.wav")) + glob.glob(os.path.join(class_dir, "v*_rr*.flac"))):
                name = os.path.splitext(os.path.basename(path))[0]
                try:
                    layer_str, rr_str = name.split("_")
                    layer_n = int(layer_str[1:])
                    rr_n = int(rr_str[2:])
                except (ValueError, IndexError):
                    logger.warning("Skipping malformed kit file name: %s", path)
                    continue
                by_layer.setdefault(layer_n, {})[rr_n] = path
            if not by_layer:
                continue
            layers = []
            for layer_n in sorted(by_layer):  # soft -> loud
                variants = [by_layer[layer_n][rr] for rr in sorted(by_layer[layer_n])]
                layers.append([_load_audio(p, sr) for p in variants])
            kit.layers[drum_class] = layers
        if not kit.layers:
            raise FileNotFoundError(
                f"No usable drum samples found under {kit_dir!r}. Expected files "
                f"like <drum_class>/v1_rr1.wav or .flac."
            )
        return kit

    def has(self, drum_class):
        return drum_class in self.layers

    def num_layers(self, drum_class):
        return len(self.layers.get(drum_class, []))

    def pick_layer(self, drum_class, velocity):
        """Return the index of the layer whose nominal velocity is nearest."""
        n = self.num_layers(drum_class)
        if n <= 1:
            return 0
        # Nominal velocity for layer i (0-based, soft->loud): evenly spaced across
        # the usable range so the softest layer answers low velocities.
        nominals = [127.0 * (i + 1) / n for i in range(n)]
        return int(np.argmin([abs(velocity - nv) for nv in nominals]))


def _velocity_gain(velocity):
    """Perceptual playback gain for a MIDI velocity (1-127)."""
    v = max(1, min(127, int(velocity))) / 127.0
    return float(min(0.98, 0.18 + 0.82 * (v ** 0.7)))


DEFAULT_LAYER = "bed"


def render_drum_score(drum_score, instrumental_len, sr, kit,
                      rr_state=None, rng=None, shaper=None,
                      split_layers=False):
    """Render a drum score to a stereo percussion track.

    ``drum_score`` is a list of ``{t, midi_note, velocity, drum_class}`` dicts,
    optionally carrying ``layer`` ("flow" | "bed") and ``source``.
    ``instrumental_len`` sizes the output buffer (extra tail is appended for the
    longest sample). Returns ``(perc_track, placed)`` where ``placed`` is a list
    of ``{t, midi_note, velocity, drum_class, layer, source}`` in play order (for
    MIDI export and for the bed-driven ducking envelope — the layer tag has to
    survive to the render stage, because flow ghosts are snares and must not
    pump the instrumental). Round-robin cursors persist in ``rr_state`` (a dict)
    if supplied.

    ``shaper(sample, drum_class) -> sample`` is applied to each one-shot buffer
    before placement (per-class transient shaping; see :mod:`bus`).

    With ``split_layers=True``, ``perc_track`` is a ``{layer: buffer}`` dict
    instead of a single buffer, so the two layers can be processed and balanced
    as separate sub-buses. Choke groups still operate **across** layers.
    """
    if rng is None:
        rng = np.random.default_rng()
    if rr_state is None:
        rr_state = {}

    notes = sorted(drum_score, key=lambda n: float(n["t"]))
    # Size the buffer to fit the last hit plus the longest sample tail.
    max_tail = 0
    for cls_layers in kit.layers.values():
        for layer in cls_layers:
            for variant in layer:
                max_tail = max(max_tail, len(variant))
    last_idx = int(notes[-1]["t"] * sr) if notes else 0
    total_len = max(instrumental_len, last_idx + max_tail)

    # Always render into per-layer sub-buses; they are summed on the way out
    # unless the caller wants them separately.
    layer_names = sorted({str(n.get("layer") or DEFAULT_LAYER) for n in notes}
                         | {"flow", DEFAULT_LAYER})
    buffers = {name: np.zeros((total_len, 2), dtype=np.float32)
               for name in layer_names}

    # Active (still ringing) open-hat placements, so a later choke trigger can
    # fade their tail. Each entry: (buffer, buffer_start, contribution_array).
    active_open_hats = []
    fade_len = int(sr * 0.010)  # 10 ms choke fade

    placed = []

    for note in notes:
        drum_class = note.get("drum_class") or MIDI_TO_CLASS.get(
            int(note.get("midi_note", 0)))
        if drum_class is None:
            continue
        velocity = int(note.get("velocity", 100))
        t = float(note["t"])
        layer = str(note.get("layer") or DEFAULT_LAYER)
        source = note.get("source")
        idx = int(t * sr)
        if idx < 0:
            continue

        def _tag():
            tag = {"t": t, "midi_note": CLASS_TO_MIDI.get(drum_class, 0),
                   "velocity": velocity, "drum_class": drum_class,
                   "layer": layer}
            if source is not None:
                tag["source"] = source
            if note.get("event_index") is not None:
                tag["event_index"] = note["event_index"]
            return tag

        # Choke: a closed hat or kick silences any ringing open hat. Chokes
        # operate across both layers (a bed kick chokes a flow open hat).
        if drum_class in _CHOKE_TRIGGERS and active_open_hats:
            _choke_open_hats(active_open_hats, idx, fade_len)
            active_open_hats = []

        if not kit.has(drum_class):
            # No sample for this class in the loaded kit; skip it but still keep
            # it in the MIDI/duck stream so downstream shape is preserved.
            placed.append(_tag())
            continue

        target = buffers.setdefault(
            layer, np.zeros((total_len, 2), dtype=np.float32))

        layer_idx = kit.pick_layer(drum_class, velocity)
        variants = kit.layers[drum_class][layer_idx]

        variant_idx, sample = _next_variant(variants, drum_class, rr_state, rng)
        # Measured on the raw variant (after any varispeed), before the lowpass
        # and shaper, so the lead-in is the sample's own.
        lead = _onset_lead(sample)

        gain = _velocity_gain(velocity)

        # Single-layer classes: darken soft hits so they don't just sound like a
        # quieter loud hit.
        if kit.num_layers(drum_class) == 1 and velocity < 80:
            cutoff = 2000.0 + 90.0 * velocity  # ~2-9 kHz across the range
            sample = _one_pole_lowpass(sample, sr, cutoff)

        # Per-class transient shaping, applied to the buffer so it stays cheap
        # and phase-safe.
        if shaper is not None:
            sample = shaper(sample, drum_class)

        contribution = (sample * gain).astype(np.float32)
        # Start the one-shot early by its lead-in so the attack lands on `t`.
        start = idx - lead
        if start < 0:
            contribution = contribution[-start:]
            start = 0
        end_idx = min(start + len(contribution), total_len)
        clip = end_idx - start
        if clip > 0:
            target[start:end_idx] += contribution[:clip]
            if drum_class == "hat_open":
                active_open_hats.append((target, start, contribution[:clip]))

        placed.append(_tag())

    if split_layers:
        return buffers, placed
    total = np.zeros((total_len, 2), dtype=np.float32)
    for buf in buffers.values():
        total += buf
    return total, placed


def _next_variant(variants, drum_class, rr_state, rng):
    """Pick the next round-robin variant, never the same one twice in a row.

    With a single variant, apply a ±3% random varispeed as pseudo-round-robin so
    consecutive hits are not bit-identical.
    """
    if len(variants) == 1:
        ratio = float(rng.uniform(0.97, 1.03))
        return 0, _varispeed(variants[0], ratio)
    last = rr_state.get(drum_class, -1)
    idx = (last + 1) % len(variants)
    if idx == last:  # defensive; only possible with len 1 (handled above)
        idx = (idx + 1) % len(variants)
    rr_state[drum_class] = idx
    return idx, variants[idx]


def _choke_open_hats(active_open_hats, choke_idx, fade_len):
    """Apply a fast fade to the tails of any ringing open hats at ``choke_idx``.

    Each entry carries the sub-bus buffer it was placed into, so a trigger in
    one layer can choke an open hat in another.
    """
    for perc, buf_start, contribution in active_open_hats:
        contrib_len = len(contribution)
        buf_end = buf_start + contrib_len
        if buf_end <= choke_idx:
            continue  # already finished ringing
        local = max(0, choke_idx - buf_start)
        if local >= contrib_len:
            continue
        tail = contribution[local:]
        n = len(tail)
        env = np.ones(n, dtype=np.float32)
        f = min(fade_len, n)
        if f > 0:
            env[:f] = np.linspace(1.0, 0.0, f, dtype=np.float32)
        env[f:] = 0.0
        # Remove the portion of the tail that the choke silences.
        removed = tail * (1.0 - env)[:, np.newaxis]
        seg_start = buf_start + local
        seg_end = min(seg_start + n, len(perc))
        perc[seg_start:seg_end] -= removed[:seg_end - seg_start]
