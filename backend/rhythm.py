"""Lightweight rhythm helpers shared by the pipeline and the groove stage.

These functions (onset grouping, beat tracking, 16th-note grid construction and
downbeat estimation) depend only on ``numpy`` and ``librosa`` — deliberately not
on ``torch``/``demucs``/``torchcrepe`` like :mod:`pipeline`. Keeping them here
lets the isolated groove stage (with its Magenta image) build a beat grid without
pulling in the heavy demucs/torch stack.
"""

import logging

import numpy as np
import librosa

logger = logging.getLogger(__name__)


def group_events(events: list, min_gap: float = 0.06):
    """
    Merges onsets that land closer together than `min_gap` seconds into a
    single hit (keeping the strongest one of the group).

    Rap flow can produce syllable onsets only 30-60ms apart; rendering a hit
    per syllable at that density produces an indistinct wash of overlapping
    clicks rather than a recognizable beat. Enforcing a minimum inter-hit
    gap turns the dense onset stream into a sparser, more legible rhythmic
    pattern.
    """
    if not events:
        return []

    ordered = sorted(events, key=lambda e: e['t'])
    grouped = [ordered[0]]

    for e in ordered[1:]:
        if e['t'] - grouped[-1]['t'] < min_gap:
            if e['strength'] > grouped[-1]['strength']:
                grouped[-1] = e
        else:
            grouped.append(e)

    return grouped


def track_beats(instrumental_wav: str):
    """Track beats on the **instrumental** stem.

    Returns ``(beat_times, tempo)`` where ``beat_times`` is a 1-D array of beat
    times in seconds and ``tempo`` is the estimated BPM. Beat tracking is run
    on the instrumental (not the vocals) because the syllable-derived
    percussion should lock to the musical grid of the backing track, not to
    the rapper's phrasing.

    The caller is expected to build its grid from the returned beat *times*
    (which follow tempo drift) rather than assuming a single constant BPM.
    """
    try:
        y, sr = librosa.load(instrumental_wav, sr=22050, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units='time')
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Beat tracking failed on %s: %s", instrumental_wav, exc)
        return np.asarray([], dtype=float), 0.0
    tempo = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    return np.asarray(beats, dtype=float), tempo


def build_sixteenth_grid(beat_times):
    """Build a 16th-note grid by interpolating between consecutive beats.

    Returns parallel arrays ``(grid_times, grid_beat_index, grid_subdivision)``
    where ``grid_subdivision`` is 0-3 (0 = on the beat, 1-3 = the intervening
    16th notes). Interpolating between the *actual* beat times keeps the grid
    aligned even when the tempo drifts, which a fixed BPM grid would not.
    """
    times, beat_idx, sub_idx = [], [], []
    for i in range(len(beat_times) - 1):
        b0, b1 = beat_times[i], beat_times[i + 1]
        for s in range(4):
            times.append(b0 + (b1 - b0) * s / 4.0)
            beat_idx.append(i)
            sub_idx.append(s)
    # Include the final beat itself as an on-beat gridline.
    times.append(beat_times[-1])
    beat_idx.append(len(beat_times) - 1)
    sub_idx.append(0)
    return np.asarray(times), np.asarray(beat_idx), np.asarray(sub_idx)


def estimate_downbeat_offset(beat_times, instrumental_wav):
    """Estimate the 4/4 downbeat phase (beat-index offset 0-3).

    Chooses the offset whose beats 1 and 3 (the strong beats of a 4/4 bar)
    capture the most onset energy in the instrumental, so kicks land where the
    track's own accents already fall.
    """
    if len(beat_times) < 4:
        return 0
    try:
        y, sr = librosa.load(instrumental_wav, sr=22050, mono=True)
        hop = 512
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        env_times = librosa.frames_to_time(
            np.arange(len(onset_env)), sr=sr, hop_length=hop
        )
    except Exception:
        return 0
    beat_strengths = np.interp(beat_times, env_times, onset_env,
                               left=0.0, right=0.0)
    best_offset, best_energy = 0, -1.0
    for offset in range(4):
        # Beats 1 and 3 are bar positions 0 and 2, i.e. even beats relative
        # to the offset.
        mask = ((np.arange(len(beat_times)) - offset) % 2) == 0
        energy = float(np.sum(beat_strengths[mask]))
        if energy > best_energy:
            best_energy, best_offset = energy, offset
    return best_offset
