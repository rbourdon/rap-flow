"""End-to-end: events + stems -> drum_score.json -> mix.

This is the integration test the spec asks for, minus the two stages that need
the ML stack (``separate`` needs demucs, ``detect`` needs torchcrepe): it starts
from a syllable event list and a synthetic drum stem and asserts the score's
``stats``, the mix's true peak and its integrated loudness.
"""

import numpy as np
import pyloudnorm as pyln
import pytest
import soundfile as sf

import groove
import pipeline
from conftest import synth_drums, synth_instrumental
from test_render import KIT_DIR

TEMPO = 90.0
BEAT = 60.0 / TEMPO
BARS = 8
DURATION = BEAT * 4 * BARS + 1.0


@pytest.fixture
def stems(tmp_path):
    pattern = []
    for bar in range(BARS):
        bar_t = bar * 4 * BEAT
        pattern += [(bar_t, "kick"), (bar_t + 2 * BEAT, "kick"),
                    (bar_t + BEAT, "snare"), (bar_t + 3 * BEAT, "snare")]
    drums = synth_drums(str(tmp_path / "drums.wav"), pattern, DURATION)
    inst = synth_instrumental(str(tmp_path / "instrumental.wav"), TEMPO, DURATION)
    return drums, inst


@pytest.fixture
def syllables():
    """A rap-density flow: deliberately off-grid, ~5.5 syllables a second."""
    events = []
    t = 0.37
    i = 0
    while t < DURATION - 1.0:
        voiced = (i % 5) != 3
        events.append({
            "t": round(t, 6),
            "strength": 0.25 + 0.6 * ((i * 7) % 5) / 4.0,
            "f0": 150.0 if voiced else 0.0,
            "periodicity": 0.8 if voiced else 0.1,
            "dur": 0.16 if voiced else 0.05,
            "kind": "nucleus" if voiced else "transient",
            "subtype": "voiced" if voiced else ("sibilant" if i % 2 else "plosive"),
            "stress": 0.2 + 0.7 * ((i * 3) % 4) / 3.0,
        })
        t += 0.181 if i % 3 else 0.227
        i += 1
    return events


def test_score_shape_and_stats(stems, syllables):
    drums, inst = stems
    score = groove.generate_drum_score(syllables, inst, drums_wav=drums)

    for key in ("tempo", "beat_times", "downbeat_offset", "notes",
                "model_used", "warning", "stats"):
        assert key in score
    assert score["model_used"] is False

    stats = score["stats"]
    assert stats["syllables"] == len(syllables)
    assert stats["flow_notes"] > 0
    assert stats["bed_notes"] > 0

    # Every note is attributable.
    for note in score["notes"]:
        assert note["layer"] in ("flow", "bed")
        assert note["source"]
        if note["layer"] == "flow":
            assert 0 <= note["event_index"] < len(syllables)
    assert [n["t"] for n in score["notes"]] == sorted(n["t"] for n in score["notes"])


def test_flow_notes_still_sit_on_their_syllables_after_the_merge(stems, syllables):
    """The guarantee has to survive the whole groove stage, not just flow.py."""
    drums, inst = stems
    score = groove.generate_drum_score(syllables, inst, drums_wav=drums)

    for note in score["notes"]:
        if note["layer"] != "flow":
            continue
        assert note["t"] == syllables[note["event_index"]]["t"]


def test_bed_owns_only_kick_and_snare(stems, syllables):
    drums, inst = stems
    score = groove.generate_drum_score(syllables, inst, drums_wav=drums)
    bed_classes = {n["drum_class"] for n in score["notes"] if n["layer"] == "bed"}
    assert bed_classes <= {"kick", "snare"}

    flow_classes = {n["drum_class"] for n in score["notes"] if n["layer"] == "flow"}
    assert "kick" not in flow_classes


def test_end_to_end_render_hits_the_loudness_and_peak_targets(tmp_path, stems,
                                                              syllables):
    drums, inst = stems
    score = groove.generate_drum_score(syllables, inst, drums_wav=drums)

    mix_path, midi_path, perc_path, inst_path = pipeline.sample_render(
        score, inst, str(tmp_path / "mix.wav"), kit_dir=KIT_DIR,
    )

    mix, sr = sf.read(mix_path)
    ceiling = 10 ** (-1.0 / 20.0)
    assert float(np.max(np.abs(mix))) <= ceiling + 1e-6
    assert abs(pyln.Meter(sr).integrated_loudness(mix) + 14.0) < 0.5


def test_no_syllables_still_produces_a_backbone(stems):
    drums, inst = stems
    score = groove.generate_drum_score([], inst, drums_wav=drums)
    assert score["stats"]["syllables"] == 0
    assert score["stats"]["flow_notes"] == 0
    assert score["notes"]
