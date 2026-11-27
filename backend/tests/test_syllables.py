"""The speech-model syllable detector, with the three models stubbed.

:mod:`syllables` is numpy only: the phone classifier, Whisper and the onset
refiner are passed in. So these tests build the classifier's output directly
(a phone timeline turned into frame posteriors) and check everything between
it and the syllable list, which is all of the logic.
"""

import numpy as np
import pytest

import syllables as S

HOP = S.HOP_S


def posteriors(timeline, p=0.9):
    """``[(phone, n_frames)]`` -> log-posteriors, ``p`` on the sounding phone."""
    rows = []
    for phone, n in timeline:
        row = np.full(len(S.PHONES), (1.0 - p) / (len(S.PHONES) - 1))
        row[S.PHONE_ID[phone]] = p
        rows.extend([row] * n)
    return np.log(np.asarray(rows))


def onsets(timeline):
    """Start time of every vowel in a timeline."""
    out, frame = [], 0
    for phone, n in timeline:
        if phone in S.VOWELS:
            out.append(frame * HOP)
        frame += n
    return out


# "the cat sat", a pause, "on a mat"
THE_CAT = [
    ("[SIL]", 20), ("DH", 4), ("AH", 6), ("K", 6), ("AE", 12), ("T", 5),
    ("S", 8), ("AE", 12), ("T", 6), ("[SIL]", 30),
    ("AA", 9), ("N", 5), ("AH", 7), ("M", 6), ("AE", 14), ("T", 6), ("[SIL]", 20),
]


def words(text, start=None, end=None):
    return [{"w": w, "start": start, "end": end} for w in text.split()]


# ---------------------------------------------------------------------------
# Words -> phones
# ---------------------------------------------------------------------------
def test_vowels_are_the_syllables():
    phones = S.word_phones("hello")
    assert [p for p, _ in phones] == ["HH", "AH", "L", "OW"]
    assert [s for _, s in phones] == [None, 0, None, 1]
    assert S.count_syllables(words("the cat sat on a mat")) == 6


@pytest.mark.parametrize("word, n", [
    ("droppin'", 2),      # dropping, minus the g
    ("'em", 1),
    ("gonna", 2),
    ("tryna", 2),         # not in the dictionary: the rap lexicon has it
    ("99", 3),            # ninety nine
    ("Don't,", 1),        # case and punctuation are not pronounced
    ("mother-in-law", 4),
])
def test_rap_spellings_count_right(word, n):
    assert S.count_syllables([{"w": word}]) == n


def test_a_dropped_g_ends_in_n():
    assert S.word_phones("rollin'")[-1][0] == "N"


def test_unknown_words_still_get_a_vowel():
    """An invented word must not vanish: the aligner needs a syllable for it."""
    for word in ("zxqv", "blorptastic", "skrrrrt"):
        assert any(stress is not None for _, stress in S.word_phones(word)), word
    assert S.word_phones("...") == ()


# ---------------------------------------------------------------------------
# Heard vowels, and lyrics aligned to them
# ---------------------------------------------------------------------------
def test_one_syllable_per_run_of_vowel_frames():
    heard, _ = S.textless_syllables(posteriors(THE_CAT))
    assert [s["t"] for s in heard] == pytest.approx(onsets(THE_CAT))
    assert all(s["source"] == "heard" and s["word"] is None for s in heard)


def test_lyrics_align_to_their_vowel_onsets():
    found, phones = S.align_words(posteriors(THE_CAT), words("the cat sat on a mat"))
    assert [s["t"] for s in found] == pytest.approx(onsets(THE_CAT))
    assert [s["word"] for s in found] == ["the", "cat", "sat", "on", "a", "mat"]
    assert [s["lexical_stress"] for s in found][:2] == [0, 1]
    # The pause between the two lines is found as a silence, not smeared over.
    assert any(p["phone"] == "[SIL]" and p["end"] - p["start"] >= 0.25 for p in phones)


def test_alignment_survives_wrong_time_hints():
    """Whisper's segment times drift; hints that contradict the audio are dropped."""
    wrong = words("the cat sat on a mat", start=30.0, end=31.0)
    found, _ = S.align_words(posteriors(THE_CAT), wrong)
    assert [s["t"] for s in found] == pytest.approx(onsets(THE_CAT))


def test_lyrics_split_what_the_classifier_hears_as_one_vowel():
    """Two words, no dip between them: loudness sees one syllable, lyrics two."""
    timeline = [("[SIL]", 10), ("G", 4), ("OW", 14), ("AH", 12), ("N", 5), ("[SIL]", 10)]
    logp = posteriors(timeline)
    found, _ = S.align_words(logp, words("go on"))
    assert len(found) == 2
    assert [s["t"] for s in found] == pytest.approx(onsets(timeline))


def test_a_word_nobody_sang_is_dropped():
    found, info = S.combine(posteriors(THE_CAT), words("the cat really sat on a mat"))
    assert info["dropped"] >= 1
    assert "really" not in [s["word"] for s in found]
    assert len(found) == 6


def test_vowels_outside_the_lyrics_are_filled_in():
    """An ad-lib the transcript lacks still gets its hits.

    Whisper says roughly when its words were sung, which is what stops the
    aligner stretching them over the line it did not transcribe.
    """
    found, info = S.combine(posteriors(THE_CAT), words("the cat sat", start=0.2, end=0.8),
                            margin_s=0.1)
    assert info["aligned"] == 3 and info["filled"] == 3
    assert [s["t"] for s in found] == pytest.approx(onsets(THE_CAT))
    assert [s["source"] for s in found] == ["lyrics"] * 3 + ["heard"] * 3


def test_no_lyrics_means_heard_vowels_only():
    found, info = S.combine(posteriors(THE_CAT), [])
    assert info["aligned"] == 0
    assert [s["t"] for s in found] == pytest.approx(onsets(THE_CAT))


# ---------------------------------------------------------------------------
# Flams
# ---------------------------------------------------------------------------
def syllable(t, end, stress=0, **extra):
    return {"t": t, "end": end, "phone": "AH", "lexical_stress": stress,
            "word": "x", "vowel_logp": -0.1, "source": "lyrics", **extra}


def test_two_onsets_inside_the_gap_become_one_and_none_moves():
    kept = S.merge_close([syllable(1.000, 1.04), syllable(1.030, 1.20, stress=1),
                          syllable(1.400, 1.50)])
    assert [s["t"] for s in kept] == [1.030, 1.400]  # the stressed one, untouched


def test_back_to_back_vowels_get_room_each():
    """"do-ing": the aligner gave the first vowel its 40 ms minimum."""
    out = S.split_hiatus([syllable(2.00, 2.04), syllable(2.04, 2.30)])
    assert out[0]["t"] == 2.00
    assert out[1]["t"] == pytest.approx(2.15) and out[1]["hiatus"]
    # Too short to be two taps: left for merge_close to make one of.
    short = S.split_hiatus([syllable(2.00, 2.04), syllable(2.04, 2.10)])
    assert [s["t"] for s in short] == [2.00, 2.04]


# ---------------------------------------------------------------------------
# Transcription windows
# ---------------------------------------------------------------------------
def test_windows_start_on_phrases_and_fit_whisper():
    phrases = [(1.0, 9.0, 20), (10.0, 24.0, 30), (26.0, 33.0, 15), (60.0, 61.0, 3)]
    wins = S.transcription_windows(phrases, pad_s=0.0)
    assert wins == [(1.0, 24.0), (26.0, 33.0), (60.0, 61.0)]
    assert all(b - a <= S.MAX_WINDOW_S + 1e-9
               for a, b in S.transcription_windows([(0.0, 100.0, 300)], pad_s=0.0))


def test_a_skipped_hook_is_listened_to_again():
    """Whisper returns nothing for the second phrase; it gets its own pass."""
    line_one = THE_CAT[:9]
    line_two = [("AA", 9), ("N", 5), ("AH", 7), ("M", 6), ("AE", 14), ("T", 6),
                ("AA", 9), ("N", 5), ("AH", 7), ("[SIL]", 20)]
    timeline = line_one + [("[SIL]", 150)] + line_two
    logp = posteriors(timeline)
    y = np.zeros(int(len(logp) * HOP * 16000), dtype=np.float32)
    calls = []

    def whisper(samples, t0, language):
        calls.append(t0)
        text = "the cat sat" if len(calls) == 1 else "on a mat on a"
        return [{"w": w, "start": t0 + 0.3 + 0.1 * i, "end": t0 + 0.4 + 0.1 * i, "lang": "en"}
                for i, w in enumerate(text.split())]

    got, info = S.transcribe(y, logp, whisper)
    assert len(calls) == 2 and info["relistened"] == 1
    assert calls[1] == pytest.approx(onsets(timeline)[3] - 0.25)  # the skipped phrase
    assert [w["w"] for w in got] == "the cat sat on a mat on a".split()
    assert info["language"] == "en"


# ---------------------------------------------------------------------------
# The whole detector
# ---------------------------------------------------------------------------
def run_detect(transcribe_window, timeline=THE_CAT, refine=True):
    logp = posteriors(timeline)
    y = np.zeros(int(len(logp) * HOP * 16000), dtype=np.float32)

    def refiner(samples, coarse):
        return np.asarray(coarse) + 0.004, np.full(len(coarse), 0.8)

    return S.detect(y, lambda samples: logp, transcribe_window,
                    refiner if refine else None)


def lyric_stub(text, lang="en"):
    def whisper(samples, t0, language):
        return [{"w": w, "start": t0 + 0.1 * i, "end": t0 + 0.1 * (i + 1),
                 "seg_start": t0, "seg_end": t0 + len(samples) / 16000, "lang": lang}
                for i, w in enumerate(text.split())]
    return whisper


def test_detect_refines_every_onset_and_keeps_the_coarse_one():
    found, info = run_detect(lyric_stub("the cat sat on a mat"))
    assert info["lyrics"] and info["syllables"] == 6
    assert [s["t_coarse"] for s in found] == pytest.approx(onsets(THE_CAT))
    assert [s["t"] for s in found] == pytest.approx([t + 0.004 for t in onsets(THE_CAT)])
    assert all(s["confidence"] == 0.8 for s in found)


def test_detect_without_whisper_still_finds_every_syllable():
    def broken(samples, t0, language):
        raise RuntimeError("CUDA out of memory")

    found, info = run_detect(broken)
    assert "CUDA out of memory" in info["lyrics_error"]
    assert not info["lyrics"]
    assert len(found) == 6 and all(s["source"] == "heard" for s in found)


def test_detect_does_not_force_english_phones_onto_other_languages():
    found, info = run_detect(lyric_stub("le chat est sur le tapis", lang="fr"))
    assert info["language"] == "fr" and not info["lyrics"]
    assert len(found) == 6 and all(s["source"] == "heard" for s in found)
