"""The regression guard for the original complaint: the drums must line up.

If :func:`test_every_note_lands_on_its_syllable` passes, the flow layer is
aligned to the voice *by construction* - there is no tolerance to tune and no
model to blame.
"""

import flow
import groove


def test_every_note_lands_on_its_syllable(events):
    """Float **equality**, not approximate: no hit may be moved at all."""
    notes = flow.build_flow_notes(events, cull=False)
    syllable_notes = [n for n in notes if n["source"] == "syllable"]

    # One syllable, one hit - before the min-gap cull.
    assert len(syllable_notes) == len(events)

    for note in notes:
        source_event = events[note["event_index"]]
        assert note["t"] == source_event["t"]


def test_event_index_back_references_the_detect_array(events):
    notes = flow.build_flow_notes(events, cull=False)
    for note in notes:
        assert 0 <= note["event_index"] < len(events)
    # Every event is represented exactly once by a syllable-sourced hit.
    referenced = sorted(n["event_index"] for n in notes if n["source"] == "syllable")
    assert referenced == list(range(len(events)))


def test_classes_follow_the_syllable_type(events):
    notes = flow.build_flow_notes(events, cull=False)
    by_index = {}
    for note in notes:
        if note["source"] == "syllable":
            by_index[note["event_index"]] = note

    for i, event in enumerate(events):
        cls = by_index[i]["drum_class"]
        if event["subtype"] == "voiced":
            assert cls in ("ride", "snare", "hat_closed")
        else:
            assert cls in ("hat_closed", "hat_open")


def test_no_grid_snapping_on_off_grid_times():
    """Times that sit nowhere near a 16th grid must come back untouched."""
    off_grid = [
        {"t": 0.4137, "strength": 0.6, "periodicity": 0.9, "dur": 0.1,
         "kind": "nucleus", "subtype": "voiced", "stress": 0.5},
        {"t": 0.8319, "strength": 0.6, "periodicity": 0.9, "dur": 0.1,
         "kind": "nucleus", "subtype": "voiced", "stress": 0.5},
    ]
    notes = flow.build_flow_notes(off_grid, cull=False)
    assert sorted(n["t"] for n in notes) == [0.4137, 0.8319]


def test_min_gap_keeps_the_louder_hit():
    notes = [
        {"t": 1.0, "drum_class": "hat_closed", "velocity": 40, "layer": "flow"},
        {"t": 1.01, "drum_class": "hat_closed", "velocity": 90, "layer": "flow"},
        {"t": 1.20, "drum_class": "hat_closed", "velocity": 50, "layer": "flow"},
    ]
    kept = flow.apply_min_gap(notes, 0.045)
    assert [n["velocity"] for n in kept] == [90, 50]


def test_accent_class_falls_back_when_unknown(events, monkeypatch):
    monkeypatch.setenv("FLOW_ACCENT_CLASS", "side_stick")
    notes = flow.build_flow_notes(events, cull=False)
    assert not any(n["drum_class"] == "side_stick" for n in notes)


def test_merge_drops_flow_ghosts_near_a_bed_snare():
    guard = 0.060
    flow_notes = [
        {"t": 1.000, "drum_class": "snare", "velocity": 30, "layer": "flow"},
        {"t": 1.030, "drum_class": "snare", "velocity": 30, "layer": "flow"},
        {"t": 1.400, "drum_class": "snare", "velocity": 30, "layer": "flow"},
        {"t": 1.010, "drum_class": "hat_closed", "velocity": 60, "layer": "flow"},
    ]
    bed_notes = [
        {"t": 1.000, "drum_class": "snare", "velocity": 100, "layer": "bed"},
    ]
    merged, flow_kept = groove.merge_layers(flow_notes, bed_notes, guard)

    surviving_flow = [n for n in merged if n["layer"] == "flow"]
    assert flow_kept == len(surviving_flow)
    for note in surviving_flow:
        if note["drum_class"] != "snare":
            continue
        assert min(abs(note["t"] - b["t"]) for b in bed_notes) >= guard
    # The hat is untouched by the snare guard, and the bed snare survives.
    assert any(n["drum_class"] == "hat_closed" for n in surviving_flow)
    assert any(n["layer"] == "bed" for n in merged)
    # Sorted by time.
    assert [n["t"] for n in merged] == sorted(n["t"] for n in merged)


def test_syllables_are_closed_hats_by_default(events):
    """Snare ghosts on every syllable plus a ride on accents put the flow layer
    in the backbone snare's band at nearly its level; the default is hats."""
    notes = flow.build_flow_notes(events, cull=False)
    voiced = [n for n in notes if n["source"] == "syllable"
              and events[n["event_index"]]["subtype"] == "voiced"]
    assert voiced
    assert {n["drum_class"] for n in voiced} == {"hat_closed"}


def test_the_ghost_snare_mapping_is_one_setting_away(events, monkeypatch):
    monkeypatch.setenv("FLOW_SYLLABLE_CLASS", "snare")
    monkeypatch.setenv("FLOW_ACCENT_CLASS", "ride")
    notes = flow.build_flow_notes(events, cull=False)
    classes = {n["drum_class"] for n in notes if n["source"] == "syllable"
               and events[n["event_index"]]["subtype"] == "voiced"}
    assert classes <= {"snare", "ride"} and "snare" in classes


def test_accent_velocity_follows_strength_not_local_stress():
    """``stress`` is relative to the loudest syllable within ±1 s, so every
    accent had stress ~1 and played at ~100. Two accents with the same stress
    but different track-level strength must not play at the same velocity."""
    evs = [
        {"t": 0.5, "strength": 0.2, "stress": 1.0, "dur": 0.1,
         "kind": "nucleus", "subtype": "voiced"},
        {"t": 5.0, "strength": 1.0, "stress": 1.0, "dur": 0.1,
         "kind": "nucleus", "subtype": "voiced"},
    ]
    notes = [n for n in flow.build_flow_notes(evs, cull=False)
             if n["source"] == "syllable"]
    assert len({n["velocity"] for n in notes}) == 2
