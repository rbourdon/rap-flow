"""Groove bed — the kick/snare backbone, transcribed from the record's own drums.

``pipeline.separate_audio`` has always written a full ``drums.wav`` stem and
``workflow.py`` has always cached it; its docstring even says it is "reused
downstream", but until now nothing in the codebase consumed it. The song's own
drum pattern was already computed and sitting free on the artifact volume.

This module onset-detects that stem per frequency band and classifies the hits
into kick and snare, which under the chosen layer split are the *only* two
classes the bed owns (the flow layer owns hats, ghosts and accents). The result
lands exactly where the record's beat lands, with no downbeat phase guessing.

A grid sanity pass then uses all four :mod:`rhythm` helpers to soft-snap
detector jitter, fill missing backbeats and clamp obvious band bleed — but the
snap window is deliberately tight (25 ms) so the record's own push and pull
survives. Sparse or heavily-processed beats fall back to a grid template.
"""

import os
import logging

import numpy as np
import librosa

import rhythm
from sampler import CLASS_TO_MIDI

logger = logging.getLogger(__name__)


_ANALYSIS_SR = 22050
_HOP_SECONDS = 0.010
_N_FFT = 1024

# Band definitions. "body+noise" is deliberately two ranges: a snare's
# fundamental sits low but what identifies it is the noise burst up top.
_KICK_BAND = (30.0, 120.0)
_SNARE_BANDS = ((150.0, 450.0), (1000.0, 6000.0))
_HAT_BAND = (6000.0, None)

# Window used to compare band energies when classifying an onset.
_CLASSIFY_WINDOW_S = 0.030

# Minimum spacing between hits of the same bed class.
_BED_WAIT_S = 0.060

# Bleed guard: a bar with more than this many hits of a class keeps the loudest.
_MAX_KICKS_PER_BAR = 8
_MAX_SNARES_PER_BAR = 6

# Below this hit density the transcription is treated as failed.
_MIN_HITS_PER_8_BARS = 4

# How close to a backbeat position a snare has to be to count as present.
_FILL_TOLERANCE_S = 0.060


def backbone_params(params: dict = None):
    """Resolve the backbone tunables from ``params`` then the environment."""
    params = params or {}

    def pick(key, env, default, cast=float):
        val = params.get(key)
        if val is None:
            val = os.environ.get(env)
        if val is None:
            return default
        if cast is bool:
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() not in ("0", "false", "no", "off")
        try:
            return cast(val)
        except (TypeError, ValueError):
            return default

    return {
        "source": pick("backbone_source", "BACKBONE_SOURCE", "drums", str),
        "snap_ms": pick("backbone_snap_ms", "BACKBONE_SNAP_MS", 25.0),
        "fill": pick("backbone_fill", "BACKBONE_FILL", True, bool),
        "hats": pick("backbone_hats", "BACKBONE_HATS", False, bool),
    }


def _velocity(level):
    """The existing velocity curve, shared with the flow layer."""
    return int(np.clip(round(30 + 97 * (float(level) ** 0.6)), 1, 127))


def _band_envelope(mag, freqs, bands):
    """Sum STFT magnitude over one or more frequency bands."""
    if isinstance(bands[0], (int, float)) or bands[0] is None:
        bands = (bands,)
    mask = np.zeros(len(freqs), dtype=bool)
    for lo, hi in bands:
        band = freqs >= lo
        if hi is not None:
            band &= freqs <= hi
        mask |= band
    if not np.any(mask):
        return np.zeros(mag.shape[1], dtype=float)
    return np.asarray(mag[mask, :].sum(axis=0), dtype=float)


def _band_onsets(envelope, fps, wait_s):
    """Peak-pick onsets on a band envelope. Returns frame indices."""
    if not len(envelope):
        return np.zeros(0, dtype=int)
    flux = np.maximum(0.0, np.diff(envelope, prepend=float(envelope[0])))
    if not np.any(flux):
        return np.zeros(0, dtype=int)
    peaks = librosa.util.peak_pick(
        flux,
        pre_max=3, post_max=3, pre_avg=5, post_avg=5,
        delta=float(np.percentile(flux[flux > 0], 60)) if np.any(flux > 0) else 0.0,
        wait=max(1, int(round(wait_s * fps))),
    )
    return np.asarray(peaks, dtype=int)


def _local_energy(envelope, frame, half_frames):
    lo = max(0, frame - half_frames)
    hi = min(len(envelope), frame + half_frames + 1)
    return float(np.max(envelope[lo:hi])) if hi > lo else 0.0


def _transcribe(drums_wav, params):
    """Band-split onset transcription of ``drums_wav`` into kick/snare(/hat)."""
    y, sr = librosa.load(drums_wav, sr=_ANALYSIS_SR, mono=True)
    if not len(y):
        return []
    hop = int(sr * _HOP_SECONDS)
    fps = sr / float(hop)
    mag = np.abs(librosa.stft(y, n_fft=_N_FFT, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=_N_FFT)

    envelopes = {
        "kick": _band_envelope(mag, freqs, _KICK_BAND),
        "snare": _band_envelope(mag, freqs, _SNARE_BANDS),
        "hat_closed": _band_envelope(mag, freqs, _HAT_BAND),
    }
    classes = ["kick", "snare"] + (["hat_closed"] if params["hats"] else [])

    half = max(1, int(round(_CLASSIFY_WINDOW_S * fps / 2)))
    refs = {
        cls: (float(np.percentile(env, 99.5)) or 1.0)
        for cls, env in envelopes.items()
    }

    notes = []
    for cls in classes:
        env = envelopes[cls]
        for frame in _band_onsets(env, fps, _BED_WAIT_S):
            level = _local_energy(env, frame, half)
            # Classify by band-energy ratio: the band that fired has to actually
            # dominate, otherwise this is bleed from a neighbouring band. A kick
            # and a snare on the same instant are both kept — that is a real
            # pattern, not a conflict.
            others = [
                _local_energy(envelopes[o], frame, half) / refs[o]
                for o in classes if o != cls
            ]
            own = level / refs[cls]
            if others and own < max(others) * 0.7:
                continue
            notes.append({
                "t": float(frame / fps),
                "drum_class": cls,
                "velocity": _velocity(min(1.0, level / refs[cls])),
                "source": "drums",
            })
    notes.sort(key=lambda n: n["t"])
    return notes


def _template_notes(beat_times, offset):
    """Kick on beat 1 and the "and" of 3, snare on 2 and 4."""
    notes = []
    beats = np.asarray(beat_times, dtype=float)
    if len(beats) < 2:
        return notes
    for i in range(len(beats) - 1):
        bar_pos = (i - offset) % 4
        beat_len = float(beats[i + 1] - beats[i])
        if bar_pos == 0:
            notes.append({"t": float(beats[i]), "drum_class": "kick",
                          "velocity": 104, "source": "template"})
        elif bar_pos in (1, 3):
            notes.append({"t": float(beats[i]), "drum_class": "snare",
                          "velocity": 100, "source": "template"})
        if bar_pos == 2:
            notes.append({"t": float(beats[i] + beat_len * 0.5),
                          "drum_class": "kick", "velocity": 96,
                          "source": "template"})
    notes.sort(key=lambda n: n["t"])
    return notes


def _soft_snap(notes, grid_times, snap_s):
    """Nudge a hit onto the nearest 16th only when it is already within ``snap_s``.

    Detector jitter disappears; the record's own micro-timing does not. Keep the
    window tight — the sampled kick plays *on top of* the record's own kick
    (which ``separate_audio`` only ducks), so a wide snap makes them flam.
    """
    if not len(grid_times) or snap_s <= 0:
        return notes
    grid = np.asarray(grid_times, dtype=float)
    for note in notes:
        k = int(np.argmin(np.abs(grid - note["t"])))
        if abs(grid[k] - note["t"]) <= snap_s:
            note["t"] = float(grid[k])
    return notes


def _bar_positions(beat_times, offset):
    """Yield ``(bar_index, [beat_time_for_bar_pos_0..3])`` for whole bars."""
    beats = np.asarray(beat_times, dtype=float)
    out = []
    start = int(offset) % 4
    i = start
    bar = 0
    while i + 4 < len(beats):
        out.append((bar, [float(beats[i + k]) for k in range(4)],
                    float(beats[i + 4])))
        i += 4
        bar += 1
    return out


def _fill_gaps(notes, beat_times, offset):
    """Insert a missing backbeat snare / beat-1 kick, at the median velocity.

    This is what carries sparse or heavily-processed beats, where the detector
    legitimately finds nothing on a backbeat that the listener still expects.
    """
    snares = [n for n in notes if n["drum_class"] == "snare"]
    kicks = [n for n in notes if n["drum_class"] == "kick"]
    snare_vel = int(np.median([n["velocity"] for n in snares])) if snares else 100
    kick_vel = int(np.median([n["velocity"] for n in kicks])) if kicks else 104
    snare_times = np.asarray(sorted(n["t"] for n in snares), dtype=float)
    kick_times = np.asarray(sorted(n["t"] for n in kicks), dtype=float)

    def has_hit(times, t):
        if not len(times):
            return False
        k = int(np.argmin(np.abs(times - t)))
        return abs(float(times[k]) - t) <= _FILL_TOLERANCE_S

    filled = []
    for _bar, bar_beats, _next_bar in _bar_positions(beat_times, offset):
        for pos in (1, 3):  # backbeats: beats 2 and 4
            if not has_hit(snare_times, bar_beats[pos]):
                filled.append({"t": bar_beats[pos], "drum_class": "snare",
                               "velocity": snare_vel, "source": "filled"})
        if not has_hit(kick_times, bar_beats[0]):
            filled.append({"t": bar_beats[0], "drum_class": "kick",
                           "velocity": kick_vel, "source": "filled"})
    return filled


def _bleed_guard(notes, beat_times, offset):
    """Keep only the loudest hits when a bar is implausibly dense."""
    bars = _bar_positions(beat_times, offset)
    if not bars:
        return notes
    edges = [b[1][0] for b in bars] + [bars[-1][2]]
    drop = set()
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        in_bar = [n for n in notes if lo <= n["t"] < hi]
        for cls, cap in (("kick", _MAX_KICKS_PER_BAR),
                         ("snare", _MAX_SNARES_PER_BAR)):
            cls_notes = [n for n in in_bar if n["drum_class"] == cls]
            for n in sorted(cls_notes, key=lambda x: x["velocity"])[
                    :max(0, len(cls_notes) - cap)]:
                drop.add(id(n))
    return [n for n in notes if id(n) not in drop]


def build_backbone(drums_wav, instrumental_wav, params: dict = None):
    """Build the kick/snare backbone.

    Returns ``{"notes", "beat_times", "tempo", "downbeat_offset", "warning",
    "stats"}``. Never raises: every degradation falls back and surfaces a
    non-fatal warning, per the repo's rule that no stage failure may fail a job.
    """
    cfg = backbone_params(params)
    warnings = []
    stats = {"filled": 0, "template_bars": 0}

    beat_times, tempo = rhythm.track_beats(instrumental_wav)
    grid_ok = len(beat_times) >= 8
    if grid_ok:
        grid_times, _, _ = rhythm.build_sixteenth_grid(beat_times)
        offset = rhythm.estimate_downbeat_offset(beat_times, instrumental_wav)
    else:
        grid_times, offset = np.zeros(0, dtype=float), 0
        warnings.append(
            f"Beat tracking degenerate ({len(beat_times)} beats): no backbone "
            f"snapping, gap fill or template. The flow layer carries the track."
        )

    notes = []
    if cfg["source"] == "drums" and drums_wav and os.path.exists(drums_wav):
        try:
            notes = _transcribe(drums_wav, cfg)
        except Exception as exc:  # noqa: BLE001 - never fail the job
            warnings.append(f"Drum-stem transcription failed ({exc}); using the grid template.")
            notes = []
    elif cfg["source"] == "drums":
        warnings.append("drums.wav missing or unreadable; using the grid template.")

    # Degenerate transcription -> template pattern on the grid.
    bars = max(1.0, len(beat_times) / 4.0) if grid_ok else 1.0
    hits_per_8_bars = len(notes) / bars * 8.0
    if grid_ok and (cfg["source"] != "drums" or not notes
                    or hits_per_8_bars < _MIN_HITS_PER_8_BARS):
        if cfg["source"] == "drums" and notes:
            warnings.append(
                f"Backbone transcription too sparse ({hits_per_8_bars:.1f} hits "
                f"per 8 bars); using the grid template."
            )
        notes = _template_notes(beat_times, offset)
        stats["template_bars"] = int(bars)

    if grid_ok and notes:
        notes = _soft_snap(notes, grid_times, cfg["snap_ms"] / 1000.0)
        if cfg["fill"]:
            filled = _fill_gaps(notes, beat_times, offset)
            stats["filled"] = len(filled)
            notes.extend(filled)
        notes = _bleed_guard(notes, beat_times, offset)

    for note in notes:
        note["midi_note"] = CLASS_TO_MIDI[note["drum_class"]]
        note["layer"] = "bed"
    notes.sort(key=lambda n: n["t"])

    return {
        "notes": notes,
        "beat_times": [float(b) for b in beat_times],
        "tempo": float(tempo) if tempo else 0.0,
        "downbeat_offset": int(offset),
        "warning": " ".join(warnings) if warnings else None,
        "stats": stats,
    }
