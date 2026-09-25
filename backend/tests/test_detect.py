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

    # Nothing fires within the 45 ms suppression window before a nucleus.
    nucleus_times = [e["t"] for e in events if e["kind"] == "nucleus"]
    for tr in transients:
        for nt in nucleus_times:
            assert not (0.0 <= (nt - tr["t"]) <= pipeline._TRANSIENT_SUPPRESS_S)


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
