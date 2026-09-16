"""Flow layer — the rapper's syllables, played verbatim.

**The load-bearing rule of the whole percussion rewrite lives here: no code
path may move a flow-layer hit off its source syllable's time.** One retained
syllable event produces exactly one hit, at that event's ``t``, with no
quantization, no grid snapping and no per-bar density caps. The syllables
already encode the density; capping them is what made the old heuristic path
throw the flow away, and re-sampling them through a VAE (the GrooVAE path) is
what made it drift.

The drum class comes from the syllable's own features:

===========================================  ==================  ==========
Event                                        Class               Velocity
===========================================  ==================  ==========
voiced, ``stress`` in the top 15% of a 2 s   ``FLOW_ACCENT_CLASS`` 70-100
window                                       (default ``ride``)
voiced, otherwise                            ``snare`` (ghost)   20-45
``sibilant``                                 ``hat_closed``      35-70
``plosive``                                  ``hat_closed``      45-75
longest sibilant at a phrase end             ``hat_open``        50-80
phrase start (> 0.6 s gap) with high stress  ``crash`` (extra)   80-110
===========================================  ==================  ==========

A side stick is the idiomatic accent sound, but ``kits/default`` ships no rim
samples, so the default accent class is ``ride``; :mod:`pipeline` validates it
against the loaded kit at render time and falls back to ``hat_closed``.

Ghosts are quiet ``snare`` hits, which is exactly what a ghost note is - and
the default kit's snare has four velocity layers, so ``sampler.pick_layer``
selects a genuinely soft sample rather than a turned-down loud one. Sharing the
``snare`` class with the backbone is why the merge guard in :mod:`groove` and
the bed-only ducking in :mod:`pipeline` both exist.
"""

import os
import logging

import numpy as np

from sampler import CLASS_TO_MIDI

logger = logging.getLogger(__name__)


# Accent class when the configured one is missing from the kit.
ACCENT_FALLBACK_CLASS = "hat_closed"

# Fraction of a 2 s window's syllables that count as accents.
_ACCENT_TOP_FRACTION = 0.15
_ACCENT_WINDOW_S = 2.0

# A gap this long before a syllable starts a new phrase.
_PHRASE_GAP_S = 0.6
# At most one crash per two 4/4 bars, approximated in seconds at ~90 BPM. The
# crash is a phrase marker, not a groove element, so a time-based spacing is
# enough and needs no beat grid.
_CRASH_MIN_GAP_S = 5.0
_CRASH_STRESS_MIN = 0.6


def _strength_to_velocity(strength):
    """Map a normalized onset strength to a MIDI velocity (moved from groove)."""
    return int(np.clip(round(30 + 97 * (float(strength) ** 0.6)), 1, 127))


def _scaled_velocity(value, lo, hi):
    """Map a 0-1 feature onto the ``[lo, hi]`` velocity range."""
    v = float(np.clip(value, 0.0, 1.0)) ** 0.7
    return int(np.clip(round(lo + (hi - lo) * v), 1, 127))


def flow_params(params: dict = None):
    """Resolve the flow-layer tunables from ``params`` then the environment."""
    params = params or {}

    def pick(key, env, default, cast=float):
        val = params.get(key)
        if val is None:
            val = os.environ.get(env)
        if val is None:
            return default
        try:
            return cast(val)
        except (TypeError, ValueError):
            return default

    return {
        "accent_class": pick("flow_accent_class", "FLOW_ACCENT_CLASS", "ride", str),
        "min_gap_ms": pick("flow_min_gap_ms", "FLOW_MIN_GAP_MS", 45.0),
    }


def _accent_mask(events):
    """True where a voiced event's stress is in the top 15% of a 2 s window.

    Judged locally so a quiet passage still gets its own accents instead of
    being flattened by a shouted hook elsewhere in the track.
    """
    times = np.asarray([e["t"] for e in events], dtype=float)
    stress = np.asarray(
        [float(e.get("stress", e.get("strength", 0.0))) for e in events], dtype=float
    )
    mask = np.zeros(len(events), dtype=bool)
    half = _ACCENT_WINDOW_S / 2.0
    pct = 100.0 * (1.0 - _ACCENT_TOP_FRACTION)
    for i, t in enumerate(times):
        lo = int(np.searchsorted(times, t - half, side="left"))
        hi = int(np.searchsorted(times, t + half, side="right"))
        window = stress[lo:hi]
        if len(window) < 2:
            mask[i] = stress[i] > 0.0
            continue
        mask[i] = stress[i] >= float(np.percentile(window, pct))
    return mask


# How far back from a phrase's last event a sibilant still counts as closing it.
_PHRASE_TAIL_S = 0.35


def _phrases(events):
    """Split time-ordered events into phrases at gaps over ``_PHRASE_GAP_S``."""
    if not events:
        return []
    groups, current = [], [0]
    for i in range(1, len(events)):
        if events[i]["t"] - events[i - 1]["t"] > _PHRASE_GAP_S:
            groups.append(current)
            current = []
        current.append(i)
    groups.append(current)
    return groups


def _phrase_end_open_hats(events):
    """Positions of the longest sibilant closing each phrase.

    An open hat is a phrase marker: it should ring into the gap, which is only
    true of the trailing "sss" of a line, not of every sibilant in it.
    """
    chosen = set()
    for group in _phrases(events):
        if not group:
            continue
        phrase_end = events[group[-1]]["t"]
        tail = [
            i for i in group
            if events[i].get("subtype") == "sibilant"
            and (phrase_end - events[i]["t"]) <= _PHRASE_TAIL_S
        ]
        if tail:
            chosen.add(max(tail, key=lambda i: float(events[i].get("dur", 0.0))))
    return chosen


def _note(t, drum_class, velocity, event_index, source="syllable"):
    return {
        "t": float(t),
        "midi_note": CLASS_TO_MIDI[drum_class],
        "velocity": int(velocity),
        "drum_class": drum_class,
        "layer": "flow",
        "source": source,
        "event_index": int(event_index),
    }


def build_flow_notes(events, params: dict = None, cull: bool = True):
    """Turn syllable events into flow-layer notes at their exact times.

    ``events`` is the detect stage's array, **in its original order** — the
    emitted notes carry ``event_index`` back-references into it, which is what
    makes the alignment guarantee testable and the score debuggable.

    Every event that survives detection produces exactly one note whose ``t``
    is bit-identical to the event's ``t``. ``cull=False`` skips the per-class
    minimum-gap pass, which is the only culling this layer does.
    """
    cfg = flow_params(params)
    accent_class = cfg["accent_class"]
    if accent_class not in CLASS_TO_MIDI:
        logger.warning(
            "FLOW_ACCENT_CLASS %r is not a known drum class; using %r.",
            accent_class, ACCENT_FALLBACK_CLASS,
        )
        accent_class = ACCENT_FALLBACK_CLASS

    indexed = [(i, e) for i, e in enumerate(events)]
    indexed.sort(key=lambda pair: float(pair[1]["t"]))
    ordered = [e for _, e in indexed]

    voiced_positions = [
        pos for pos, e in enumerate(ordered)
        if e.get("subtype", "voiced") == "voiced"
    ]
    voiced_events = [ordered[pos] for pos in voiced_positions]
    accents = _accent_mask(voiced_events) if voiced_events else np.zeros(0, bool)
    accent_by_pos = {pos: bool(a) for pos, a in zip(voiced_positions, accents)}

    open_hat_positions = _phrase_end_open_hats(ordered)

    notes = []
    last_crash_t = None
    prev_t = None
    for pos, (orig_index, event) in enumerate(indexed):
        # `t` is copied, never computed. This is the guarantee.
        t = float(event["t"])
        strength = float(event.get("strength", 0.5))
        stress = float(event.get("stress", strength))
        subtype = event.get("subtype", "voiced")

        if subtype == "voiced":
            if accent_by_pos.get(pos):
                notes.append(_note(t, accent_class,
                                   _scaled_velocity(stress, 70, 100), orig_index))
            else:
                notes.append(_note(t, "snare",
                                   _scaled_velocity(strength, 20, 45), orig_index,
                                   source="syllable"))
        elif subtype == "sibilant":
            if pos in open_hat_positions:
                notes.append(_note(t, "hat_open",
                                   _scaled_velocity(strength, 50, 80), orig_index))
            else:
                notes.append(_note(t, "hat_closed",
                                   _scaled_velocity(strength, 35, 70), orig_index))
        else:  # plosive
            notes.append(_note(t, "hat_closed",
                               _scaled_velocity(strength, 45, 75), orig_index))

        # Phrase-start crash, *in addition to* the syllable's own hit.
        starts_phrase = prev_t is None or (t - prev_t) > _PHRASE_GAP_S
        if (starts_phrase and stress >= _CRASH_STRESS_MIN
                and (last_crash_t is None or t - last_crash_t >= _CRASH_MIN_GAP_S)):
            notes.append(_note(t, "crash",
                               _scaled_velocity(stress, 80, 110), orig_index,
                               source="phrase"))
            last_crash_t = t
        prev_t = t

    notes.sort(key=lambda n: n["t"])
    if cull:
        notes = apply_min_gap(notes, cfg["min_gap_ms"] / 1000.0)
    return notes


def apply_min_gap(notes, min_gap_s: float):
    """Drop same-class hits closer than ``min_gap_s``, keeping the louder one.

    This is the *only* culling the flow layer does: it stops a sample stacking
    on itself (which just sounds like distortion), and it never moves a hit.
    """
    if min_gap_s <= 0:
        return notes
    kept = []
    last_by_class = {}
    for note in sorted(notes, key=lambda n: (n["t"], -n["velocity"])):
        cls = note["drum_class"]
        prev_i = last_by_class.get(cls)
        if prev_i is not None and note["t"] - kept[prev_i]["t"] < min_gap_s:
            if note["velocity"] > kept[prev_i]["velocity"]:
                kept[prev_i] = note
            continue
        last_by_class[cls] = len(kept)
        kept.append(note)
    kept.sort(key=lambda n: n["t"])
    return kept
