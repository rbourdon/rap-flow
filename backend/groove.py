"""Drum-score generation — two independently-timed layers, merged.

Every hit in the score traces either to a **syllable** or to the **record's own
beat**. Nothing is sampled from a model, so nothing drifts:

* the **flow layer** (:mod:`flow`) places hats, ghosts and accents at the exact
  syllable attack times, ignoring the grid entirely — including behind and ahead
  of the beat, which is where a rapper's flow actually sits;
* the **groove bed** (:mod:`backbone`) transcribes kick and snare from the
  song's own separated ``drums.wav`` stem, so the backbone lands where the
  record's beat lands and no downbeat phase has to be guessed.

This replaces the GrooVAE ``tap2drum`` path, which quantized the taps to a 16th
grid, discarded their velocities at encode (the checkpoint was
``groovae_2bar_tap_fixed_velocity``) and then *sampled* a fresh groove from the
latent at temperature 0.5. A VAE round trip returns something in the
neighbourhood of its input, not its input — so no per-syllable correspondence
survived for the old ``_snap_to_grid`` / ``_dedupe`` / ``_gate_model_notes``
repairs to preserve.

The merge is deliberately minimal (see :func:`merge_layers`): the layer balance
is **not** applied here, because it is a render-stage bus gain. That is what
lets the result page re-render the balance in seconds off the cached score.
"""

import os
import logging

import numpy as np

import backbone as backbone_mod
import flow as flow_mod

logger = logging.getLogger(__name__)


# A flow ghost snare this close to a bed snare is dropped: the backbone wins,
# because it is the beat. Flow and bed share the `snare` class (a ghost note
# *is* a quiet snare), which is why this guard exists at all.
_MERGE_SNARE_GUARD_MS_DEFAULT = 60.0


def _merge_guard_s(params: dict = None):
    params = params or {}
    val = params.get("merge_snare_guard_ms")
    if val is None:
        val = os.environ.get("MERGE_SNARE_GUARD_MS")
    try:
        return float(val) / 1000.0 if val is not None else _MERGE_SNARE_GUARD_MS_DEFAULT / 1000.0
    except (TypeError, ValueError):
        return _MERGE_SNARE_GUARD_MS_DEFAULT / 1000.0


def merge_layers(flow_notes, bed_notes, guard_s):
    """Merge the two layers into one time-ordered score.

    Only two rules:

    1. a flow ghost ``snare`` within ``guard_s`` of a bed ``snare`` is dropped;
    2. sort by time.

    Choke groups stay in :func:`sampler.render_drum_score` and now operate
    across both layers, and the layer balance is applied at render time.
    """
    bed_snares = np.asarray(
        sorted(n["t"] for n in bed_notes if n["drum_class"] == "snare"),
        dtype=float,
    )

    kept_flow = []
    for note in flow_notes:
        if note["drum_class"] == "snare" and len(bed_snares):
            k = int(np.argmin(np.abs(bed_snares - note["t"])))
            if abs(float(bed_snares[k]) - note["t"]) < guard_s:
                continue
        kept_flow.append(note)

    notes = kept_flow + list(bed_notes)
    notes.sort(key=lambda n: n["t"])
    return notes, len(kept_flow)


def generate_drum_score(events, instrumental_wav, drums_wav=None,
                        params: dict = None):
    """Build the drum score for ``events``.

    ``events`` is the detect stage's event array; ``drums_wav`` is the separated
    drum stem (already written and cached by the ``separate`` stage).

    Returns the ``drum_score.json`` payload::

        {"tempo", "beat_times", "downbeat_offset", "notes",
         "model_used": False, "warning", "stats"}

    Each note carries ``layer`` ("flow" | "bed"), ``source`` and — for flow
    notes — ``event_index``, a back-reference into ``events`` that makes the
    alignment guarantee testable and the output debuggable.
    """
    params = params or {}
    events = list(events or [])

    bed = backbone_mod.build_backbone(drums_wav, instrumental_wav, params)
    flow_notes = flow_mod.build_flow_notes(events, params)

    notes, flow_kept = merge_layers(
        flow_notes, bed["notes"], _merge_guard_s(params)
    )

    warnings = [w for w in (bed.get("warning"),) if w]
    stats = {
        "syllables": len(events),
        "flow_notes": flow_kept,
        "bed_notes": len(bed["notes"]),
        "filled": bed["stats"].get("filled", 0),
        "template_bars": bed["stats"].get("template_bars", 0),
    }

    logger.info(
        "groove: %d notes (%d flow, %d bed) from %d syllables",
        len(notes), stats["flow_notes"], stats["bed_notes"], stats["syllables"],
    )

    return {
        "tempo": bed["tempo"],
        "beat_times": bed["beat_times"],
        "downbeat_offset": bed["downbeat_offset"],
        "notes": notes,
        # No generative model is involved anywhere in this path. The key is kept
        # because the stage callback and the frontend already read it.
        "model_used": False,
        "warning": " ".join(warnings) if warnings else None,
        "stats": stats,
    }
