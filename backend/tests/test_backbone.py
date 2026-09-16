"""The backbone must land where the record's own beat lands."""

import numpy as np
import pytest

import backbone
from conftest import synth_drums, synth_instrumental

TEMPO = 90.0
BEAT = 60.0 / TEMPO
BARS = 8
DURATION = BEAT * 4 * BARS + 1.0


def _four_on_the_floor(skip_backbeat_in_bar=None):
    """Kick on 1 and 3, snare on 2 and 4, for ``BARS`` bars."""
    pattern = []
    for bar in range(BARS):
        bar_t = bar * 4 * BEAT
        pattern.append((bar_t + 0 * BEAT, "kick"))
        pattern.append((bar_t + 2 * BEAT, "kick"))
        for pos in (1, 3):
            if skip_backbeat_in_bar == bar and pos == 1:
                continue
            pattern.append((bar_t + pos * BEAT, "snare"))
    return pattern


@pytest.fixture
def stems(tmp_path):
    drums = synth_drums(str(tmp_path / "drums.wav"), _four_on_the_floor(), DURATION)
    inst = synth_instrumental(str(tmp_path / "instrumental.wav"), TEMPO, DURATION)
    return drums, inst


def _nearest(times, t):
    times = np.asarray(times, dtype=float)
    return float(times[int(np.argmin(np.abs(times - t)))]) if len(times) else None


def test_transcribes_a_known_pattern_within_15ms(stems):
    """Transcription accuracy on its own — snapping and fill are separate.

    ``backbone_snap_ms=0`` because the grid here comes from librosa's beat
    tracker, which is a couple of dozen milliseconds off on a synthetic
    metronome; snapping to *that* would be testing the beat tracker, not the
    transcriber. :func:`test_snap_never_moves_a_hit_further_than_the_window`
    covers the snap.
    """
    drums, inst = stems
    result = backbone.build_backbone(
        drums, inst, {"backbone_fill": False, "backbone_snap_ms": 0}
    )

    kicks = [n["t"] for n in result["notes"] if n["drum_class"] == "kick"]
    snares = [n["t"] for n in result["notes"] if n["drum_class"] == "snare"]
    assert kicks and snares

    # Check the middle bars: the first and last can be clipped by the analysis
    # window, and a beat-tracker warm-up is not what this asserts.
    for bar in range(1, BARS - 1):
        bar_t = bar * 4 * BEAT
        for pos in (0, 2):
            expected = bar_t + pos * BEAT
            assert abs(_nearest(kicks, expected) - expected) <= 0.015
        for pos in (1, 3):
            expected = bar_t + pos * BEAT
            assert abs(_nearest(snares, expected) - expected) <= 0.015


def test_snap_never_moves_a_hit_further_than_the_window(stems):
    """The record's own push and pull survives; only jitter is absorbed."""
    drums, inst = stems
    snap_ms = 25.0
    raw = backbone.build_backbone(
        drums, inst, {"backbone_fill": False, "backbone_snap_ms": 0})
    snapped = backbone.build_backbone(
        drums, inst, {"backbone_fill": False, "backbone_snap_ms": snap_ms})

    assert len(raw["notes"]) == len(snapped["notes"])
    for before, after in zip(raw["notes"], snapped["notes"]):
        assert before["drum_class"] == after["drum_class"]
        assert abs(after["t"] - before["t"]) <= snap_ms / 1000.0 + 1e-9


def test_every_note_is_tagged_as_bed(stems):
    drums, inst = stems
    result = backbone.build_backbone(drums, inst)
    assert result["notes"]
    assert all(n["layer"] == "bed" for n in result["notes"])
    # Under the chosen split the bed owns kick and snare only; hats are the
    # flow layer's.
    assert {n["drum_class"] for n in result["notes"]} <= {"kick", "snare"}


def test_fill_inserts_exactly_one_snare_for_a_missing_backbeat(tmp_path):
    missing_bar = 4
    drums = synth_drums(
        str(tmp_path / "drums.wav"),
        _four_on_the_floor(skip_backbeat_in_bar=missing_bar),
        DURATION,
    )
    inst = synth_instrumental(str(tmp_path / "instrumental.wav"), TEMPO, DURATION)

    result = backbone.build_backbone(drums, inst, {"backbone_fill": True})
    filled = [n for n in result["notes"] if n["source"] == "filled"
              and n["drum_class"] == "snare"]

    expected = missing_bar * 4 * BEAT + 1 * BEAT
    in_that_bar = [
        n for n in filled
        if missing_bar * 4 * BEAT - 0.1 <= n["t"] < (missing_bar + 1) * 4 * BEAT
    ]
    assert len(in_that_bar) == 1
    assert abs(in_that_bar[0]["t"] - expected) <= 0.08
    assert result["stats"]["filled"] >= 1


def test_missing_drum_stem_falls_back_to_the_template_with_a_warning(tmp_path):
    inst = synth_instrumental(str(tmp_path / "instrumental.wav"), TEMPO, DURATION)
    result = backbone.build_backbone(str(tmp_path / "nope.wav"), inst)

    assert result["warning"]
    assert result["notes"]
    assert any(n["source"] in ("template", "filled") for n in result["notes"])


def test_degenerate_beat_grid_does_not_snap_or_fill(tmp_path):
    """A too-short instrumental leaves the flow layer to carry the track."""
    import soundfile as sf

    short = str(tmp_path / "short.wav")
    sf.write(short, np.zeros(2048), 44100)
    drums = synth_drums(str(tmp_path / "drums.wav"), _four_on_the_floor(), 1.0)

    result = backbone.build_backbone(drums, short)
    assert result["warning"]
    assert result["stats"]["filled"] == 0
    assert result["stats"]["template_bars"] == 0
