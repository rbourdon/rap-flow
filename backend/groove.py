"""GrooVAE (tap2drum) drum-score generation — the *decision* layer.

This is the first of the two new percussion stages. It turns the vocal syllable
events into a **drum score**: a list of ``{t, midi_note, velocity, drum_class}``
notes describing *which* drum plays *when*, *how hard*, and with what
micro-timing. The score is the single source of truth for both MIDI export and
sample rendering (:mod:`sampler`).

Two paths produce the score:

* **GrooVAE tap2drum** (the intended path) — the vocal onsets are turned into a
  monophonic "tap" sequence and fed, in 2-bar windows, through Magenta's
  ``groovae_2bar_tap_fixed_velocity`` model, which expands them into a full
  9-class drum performance with per-note velocity and micro-timing. Magenta pins
  an old TensorFlow that conflicts with the demucs/torch worker image, so the
  actual model call lives in :mod:`groovae` and is only imported inside the
  isolated groove Modal function (see ``worker.py``). This module never imports
  Magenta at module load.
* **Heuristic fallback** — if the model is disabled, unavailable, or fails for
  any reason, a metrical heuristic (kicks on 1 & 3, snares on 2 & 4, hats on the
  subdivisions, open hats on the off-beats, a crash at phrase starts) maps the
  onsets to drums so a job never fails because of Magenta.

Crucially, the vocal onsets stay the rhythmic source of truth: the taps are
built at the **raw onset times** (no global quantization), so the drums stay
locked to the voice.
"""

import os
import logging

import numpy as np

import rhythm
from sampler import CLASS_TO_MIDI

logger = logging.getLogger(__name__)


# Reduce any General MIDI drum pitch the model might emit down to one of our nine
# classes (Roland/Groove 9-class reduction).
_GM_PITCH_TO_CLASS = {}
for _cls, _pitches in {
    "kick": (35, 36),
    "snare": (37, 38, 40),
    "hat_closed": (22, 42, 44),
    "hat_open": (26, 46),
    "tom_low": (41, 43, 58),
    "tom_mid": (45, 47),
    "tom_high": (48, 50),
    "crash": (49, 52, 55, 57),
    "ride": (51, 53, 59),
}.items():
    for _p in _pitches:
        _GM_PITCH_TO_CLASS[_p] = _cls


def _env_flag(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")


def build_tap_sequence(events):
    """Build a monophonic tap list from syllable events at their **raw** times.

    Returns a list of ``{t, velocity}`` where velocity is derived from the onset
    strength. No quantization is applied — the taps carry the voice's timing.
    """
    taps = []
    for e in sorted(events, key=lambda x: float(x["t"])):
        strength = float(e.get("strength", 0.5))
        vel = int(np.clip(round(40 + 87 * (strength ** 0.6)), 1, 127))
        taps.append({"t": float(e["t"]), "velocity": vel})
    return taps


def _strength_to_velocity(strength):
    return int(np.clip(round(30 + 97 * (float(strength) ** 0.6)), 1, 127))


# Metrical drum roles for the heuristic fallback.
def heuristic_drum_score(events, instrumental_wav):
    """Map syllable events to a drum score by metrical position (fallback).

    Locks onsets to a beat-tracked 16th grid, assigns kicks/snares/hats by their
    metrical slot (kicks on beats 1 & 3, snares on 2 & 4, hats on subdivisions,
    open hats on the off-beats), caps kick/snare density per bar, and drops a
    crash on strong bar downbeats. Produces at least four drum classes on a
    typical verse. Returns ``{"tempo": bpm, "notes": [...], "model_used": False}``.
    """
    grouped = rhythm.group_events(events)
    beat_times, tempo = rhythm.track_beats(instrumental_wav)

    if len(beat_times) < 8:
        logger.warning(
            "Beat tracking degenerate (%d beats); heuristic drums unquantized.",
            len(beat_times),
        )
        grid = None
        offset = 0
    else:
        grid = rhythm.build_sixteenth_grid(beat_times)
        offset = rhythm.estimate_downbeat_offset(beat_times, instrumental_wav)

    assignments = _quantize_and_assign(grouped, grid, offset)

    notes = []
    seen_bars = set()
    for a in assignments:
        drum_class = a["drum_class"]
        vel = _strength_to_velocity(a["strength"])
        notes.append({
            "t": float(a["t"]),
            "midi_note": CLASS_TO_MIDI[drum_class],
            "velocity": vel,
            "drum_class": drum_class,
        })
        # Crash on the strong downbeat that opens a bar (phrase accent).
        bar = a.get("bar")
        if (bar is not None and bar not in seen_bars and a.get("sub") == 0
                and a.get("bar_pos") == 0 and a["strength"] > 0.5):
            seen_bars.add(bar)
            notes.append({
                "t": float(a["t"]),
                "midi_note": CLASS_TO_MIDI["crash"],
                "velocity": min(127, vel + 10),
                "drum_class": "crash",
            })

    notes.sort(key=lambda n: n["t"])
    return {"tempo": float(tempo) if tempo else 0.0, "notes": notes,
            "model_used": False}


def _quantize_and_assign(events, grid, offset):
    """Snap events to the 16th grid and assign a drum class to each.

    Returns dicts ``{t, strength, drum_class, sub, bar, bar_pos}``.
    """
    if grid is None:
        out = []
        voiced_count = 0
        for e in sorted(events, key=lambda x: x["t"]):
            per = e.get("periodicity", 1.0)
            if per <= 0.2:
                drum_class = "hat_closed"
            else:
                drum_class = "kick" if voiced_count % 2 == 0 else "snare"
                voiced_count += 1
            out.append({"t": float(e["t"]), "strength": float(e["strength"]),
                        "drum_class": drum_class, "sub": None, "bar": None,
                        "bar_pos": None})
        return out

    grid_times, grid_beat, grid_sub = grid
    slots = {}
    for e in events:
        t = float(e["t"])
        k = int(np.argmin(np.abs(grid_times - t)))
        gt = float(grid_times[k])
        dt = gt - t
        # Soft snap: keep raw timing but nudge onto the grid when very close, so
        # the drums stay locked to the voice rather than globally quantized.
        qt = gt if abs(dt) <= 0.015 else t
        assign = {
            "t": qt,
            "strength": float(e["strength"]),
            "periodicity": float(e.get("periodicity", 1.0)),
            "beat_index": int(grid_beat[k]),
            "sub": int(grid_sub[k]),
        }
        prev = slots.get(k)
        if prev is None or assign["strength"] > prev["strength"]:
            slots[k] = assign

    ordered = sorted(slots.values(), key=lambda a: a["t"])

    for a in ordered:
        bar_pos = (a["beat_index"] - offset) % 4  # 0..3 -> beats 1..4
        a["bar"] = (a["beat_index"] - offset) // 4
        a["bar_pos"] = bar_pos
        if a["periodicity"] <= 0.2:
            # Unvoiced consonants -> hats; open hat on the off-beat "and".
            a["drum_class"] = "hat_open" if a["sub"] == 2 else "hat_closed"
        elif a["sub"] == 0:
            a["drum_class"] = "kick" if bar_pos in (0, 2) else "snare"
        else:
            a["drum_class"] = "hat_open" if a["sub"] == 2 else "hat_closed"

    _apply_density_gating(ordered)
    return ordered


def _apply_density_gating(assignments):
    """Cap kicks/snares per 4/4 bar, demoting the weakest overflow to hats."""
    bars = {}
    for a in assignments:
        bars.setdefault(a["bar"], []).append(a)
    for items in bars.values():
        kicks = [a for a in items if a["drum_class"] == "kick"]
        snares = [a for a in items if a["drum_class"] == "snare"]
        for a in sorted(kicks, key=lambda x: x["strength"])[:max(0, len(kicks) - 4)]:
            a["drum_class"] = "hat_closed"
        for a in sorted(snares, key=lambda x: x["strength"])[:max(0, len(snares) - 2)]:
            a["drum_class"] = "hat_closed"


def generate_drum_score(events, instrumental_wav, temperature=None,
                        enabled=None):
    """Produce a drum score for ``events``, GrooVAE first with heuristic fallback.

    Returns ``{"tempo", "notes", "model_used", "warning"}``. Any failure of the
    Magenta path logs a warning and falls back to the heuristic so a job never
    fails because of Magenta.
    """
    if enabled is None:
        enabled = _env_flag("GROOVE_ENABLED", True)
    if temperature is None:
        temperature = float(os.environ.get("GROOVE_TEMPERATURE", 0.5))

    if not enabled:
        logger.info("GROOVE_ENABLED is off; using heuristic drum score.")
        score = heuristic_drum_score(events, instrumental_wav)
        score["warning"] = None
        return score

    try:
        import groovae  # noqa: WPS433 - lazy so Magenta stays out of the main image

        # Group the raw onsets first so a dense syllable stream (30-60 ms apart)
        # doesn't feed the model a wash of taps that becomes a wash of drums.
        grouped = rhythm.group_events(events)
        taps = build_tap_sequence(grouped)
        beat_times, tempo = rhythm.track_beats(instrumental_wav)
        if not tempo or tempo <= 0:
            tempo = 120.0
        notes = groovae.tap2drum(taps, tempo=tempo, temperature=temperature)
        # Normalize model output onto our nine classes.
        cleaned = []
        for n in notes:
            drum_class = _GM_PITCH_TO_CLASS.get(int(n["midi_note"]))
            if drum_class is None:
                continue
            cleaned.append({
                "t": float(n["t"]),
                "midi_note": CLASS_TO_MIDI[drum_class],
                "velocity": int(np.clip(int(n.get("velocity", 100)), 1, 127)),
                "drum_class": drum_class,
            })
        cleaned = _dedupe(cleaned)
        # Plausibility gating: cap kick/snare per bar and enforce a per-class
        # minimum inter-hit gap so the model can't emit a machine-gun wash.
        cleaned = _gate_model_notes(cleaned, tempo)
        if not cleaned:
            raise RuntimeError("GrooVAE returned no usable notes")
        cleaned.sort(key=lambda n: n["t"])
        logger.info("GrooVAE produced %d drum notes.", len(cleaned))
        return {"tempo": float(tempo), "notes": cleaned, "model_used": True,
                "warning": None}
    except Exception as exc:  # noqa: BLE001 - never fail the job on Magenta
        warning = f"GrooVAE unavailable ({exc}); using heuristic drum score."
        logger.warning(warning)
        score = heuristic_drum_score(events, instrumental_wav)
        score["warning"] = warning
        return score


def _dedupe(notes, eps=0.03):
    """Drop near-duplicate notes (same class within ``eps`` seconds).

    De-overlaps notes emitted at 2-bar window boundaries.
    """
    kept = []
    last_by_class = {}
    for n in sorted(notes, key=lambda x: x["t"]):
        prev = last_by_class.get(n["drum_class"])
        if prev is not None and abs(n["t"] - prev) < eps:
            continue
        last_by_class[n["drum_class"]] = n["t"]
        kept.append(n)
    return kept


# Minimum time (seconds) between two hits of the same class on the model path.
# Kicks/snares are spaced further apart than hats, which legitimately subdivide.
_MODEL_MIN_GAP = {
    "kick": 0.09,
    "snare": 0.09,
    "hat_closed": 0.05,
    "hat_open": 0.08,
}
_MODEL_MIN_GAP_DEFAULT = 0.08

# Per-4/4-bar caps for the dominant classes, mirroring the heuristic path.
_MODEL_BAR_CAP = {"kick": 4, "snare": 2}


def _gate_model_notes(notes, tempo):
    """Gate GrooVAE output: per-class min gap + per-bar kick/snare caps.

    The model can emit an implausibly dense stream of hits (especially after the
    tempo rescale realigns windows). This mirrors the heuristic path's density
    control so the drums read as a groove rather than a wash: hits of the same
    class closer than a per-class minimum gap are dropped (keeping the louder
    one), and kicks/snares are capped per 4/4 bar (weakest overflow removed).
    """
    if not notes:
        return notes

    ordered = sorted(notes, key=lambda n: (n["t"], -n["velocity"]))

    # 1. Enforce a per-class minimum inter-hit gap, keeping the louder hit.
    kept = []
    last_kept = {}  # drum_class -> index into `kept`
    for n in ordered:
        cls = n["drum_class"]
        gap = _MODEL_MIN_GAP.get(cls, _MODEL_MIN_GAP_DEFAULT)
        prev_i = last_kept.get(cls)
        if prev_i is not None and n["t"] - kept[prev_i]["t"] < gap:
            # Too close: keep whichever is louder.
            if n["velocity"] > kept[prev_i]["velocity"]:
                kept[prev_i] = n
            continue
        last_kept[cls] = len(kept)
        kept.append(n)

    if tempo and tempo > 0:
        bar_sec = 4.0 * 60.0 / float(tempo)
    else:
        bar_sec = None

    # 2. Cap kicks/snares per bar, dropping the weakest overflow.
    if bar_sec and bar_sec > 0:
        bars = {}
        for n in kept:
            bars.setdefault(int(n["t"] // bar_sec), []).append(n)
        drop = set()
        for items in bars.values():
            for cls, cap in _MODEL_BAR_CAP.items():
                cls_notes = [n for n in items if n["drum_class"] == cls]
                overflow = sorted(cls_notes, key=lambda x: x["velocity"])[
                    :max(0, len(cls_notes) - cap)]
                for n in overflow:
                    drop.add(id(n))
        kept = [n for n in kept if id(n) not in drop]

    kept.sort(key=lambda n: n["t"])
    return kept
