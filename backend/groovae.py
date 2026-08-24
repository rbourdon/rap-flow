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


def _window_seconds(qpm):
    """Length of a 2-bar window, in seconds, at ``qpm``."""
    return _BARS * _BEATS_PER_BAR * (60.0 / qpm)


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


def tap2drum(taps, tempo, temperature=0.5):
    """Expand a monophonic tap list into a 9-class drum performance.

    ``taps`` is a list of ``{t, velocity}`` at raw onset times. The taps are
    sliced into consecutive 2-bar windows at ``tempo``, each window is run
    through GrooVAE, and the results are concatenated (with each window's notes
    offset back to absolute time). Returns a list of ``{t, midi_note,
    velocity}`` with the model's velocities and micro-timing.
    """
    if not taps:
        return []

    model = _load_model()
    qpm = float(tempo) if tempo and tempo > 0 else 120.0
    win = _window_seconds(qpm)
    last_t = max(tap["t"] for tap in taps)

    out = []
    start = 0.0
    while start <= last_t + 1e-6:
        end = start + win
        window_taps = [tap for tap in taps if start <= tap["t"] < end]
        if window_taps:
            try:
                drum_ns = _run_window(model, window_taps, qpm, start, end,
                                      temperature)
                for note in drum_ns.notes:
                    out.append({
                        "t": start + float(note.start_time),
                        "midi_note": int(note.pitch),
                        "velocity": int(note.velocity),
                    })
            except Exception as exc:  # noqa: BLE001
                logger.warning("GrooVAE window at %.2fs failed: %s", start, exc)
        start = end

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
