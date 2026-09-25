"""The render stage: bed-only ducking, limiter ceiling, loudness, stems."""

import os

import numpy as np
import pyloudnorm as pyln
import pytest
import soundfile as sf

import pipeline
import sampler
from conftest import synth_instrumental

SR = 44100
KIT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kits", "default"
)


def _score(notes, tempo=90.0, beats=None):
    return {
        "tempo": tempo,
        "beat_times": beats if beats is not None else [],
        "downbeat_offset": 0,
        "notes": notes,
        "model_used": False,
        "warning": None,
        "stats": {},
    }


def test_flow_only_ghost_snares_produce_a_flat_duck_envelope():
    """The correctness requirement behind bed-only ducking.

    Flow ghosts *are* snares, so the old class-membership test would have made
    every syllable pump the instrumental.
    """
    placed = [
        {"t": 0.5, "drum_class": "snare", "velocity": 30, "layer": "flow"},
        {"t": 0.9, "drum_class": "snare", "velocity": 28, "layer": "flow"},
        {"t": 1.4, "drum_class": "hat_closed", "velocity": 60, "layer": "flow"},
    ]
    env = pipeline._bed_duck_envelope(placed, SR * 2, SR, 0.7, 80.0)
    assert np.allclose(env, 1.0)


def test_bed_kick_and_snare_still_duck():
    placed = [{"t": 0.5, "drum_class": "kick", "velocity": 100, "layer": "bed"}]
    env = pipeline._bed_duck_envelope(placed, SR * 2, SR, 0.7, 80.0)
    assert float(np.min(env)) == pytest.approx(0.7, abs=0.01)
    # The dip is local: well away from the hit the bed is untouched.
    assert env[0] == 1.0 and env[-1] == 1.0


@pytest.fixture
def rendered(tmp_path):
    """Render a two-layer score over a synthetic instrumental."""
    inst = synth_instrumental(str(tmp_path / "instrumental.wav"), 90.0, 8.0)

    notes = []
    beat = 60.0 / 90.0
    for i in range(12):
        t = 0.25 + i * beat
        notes.append({"t": t, "midi_note": sampler.CLASS_TO_MIDI["kick"],
                      "velocity": 104, "drum_class": "kick", "layer": "bed",
                      "source": "drums"})
        notes.append({"t": t + beat / 2, "midi_note": sampler.CLASS_TO_MIDI["snare"],
                      "velocity": 100, "drum_class": "snare", "layer": "bed",
                      "source": "drums"})
    for i in range(48):
        t = 0.31 + i * 0.1607
        notes.append({"t": t, "midi_note": sampler.CLASS_TO_MIDI["hat_closed"],
                      "velocity": 55, "drum_class": "hat_closed",
                      "layer": "flow", "source": "syllable", "event_index": i})
    notes.sort(key=lambda n: n["t"])

    mix, midi, perc, instr = pipeline.sample_render(
        _score(notes), inst, str(tmp_path / "mix.wav"), kit_dir=KIT_DIR,
    )
    return {"mix": mix, "midi": midi, "perc": perc, "inst": instr}


def test_mix_respects_the_true_peak_ceiling(rendered):
    mix, sr = sf.read(rendered["mix"])
    ceiling = 10 ** (-1.0 / 20.0)
    assert float(np.max(np.abs(mix))) <= ceiling + 1e-6


def test_mix_is_normalized_to_minus_14_lufs(rendered):
    mix, sr = sf.read(rendered["mix"])
    loudness = pyln.Meter(sr).integrated_loudness(mix)
    assert abs(loudness + 14.0) < 0.5


def test_stems_sum_back_to_the_mix(rendered):
    """The player sums the two stems at unity, so they have to reconstruct it."""
    mix, _ = sf.read(rendered["mix"])
    perc, _ = sf.read(rendered["perc"])
    inst, _ = sf.read(rendered["inst"])
    n = min(len(mix), len(perc), len(inst))
    assert np.max(np.abs(mix[:n] - (perc[:n] + inst[:n]))) < 1e-4


def test_midi_and_stems_are_written(rendered):
    for key in ("mix", "midi", "perc", "inst"):
        assert os.path.exists(rendered[key])
    assert rendered["midi"].endswith(".mid")


def test_layer_stems_are_opt_in(tmp_path):
    inst = synth_instrumental(str(tmp_path / "instrumental.wav"), 90.0, 3.0)
    notes = [
        {"t": 0.5, "midi_note": sampler.CLASS_TO_MIDI["kick"], "velocity": 100,
         "drum_class": "kick", "layer": "bed"},
        {"t": 0.7, "midi_note": sampler.CLASS_TO_MIDI["hat_closed"],
         "velocity": 60, "drum_class": "hat_closed", "layer": "flow"},
    ]
    out = str(tmp_path / "mix.wav")
    pipeline.sample_render(_score(notes), inst, out, kit_dir=KIT_DIR)
    assert not os.path.exists(out.replace(".wav", "_flow_only.wav"))

    out2 = str(tmp_path / "mix2.wav")
    pipeline.sample_render(_score(notes), inst, out2, kit_dir=KIT_DIR,
                           render_layer_stems=True)
    assert os.path.exists(out2.replace(".wav", "_flow_only.wav"))
    assert os.path.exists(out2.replace(".wav", "_bed_only.wav"))


def test_balance_keeps_total_percussion_level_within_1db(tmp_path):
    """Total percussion RMS must not move as the balance sweeps 0 -> 1.

    Both layers play the same class at the same velocity so their sub-buses
    carry equal power; that isolates the equal-power balance law, which is what
    is under test here (``test_bus.test_balance_is_equal_power`` covers the law
    itself). With genuinely different content the two layers have different
    intrinsic levels and the *sum* legitimately changes — that is the balance
    doing its job, not a level fault.
    """
    inst = synth_instrumental(str(tmp_path / "instrumental.wav"), 90.0, 6.0)
    instd, sr = sf.read(inst)
    instd = np.column_stack((instd, instd)) if instd.ndim == 1 else instd

    notes = []
    for i in range(16):
        # hat_closed on both layers: equal level, so the only variable is the
        # balance law itself.
        notes.append({"t": 0.25 + i * 0.35,
                      "midi_note": sampler.CLASS_TO_MIDI["hat_closed"],
                      "velocity": 70, "drum_class": "hat_closed", "layer": "bed"})
        notes.append({"t": 0.42 + i * 0.35,
                      "midi_note": sampler.CLASS_TO_MIDI["hat_closed"],
                      "velocity": 70, "drum_class": "hat_closed", "layer": "flow"})

    kit = sampler.DrumKit.load(KIT_DIR, sr)
    sub_buses, _ = sampler.render_drum_score(notes, len(instd), sr, kit,
                                             split_layers=True)

    levels = []
    for balance in (0.0, 0.25, 0.5, 0.75, 1.0):
        perc = pipeline.balance_sub_buses(sub_buses, balance)
        rms = float(np.sqrt(np.mean(perc ** 2)))
        levels.append(20 * np.log10(rms))

    assert max(levels) - min(levels) < 1.0


def _onset(buf):
    """Index of the first frame above -40 dB re the buffer's peak."""
    env = np.max(np.abs(buf), axis=1)
    return int(np.argmax(env > float(env.max()) * 0.01))


def test_a_loosely_cut_sample_sounds_at_its_note_time(tmp_path):
    """Leading silence in a kit sample used to delay every hit by that much."""
    class_dir = tmp_path / "kit" / "snare"
    class_dir.mkdir(parents=True)
    lead = int(0.015 * SR)
    tt = np.arange(4000) / SR
    hit = np.concatenate([np.zeros(lead),
                          np.sin(2 * np.pi * 200.0 * tt) * np.exp(-tt / 0.02)])
    sf.write(str(class_dir / "v1_rr1.wav"), hit, SR)

    kit = sampler.DrumKit.load(str(tmp_path / "kit"), SR)
    assert _onset(kit.layers["snare"][0][0]) <= int(0.0015 * SR)

    note = {"t": 0.5, "midi_note": sampler.CLASS_TO_MIDI["snare"],
            "velocity": 100, "drum_class": "snare", "layer": "flow"}
    perc, _ = sampler.render_drum_score([note], SR, SR, kit)
    assert abs(_onset(perc) - int(0.5 * SR)) <= int(0.0015 * SR)


def test_every_default_kit_sample_starts_on_its_attack():
    """The bundled Salamander samples start 0-17 ms before the hit, varying
    per velocity layer and round-robin; loaded, all of them start within the
    1 ms pre-roll."""
    kit = sampler.DrumKit.load(KIT_DIR, SR)
    for drum_class, layers in kit.layers.items():
        for layer in layers:
            for sample in layer:
                assert _onset(sample) <= int(0.0015 * SR), drum_class


def test_flow_accent_class_missing_from_the_kit_falls_back(tmp_path):
    """``FLOW_ACCENT_CLASS`` is validated against the kit at render time."""
    notes = [{"t": 0.5, "midi_note": 37, "velocity": 90,
              "drum_class": "side_stick", "layer": "flow"}]
    kit = sampler.DrumKit.load(KIT_DIR, SR)
    remapped = pipeline._remap_missing_classes(notes, kit)
    assert remapped[0]["drum_class"] == "hat_closed"
    assert remapped[0]["midi_note"] == sampler.CLASS_TO_MIDI["hat_closed"]
