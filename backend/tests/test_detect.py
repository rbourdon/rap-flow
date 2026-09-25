"""Syllable detection: one event per nucleus, at the attack, plus consonants.

``torchcrepe`` (and torch) are not in ``requirements.txt`` for these tests, so
:func:`pipeline._crepe_pitch` is stubbed with a periodicity track derived from
the synthesised signal itself. That is the only thing stubbed — the envelope,
peak-picking, attack-finding and transient logic under test are the real code.
"""

import numpy as np
import pytest
import soundfile as sf

import pipeline

SR = 22050
NUCLEUS_HZ = 160.0


def synth_voice(path, syllable_times, sr=SR, duration=None, sibilant_times=()):
    """A crude voice: a vowel-band buzz per syllable, plus HF noise bursts."""
    duration = duration or (max(list(syllable_times) + list(sibilant_times)) + 1.0)
    y = np.zeros(int(duration * sr))

    for t0 in syllable_times:
        n = int(0.18 * sr)
        idx = int(t0 * sr)
        tt = np.arange(n) / sr
        # Fast 25 ms attack, slower decay: the nucleus peak sits well after the
        # syllable's start, which is exactly what the attack search has to undo.
        env = (1.0 - np.exp(-tt / 0.025)) * np.exp(-tt / 0.09)
        buzz = sum(np.sin(2 * np.pi * NUCLEUS_HZ * k * tt) / k for k in (1, 2, 3, 5))
        end = min(len(y), idx + n)
        y[idx:end] += (env * buzz)[:end - idx]

    for t0 in sibilant_times:
        n = int(0.10 * sr)
        idx = int(t0 * sr)
        rng = np.random.default_rng(idx)
        tt = np.arange(n) / sr
        noise = rng.standard_normal(n) * np.exp(-tt / 0.05)
        # Push the energy above 4 kHz where the transient detector looks.
        noise = noise * np.sin(2 * np.pi * 7000.0 * tt)
        end = min(len(y), idx + n)
        y[idx:end] += (noise * 0.9)[:end - idx]

    peak = float(np.max(np.abs(y))) or 1.0
    sf.write(path, y / peak * 0.9, sr)
    return path, duration


@pytest.fixture
def stub_crepe(monkeypatch):
    """Periodicity = "is the vowel buzz sounding here", computed from the audio."""
    def fake(y, sr):
        hop = int(sr * pipeline._HOP_SECONDS)
        rms = np.sqrt(np.convolve(y ** 2, np.ones(hop) / hop, mode="same"))
        frames = rms[::hop]
        # Voiced where the low/mid band carries energy; the HF noise bursts are
        # attenuated by a lowpass first so they read as unvoiced.
        import scipy.signal
        sos = scipy.signal.butter(4, 3400.0, btype="low", fs=sr, output="sos")
        low = scipy.signal.sosfilt(sos, y)
        low_rms = np.sqrt(np.convolve(low ** 2, np.ones(hop) / hop, mode="same"))[::hop]
        ref = float(np.percentile(low_rms, 95)) or 1.0
        per = np.clip(low_rms / ref, 0.0, 1.0)
        pitch = np.full(len(frames), NUCLEUS_HZ)
        return pitch, per
    monkeypatch.setattr(pipeline, "_crepe_pitch", fake)


def test_one_event_per_syllable(tmp_path, stub_crepe):
    times = [0.5, 0.9, 1.3, 1.7, 2.1, 2.5, 2.9, 3.3]
    path, _ = synth_voice(str(tmp_path / "vocals.wav"), times)

    result = pipeline.detect_syllables(path)
    assert result["detector"] == "nucleus"
    assert result["warning"] is None

    nuclei = [e for e in result["events"] if e["kind"] == "nucleus"]
    # Exactly one nucleus per synthesised syllable — the old flux detector
    # over-fired inside each long vowel instead.
    assert len(nuclei) == len(times)


def test_t_is_the_attack_not_the_loudness_peak(tmp_path, stub_crepe):
    times = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    path, _ = synth_voice(str(tmp_path / "vocals.wav"), times)

    nuclei = [e for e in pipeline.detect_syllables(path)["events"]
              if e["kind"] == "nucleus"]
    detected = sorted(e["t"] for e in nuclei)

    for expected, got in zip(times, detected):
        # The envelope peaks ~40 ms into the syllable; `t` has to be back at the
        # start, not at the peak, or every drum lands half a vowel late.
        assert abs(got - expected) < 0.030


def test_events_carry_the_full_schema(tmp_path, stub_crepe):
    path, _ = synth_voice(str(tmp_path / "vocals.wav"),
                          [0.5, 0.9, 1.3, 1.7, 2.1, 2.5])
    for event in pipeline.detect_syllables(path)["events"]:
        for key in ("t", "strength", "f0", "periodicity", "dur", "kind",
                    "subtype", "stress"):
            assert key in event
        assert event["kind"] in ("nucleus", "transient")
        assert event["subtype"] in ("voiced", "sibilant", "plosive")
        assert 0.0 <= event["strength"] <= 1.0
        assert 0.0 <= event["stress"] <= 1.0


def test_strengths_are_not_crushed_by_one_outlier(tmp_path, stub_crepe):
    """Normalizing by P95 instead of the max keeps the quiet hits audible."""
    path, _ = synth_voice(str(tmp_path / "vocals.wav"),
                          [0.5, 0.9, 1.3, 1.7, 2.1, 2.5, 2.9, 3.3])
    y, sr = sf.read(path)
    # Make one syllable enormously louder than the rest.
    idx = int(2.1 * sr)
    y[idx:idx + int(0.18 * sr)] *= 8.0
    sf.write(path, np.clip(y, -1.0, 1.0), sr)

    nuclei = [e for e in pipeline.detect_syllables(path)["events"]
              if e["kind"] == "nucleus"]
    strengths = sorted(e["strength"] for e in nuclei)
    assert float(np.median(strengths)) > 0.15


def test_transients_are_typed_and_not_doubled_onto_nuclei(tmp_path, stub_crepe,
                                                          monkeypatch):
    monkeypatch.setenv("TRANSIENT_ENABLED", "1")
    times = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
    # Sibilants placed in the gaps, well clear of any nucleus attack.
    sibilants = [0.78, 1.78, 2.78]
    path, _ = synth_voice(str(tmp_path / "vocals.wav"), times,
                          sibilant_times=sibilants)

    events = pipeline.detect_syllables(path)["events"]
    transients = [e for e in events if e["kind"] == "transient"]
    assert transients
    assert all(e["subtype"] in ("sibilant", "plosive") for e in transients)

    # Nothing fires within the suppression window before a nucleus.
    suppress_s = pipeline._syllable_params()["transient_suppress_ms"] / 1000.0
    nucleus_times = [e["t"] for e in events if e["kind"] == "nucleus"]
    for tr in transients:
        for nt in nucleus_times:
            assert not (0.0 <= (nt - tr["t"]) <= suppress_s)


def test_events_are_sorted_by_time(tmp_path, stub_crepe):
    path, _ = synth_voice(str(tmp_path / "vocals.wav"),
                          [0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
                          sibilant_times=[0.78, 1.78])
    events = pipeline.detect_syllables(path)["events"]
    assert [e["t"] for e in events] == sorted(e["t"] for e in events)


def test_silence_falls_back_to_the_flux_detector(tmp_path, stub_crepe):
    path = str(tmp_path / "vocals.wav")
    sf.write(path, np.zeros(SR * 3), SR)

    result = pipeline.detect_syllables(path)
    assert result["detector"] == "flux"
    assert result["warning"]
    assert "flux" in result["warning"]


def test_detector_can_be_forced_to_flux(tmp_path, stub_crepe, monkeypatch):
    monkeypatch.setenv("SYL_DETECTOR", "flux")
    path, _ = synth_voice(str(tmp_path / "vocals.wav"), [0.5, 1.0, 1.5, 2.0])

    result = pipeline.detect_syllables(path)
    assert result["detector"] == "flux"
    # The escape hatch is not a degradation, so it carries no warning.
    assert result["warning"] is None


def test_crepe_decodes_with_weighted_argmax_not_viterbi(monkeypatch):
    """torchcrepe's default Viterbi decoder reads real rap vocals as unvoiced.

    On a separated vocal the tiny model's activations are flat enough that the
    Viterbi path parks on the top pitch bin, periodicity comes back ~0 and the
    flow layer turns into a stream of hats. torch is not installed for the
    tests, so both modules are faked and the call itself is checked.
    """
    import sys
    import types

    class FakeTensor:
        def __init__(self, arr):
            self.arr = np.asarray(arr)

        def float(self):
            return self

        def unsqueeze(self, _dim):
            return self

        def squeeze(self):
            return self

        def numpy(self):
            return self.arr

    calls = {}
    decode = types.SimpleNamespace(viterbi=object(), argmax=object(),
                                   weighted_argmax=object())

    def predict(audio, **kwargs):
        calls.update(kwargs)
        return FakeTensor(np.full(10, 150.0)), FakeTensor(np.full(10, 0.8))

    fake_torch = types.ModuleType("torch")
    fake_torch.from_numpy = FakeTensor
    fake_crepe = types.ModuleType("torchcrepe")
    fake_crepe.decode = decode
    fake_crepe.predict = predict
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchcrepe", fake_crepe)

    pitch, periodicity = pipeline._crepe_pitch(np.zeros(SR), SR)

    assert calls["decoder"] is decode.weighted_argmax
    # Never the whole track in one forward pass.
    assert calls["batch_size"] == pipeline._CREPE_BATCH_FRAMES
    assert len(pitch) == len(periodicity) == 10


def synth_connected_phrase(path, syllable_times, lead_silence_s, sr=SR):
    """One unbroken voiced phrase after a long, nearly silent stretch.

    The buzz never stops: each syllable is an amplitude swell over it, so the
    dips between syllables are only ~7 dB, as across a voiced consonant in
    connected rap. The lead-in is a -60 dB noise floor, like separation bleed
    through an instrumental break.
    """
    duration = lead_silence_s + max(syllable_times) + 0.6
    n = int(duration * sr)
    t = np.arange(n) / sr
    rng = np.random.default_rng(0)
    y = 1e-3 * rng.standard_normal(n)

    start = lead_silence_s + syllable_times[0] - 0.02
    stop = lead_silence_s + syllable_times[-1] + 0.25
    swell = np.zeros(n)
    for t0 in syllable_times:
        rel = t - (lead_silence_s + t0)
        on = rel >= 0
        swell[on] += (1.0 - np.exp(-rel[on] / 0.015)) * np.exp(-rel[on] / 0.06)
    amp = 0.4 + 0.6 * np.clip(swell / 0.8, 0.0, 1.0)
    amp[(t < start) | (t > stop)] = 0.0
    buzz = sum(np.sin(2 * np.pi * NUCLEUS_HZ * k * t) / k for k in (1, 2, 3, 5))
    y += 0.3 * amp * buzz
    sf.write(path, y / float(np.max(np.abs(y))) * 0.9, sr)
    return [lead_silence_s + t0 for t0 in syllable_times]


def test_connected_syllables_survive_a_mostly_silent_track(tmp_path, stub_crepe):
    """The prominence gate is in dB, not a fraction of the track's range.

    Gating on a fraction of the whole track's P90-P10 envelope range made the
    threshold grow with how much of the track is silence: here it would sit
    near 17 dB, far above the dips between these syllables, and every one of
    them was dropped.
    """
    times = [0.5 + 0.16 * i for i in range(16)]  # ~6 syllables/s
    expected = synth_connected_phrase(str(tmp_path / "vocals.wav"), times,
                                      lead_silence_s=8.0)

    result = pipeline.detect_syllables(str(tmp_path / "vocals.wav"))
    assert result["detector"] == "nucleus"
    nuclei = [e["t"] for e in result["events"] if e["kind"] == "nucleus"]
    assert abs(len(nuclei) - len(expected)) <= 1
    for t0 in expected[1:-1]:
        assert min(abs(t - t0) for t in nuclei) < 0.05


def add_hiss(path, times, sr=SR):
    """Add a real "s": noise high-passed above 4 kHz, so unlike
    :func:`synth_voice`'s bursts it has no vowel-band energy to read as voiced."""
    import scipy.signal
    y, _ = sf.read(path)
    sos = scipy.signal.butter(6, 4000.0, btype="high", fs=sr, output="sos")
    for t0 in times:
        n = int(0.09 * sr)
        idx = int(t0 * sr)
        tt = np.arange(n) / sr
        rng = np.random.default_rng(idx)
        hiss = scipy.signal.sosfilt(sos, rng.standard_normal(n))
        hiss *= np.minimum(1.0, tt / 0.01) * np.exp(-tt / 0.05)
        end = min(len(y), idx + n)
        y[idx:end] += (0.6 * hiss)[:end - idx]
    sf.write(path, y / float(np.max(np.abs(y))) * 0.9, sr)


def test_onset_consonants_do_not_become_hats(tmp_path, stub_crepe):
    """An "s" 120 ms ahead of its vowel is that syllable's own onset ("st",
    "sp"); a hat on it plays as an early flam. A trailing "s" with no syllable
    after it still gets its own hit."""
    times = [0.5, 1.0, 1.5, 2.0, 2.5]
    onsets = [t - 0.12 for t in times[1:]]
    trailing = 2.9
    path, _ = synth_voice(str(tmp_path / "vocals.wav"), times, duration=3.6)
    add_hiss(path, onsets + [trailing])

    events = pipeline.detect_syllables(path)["events"]
    transients = [e["t"] for e in events if e["kind"] == "transient"]
    for s in onsets:
        assert not any(abs(t - s) < 0.06 for t in transients)
    assert any(abs(t - trailing) < 0.06 for t in transients)


def test_a_pitch_tracker_that_hears_no_voicing_is_reported(tmp_path, monkeypatch):
    """What the Viterbi failure looked like: a vocal read as unvoiced but for a
    stray syllable. One nucleus over ~0.2 s of "voiced" time passes the
    nucleus-rate check, so there was no fallback and no warning at all."""
    times = [0.5 + 0.4 * i for i in range(12)]
    path, _ = synth_voice(str(tmp_path / "vocals.wav"), times)

    def nearly_deaf(y, sr):
        hop = int(sr * pipeline._HOP_SECONDS)
        n = 1 + len(y) // hop
        per = np.full(n, 0.01)
        first = int(times[0] * sr / hop)
        per[first:first + 18] = 0.9  # only the first syllable reads as voiced
        return np.full(n, 1980.0), per

    monkeypatch.setattr(pipeline, "_crepe_pitch", nearly_deaf)
    result = pipeline.detect_syllables(path)
    assert result["detector"] == "nucleus"  # no fallback: the old blind spot
    assert result["warning"]
    assert "Pitch tracker" in result["warning"]
