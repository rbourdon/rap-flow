"""Magenta GrooVAE (tap2drum) model runner — **isolated** Magenta dependency.

This module is the only place that imports Magenta / note-seq / TensorFlow.
Magenta pins an old TensorFlow that conflicts with the demucs/torch worker
image, so this module is imported *only* inside the dedicated groove Modal
function, which uses its own image (see ``worker.py``). It is never imported by
the main worker image; :mod:`groove` imports it lazily behind a try/except.

It exposes a single function, :func:`tap2drum`, which takes a monophonic tap
list (built from the vocal onsets at their raw times) and returns a full 9-class
drum performance using the ``groovae_2bar_tap_fixed_velocity`` checkpoint. The
checkpoint is expected to be baked into the image at build time; ``GROOVE_CKPT``
overrides its location.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Checkpoint baked into the groove image at build time.
CHECKPOINT_PATH = os.environ.get(
    "GROOVE_CKPT", "/models/groovae_2bar_tap_fixed_velocity.tar"
)
CONFIG_NAME = "groovae_2bar_tap_fixed_velocity"

# 2 bars of 4/4 at 4 steps/quarter = 32 steps.
_STEPS_PER_QUARTER = 4
_BEATS_PER_BAR = 4
_BARS = 2

# Magenta's MusicVAE decodes drum sequences at a fixed default tempo (120 QPM),
# so the ``start_time`` values on the returned NoteSequence are in seconds *at
# 120 BPM*, regardless of the track's real tempo. Decoded times must therefore
# be rescaled to real seconds (see :func:`tap2drum`).
_MODEL_QPM = 120.0

_MODEL = None


def _load_model():
    """Load (once) the TrainedModel for the tap2drum checkpoint."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from magenta.models.music_vae import TrainedModel, configs

    config = configs.CONFIG_MAP[CONFIG_NAME]
    _MODEL = TrainedModel(
        config, batch_size=1, checkpoint_dir_or_path=CHECKPOINT_PATH
    )
    return _MODEL


# Beats spanned by a single 2-bar decode window.
_WINDOW_BEATS = _BARS * _BEATS_PER_BAR  # 8


def _window_seconds(qpm):
    """Length of a 2-bar window, in seconds, at ``qpm``."""
    return _BARS * _BEATS_PER_BAR * (60.0 / qpm)


def _build_windows(beat_times, last_t, fallback_win):
    """Return contiguous ``(start, end)`` decode windows.

    When ``beat_times`` has enough beats, windows are cut at **actual** beat
    boundaries every :data:`_WINDOW_BEATS` (= 2 bars) beats, so a window's real
    duration follows tempo drift rather than a single global BPM. The first
    window is extended down to ``0.0`` to catch any lead-in taps, and fixed-BPM
    windows are appended past the last tracked beat to cover trailing taps.

    Falls back to uniform ``fallback_win``-length windows (the previous
    behaviour) when beat tracking is degenerate (fewer than ``_WINDOW_BEATS + 1``
    beats), so a track without a usable beat grid still gets drums.
    """
    beats = [float(b) for b in (beat_times if beat_times is not None else [])]
    if len(beats) < _WINDOW_BEATS + 1:
        # Degenerate beat grid: uniform fixed-BPM windows from 0 to last tap.
        windows = []
        start = 0.0
        while start <= last_t + 1e-6:
            windows.append((start, start + fallback_win))
            start += fallback_win
        return windows or [(0.0, fallback_win)]

    n = len(beats)
    windows = []
    k = 0
    while k + 1 < n:
        s = beats[k]
        end_idx = min(k + _WINDOW_BEATS, n - 1)
        e = beats[end_idx]
        if e > s:
            windows.append([s, e])
        k += _WINDOW_BEATS

    # Extend the first window to catch taps before the first tracked beat.
    windows[0][0] = min(windows[0][0], 0.0)

    # Append fixed-BPM windows past the last tracked beat for trailing taps.
    tail = windows[-1][1]
    while last_t >= tail - 1e-6 and last_t > tail:
        windows.append([tail, tail + fallback_win])
        tail += fallback_win

    return [(s, e) for s, e in windows]


def _build_tap_ns(taps, qpm, start, end):
    """Build a quantized monophonic tap NoteSequence for ``[start, end)``."""
    import note_seq
    from note_seq.protobuf import music_pb2

    ns = music_pb2.NoteSequence()
    ns.tempos.add(qpm=qpm)
    for tap in taps:
        t = tap["t"]
        if not (start <= t < end):
            continue
        note = ns.notes.add()
        note.start_time = t - start
        note.end_time = t - start + 0.05
        note.pitch = 42  # taps live on a single drum lane
        note.velocity = int(tap.get("velocity", 100))
        note.is_drum = True
    ns.total_time = end - start
    return note_seq.quantize_note_sequence(ns, _STEPS_PER_QUARTER)


def tap2drum(taps, tempo, temperature=0.5, beat_times=None):
    """Expand a monophonic tap list into a 9-class drum performance.

    ``taps`` is a list of ``{t, velocity}`` at raw onset times. The taps are
    sliced into consecutive 2-bar windows, each window is run through GrooVAE,
    and the results are concatenated (each window's notes rescaled from the
    model's fixed 120 BPM reference into the window's **own real duration** and
    offset back to absolute time). Returns a list of ``{t, midi_note, velocity}``
    with the model's velocities and micro-timing.

    ``beat_times`` (seconds, from beat tracking) makes the windowing follow the
    song's actual beats, so tempo *changes* are tracked: each window spans real
    beats and is stretched/compressed to its local tempo. When ``beat_times`` is
    missing or degenerate, uniform windows at the global ``tempo`` are used.
    """
    if not taps:
        return []

    model = _load_model()
    qpm = float(tempo) if tempo and tempo > 0 else 120.0
    fallback_win = _window_seconds(qpm)
    model_win = _window_seconds(_MODEL_QPM)  # seconds the model's 2 bars span
    last_t = max(tap["t"] for tap in taps)

    windows = _build_windows(beat_times, last_t, fallback_win)

    out = []
    for start, end in windows:
        real_dur = end - start
        if real_dur <= 0:
            continue
        window_taps = [tap for tap in taps if start <= tap["t"] < end]
        if not window_taps:
            continue
        # Local tempo of this window: 2 bars (_WINDOW_BEATS beats) over its real
        # duration. Quantizing the taps at the local qpm lands them on the right
        # steps even when the tempo drifts between windows.
        local_qpm = _WINDOW_BEATS * 60.0 / real_dur
        # Per-window rescale: the model always emits ``model_win`` seconds of
        # notes; stretch/compress that into this window's real duration.
        time_scale = real_dur / model_win
        try:
            drum_ns = _run_window(model, window_taps, local_qpm, start, end,
                                  temperature)
            for note in drum_ns.notes:
                rel_t = float(note.start_time) * time_scale
                # Drop anything past this window's real duration so consecutive
                # windows don't overlap.
                if rel_t >= real_dur:
                    continue
                out.append({
                    "t": start + rel_t,
                    "midi_note": int(note.pitch),
                    "velocity": int(note.velocity),
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("GrooVAE window at %.2fs failed: %s", start, exc)

    out.sort(key=lambda n: n["t"])
    return out


def _run_window(model, window_taps, qpm, start, end, temperature):
    """Encode a tap window and decode a drum performance for it."""
    tap_ns = _build_tap_ns(window_taps, qpm, start, end)
    # tap2drum: encode the tap sequence to latent z, then decode drums.
    z, _, _ = model.encode([tap_ns])
    total_steps = _BARS * _BEATS_PER_BAR * _STEPS_PER_QUARTER
    results = model.decode(z, length=total_steps, temperature=temperature)
    return results[0]
