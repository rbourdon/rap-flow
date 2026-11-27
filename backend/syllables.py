"""Syllables from phones: who is rapping what, and exactly when each vowel starts.

The percussion follows the rapper's flow only as well as this module finds the
syllables, so it does not guess them from loudness (the ``nucleus`` detector in
:mod:`pipeline` does, and on a separated rap vocal it misses roughly one
syllable in four). It works from speech models instead:

1. **A phone classifier** (:func:`speech.phone_posteriors`, a wav2vec2 frame
   classifier at a 10 ms hop) says which phone is sounding in every frame. Each
   run of vowel frames is a syllable nucleus; that alone (:func:`textless_syllables`)
   needs no lyrics and no language model.
2. **The lyrics** (:func:`transcribe`, Whisper over phrase-aligned windows) say
   how many syllables there *should* be. They are converted to phones
   (:func:`word_phones`) and force-aligned to the classifier's posteriors
   (:func:`align_words`), which settles what loudness cannot: two vowels with no
   dip between them, a schwa swallowed between consonants, a breath that is not
   a syllable. Anything sung where no words came back is filled in from (1).
3. **A small onset network** (:func:`speech.refine_onsets`) moves each syllable
   from "this 10 ms frame" onto the vowel onset itself, at a 2 ms hop. The
   vowel onset is where a listener hears the beat of a syllable, and where
   people tapping along to speech put their taps.

Everything here is numpy only, so it runs in the test environment; the three
model calls live in :mod:`speech` and are passed in (or stubbed by the tests).
"""

import functools
import logging
import re

import numpy as np

logger = logging.getLogger(__name__)

HOP_S = 0.010

# Output classes of the phone classifier, in its own index order.
PHONES = [
    "[SIL]", "NG", "F", "M", "AE", "R", "UW", "N", "IY", "AW", "V", "UH", "OW",
    "AA", "ER", "HH", "Z", "K", "CH", "W", "EY", "ZH", "T", "EH", "Y", "AH", "B",
    "P", "TH", "DH", "AO", "G", "L", "JH", "OY", "SH", "D", "AY", "S", "IH",
    "[UNK]", "[PAD]",
]
PHONE_ID = {p: i for i, p in enumerate(PHONES)}
SIL = PHONE_ID["[SIL]"]
VOWELS = frozenset(
    ["AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY", "IH", "IY", "OW", "OY", "UH", "UW"]
)
VOWEL_IDS = np.array(sorted(PHONE_ID[v] for v in VOWELS))

# A lyric vowel gets at least this many frames in the alignment. Without a
# floor the aligner hands a swallowed vowel a single frame wherever it costs
# least, usually glued to the next vowel, which plays as a flam.
VOWEL_MIN_FRAMES = 4
# Two onsets closer than this cannot be two taps; the weaker one goes.
MIN_GAP_S = 0.05
# Whisper reads at most 30 s at a time.
MAX_WINDOW_S = 28.0
# A gap this long between heard vowels starts a new vocal phrase.
PHRASE_GAP_S = 0.7

_NEG = -1e30


# ---------------------------------------------------------------------------
# Syllables straight from the classifier (no lyrics)
# ---------------------------------------------------------------------------
def frame_segments(logp):
    """Run-length encode the per-frame best phone -> ``[(start_s, end_s, phone)]``."""
    ids = np.argmax(logp, axis=1)
    segs = []
    start = 0
    for i in range(1, len(ids) + 1):
        if i == len(ids) or ids[i] != ids[start]:
            segs.append((start * HOP_S, i * HOP_S, PHONES[int(ids[start])]))
            start = i
    return segs


def _vowel_logp(logp, start_s, end_s):
    """Mean log-probability that frames in ``[start_s, end_s)`` are *some* vowel."""
    a, b = int(round(start_s / HOP_S)), int(round(end_s / HOP_S))
    if b <= a:
        return -99.0
    return float(np.mean(np.logaddexp.reduce(logp[a:b][:, VOWEL_IDS], axis=1)))


def textless_syllables(logp, min_dur_s=0.03):
    """One syllable per run of vowel frames. Returns ``(syllables, segments)``."""
    segs = frame_segments(logp)
    out = []
    for start, end, phone in segs:
        if phone in VOWELS and end - start >= min_dur_s:
            out.append({
                "t": start, "end": end, "phone": phone, "lexical_stress": None,
                "word": None, "vowel_logp": _vowel_logp(logp, start, end),
                "source": "heard",
            })
    return out, segs


# ---------------------------------------------------------------------------
# Words -> phones
# ---------------------------------------------------------------------------
# Spellings rap uses that the dictionary lacks or gets wrong.
_LEXICON = {
    "tryna": "T R AY1 N AH0", "finna": "F IH1 N AH0", "imma": "AY1 M AH0",
    "ima": "AY1 M AH0", "i'ma": "AY1 M AH0", "gon": "G AA1 N", "gon'": "G AA1 N",
    "ya'll": "Y AO1 L", "y'all": "Y AO1 L", "em": "AH0 M", "'em": "AH0 M",
    "cuz": "K AH1 Z", "'cause": "K AH1 Z", "lil": "L IH1 L", "ain't": "EY1 N T",
    "nah": "N AA1", "yuh": "Y AH1", "uh": "AH1", "huh": "HH AH1", "ha": "HH AA1",
    "ooh": "UW1", "woah": "W OW1", "whoa": "W OW1", "skrrt": "S K ER1 T",
    "brrr": "B ER1", "aye": "AY1", "ayy": "EY1", "yo": "Y OW1", "wit": "W IH1 T",
    "da": "D AH0", "tha": "DH AH0", "ya": "Y AH0", "bout": "B AW1 T",
    "'bout": "B AW1 T", "til": "T IH1 L", "'til": "T IH1 L", "outta": "AW1 T AH0",
    "kinda": "K AY1 N D AH0", "gotta": "G AA1 T AH0", "gonna": "G AH1 N AH0",
    "wanna": "W AA1 N AH0", "lemme": "L EH1 M IY0", "gimme": "G IH1 M IY0",
    "nigga": "N IH1 G AH0", "niggas": "N IH1 G AH0 Z", "homie": "HH OW1 M IY0",
    "homies": "HH OW1 M IY0 Z", "shawty": "SH AO1 T IY0",
}

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _number_words(n):
    """A non-negative integer spelled out, the way it is said in a lyric."""
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10]] + ([_ONES[n % 10]] if n % 10 else [])
    if n < 1000:
        rest = _number_words(n % 100) if n % 100 else []
        return [_ONES[n // 100], "hundred"] + rest
    if n < 10000 and n % 100 and 1100 <= n <= 2099:  # years: "nineteen ninety"
        return _number_words(n // 100) + _number_words(n % 100)
    if n < 1000000:
        rest = _number_words(n % 1000) if n % 1000 else []
        return _number_words(n // 1000) + ["thousand"] + rest
    return [_ONES[int(d)] for d in str(n)]


@functools.lru_cache(maxsize=1)
def _cmudict():
    try:
        import cmudict
        return cmudict.dict()
    except Exception as exc:  # pragma: no cover - depends on the environment
        logger.warning("cmudict unavailable (%s); pronunciations fall back to spelling rules.", exc)
        return {}


@functools.lru_cache(maxsize=1)
def _neural_g2p():
    try:
        from g2p_en import G2p
        return G2p()
    except Exception as exc:  # pragma: no cover - depends on the environment
        logger.info("g2p_en unavailable (%s); unknown words fall back to spelling rules.", exc)
        return None


def _parse(pron):
    """``["HH", "AH0", ...]`` -> ``[("HH", None), ("AH", 0), ...]`` (known phones only)."""
    out = []
    for symbol in pron:
        m = re.match(r"^([A-Z]+)([012])?$", symbol)
        if m and m.group(1) in PHONE_ID:
            out.append((m.group(1), int(m.group(2)) if m.group(2) else None))
    # A vowel always carries a stress digit; one without (g2p_en emits these
    # occasionally) would otherwise not count as a syllable.
    return [(p, (0 if (s is None and p in VOWELS) else s)) for p, s in out]


_SPELLING_VOWELS = {"a": "AE", "e": "EH", "i": "IH", "o": "AA", "u": "AH", "y": "IY"}
_SPELLING_CONS = {
    "b": "B", "c": "K", "d": "D", "f": "F", "g": "G", "h": "HH", "j": "JH", "k": "K",
    "l": "L", "m": "M", "n": "N", "p": "P", "q": "K", "r": "R", "s": "S", "t": "T",
    "v": "V", "w": "W", "x": "K", "z": "Z",
}


def _spell_out(word):
    """Last-resort pronunciation: one vowel per group of vowel letters.

    Crude, but the syllable *count* is what the aligner needs most, and that it
    gets about right ("skrilla" -> 2, "gwap" -> 1).
    """
    out = []
    groups = re.findall(r"[aeiouy]+|[^aeiouy]", word)
    for i, g in enumerate(groups):
        if g[0] in "aeiouy":
            if g == "e" and i == len(groups) - 1 and any(p in VOWELS for p, _ in out):
                continue  # silent final e
            out.append((_SPELLING_VOWELS[g[0]], 1 if not any(s is not None for _, s in out) else 0))
        elif g in _SPELLING_CONS:
            out.append((_SPELLING_CONS[g], None))
    if out and not any(s is not None for _, s in out):
        out.insert(len(out) // 2, ("AH", 1))
    return out


def _pronounce(part):
    if part in _LEXICON:
        return _parse(_LEXICON[part].split())
    d = _cmudict()
    prons = d.get(part)
    if not prons and part.endswith("in'"):
        base = d.get(part[:-1] + "g")
        if base:  # droppin' -> dropping with the velar dropped
            prons = [base[0][:-1] + ["N"]] if base[0][-1] == "NG" else base
    if not prons and part.endswith("'s") and d.get(part[:-2]):
        prons = [d[part[:-2]][0] + ["Z"]]
    if not prons and part.replace("'", "") in d:
        prons = d[part.replace("'", "")]
    if prons:
        return _parse(prons[0])
    bare = part.replace("'", "")
    if not bare:
        return []
    g2p = _neural_g2p()
    if g2p is not None:
        try:
            got = _parse([p for p in g2p(bare) if p.strip()])
            if got:
                return got
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("g2p_en failed on %r: %s", bare, exc)
    return _spell_out(bare)


@functools.lru_cache(maxsize=50000)
def word_phones(word):
    """A lyric token -> ``((phone, stress), ...)``.

    ``stress`` is ``None`` for a consonant and 0/1/2 (CMUdict: unstressed,
    primary, secondary) for a vowel, so the vowels are the syllables.
    """
    w = word.lower().strip().replace("’", "'").replace("`", "'")
    w = re.sub(r"[^a-z0-9' -]", "", w).strip("- ")
    out = []
    for token in re.split(r"[ -]+", w):
        for part in re.findall(r"\d+|[a-z']+", token):
            if part.isdigit():
                for spoken in _number_words(int(part[:9])):
                    out.extend(_pronounce(spoken))
            else:
                out.extend(_pronounce(part))
    return tuple(out)


def count_syllables(words):
    return sum(1 for w in words for _, s in word_phones(w["w"]) if s is not None)


# ---------------------------------------------------------------------------
# Forced alignment
# ---------------------------------------------------------------------------
def _build_states(words, vowel_min):
    """The state chain the aligner walks: phones in lyric order, with an
    optional silence between words.

    A vowel is a chain of ``vowel_min`` states of which only the last may
    repeat, which is how a minimum duration is expressed in a Viterbi pass.
    """
    sil = {"pid": SIL, "optional": True, "loop": True, "head": True,
           "word": -1, "phone": "[SIL]", "stress": None}
    states = [dict(sil)]
    for wi, w in enumerate(words):
        phones = word_phones(w["w"])
        if not phones:
            continue
        for phone, stress in phones:
            n = vowel_min if stress is not None else 1
            for r in range(n):
                states.append({
                    "pid": PHONE_ID[phone], "optional": False, "loop": r == n - 1,
                    "head": r == 0, "word": wi, "phone": phone, "stress": stress,
                })
        states.append(dict(sil))
    return states


def _viterbi(logp, states, bounds=None):
    """Best monotonic path of ``states`` through the frames of ``logp``.

    Returns ``(first_frame, last_frame)`` per state, -1 where an optional state
    was skipped. ``bounds`` is an ``(S, 2)`` array of the frames each state may
    occupy.
    """
    n_frames, n_states = logp.shape[0], len(states)
    pid = np.array([s["pid"] for s in states])
    optional = np.array([s["optional"] for s in states])
    loop = np.array([s["loop"] for s in states])
    em = logp[:, pid].astype(np.float64)
    if bounds is not None:
        frames = np.arange(n_frames)[:, None]
        em = np.where((frames >= bounds[:, 0][None]) & (frames <= bounds[:, 1][None]), em, _NEG)
    skip_ok = np.concatenate(([False], optional[:-1]))  # may jump over the state before

    score = np.full(n_states, _NEG)
    score[0] = em[0, 0]
    if n_states > 1 and optional[0]:
        score[1] = em[0, 1]
    back = np.zeros((n_frames, n_states), dtype=np.int8)
    for t in range(1, n_frames):
        best = np.where(loop, score, _NEG)
        step = np.zeros(n_states, dtype=np.int8)
        one = np.concatenate(([_NEG], score[:-1]))
        better = one > best
        best[better] = one[better]
        step[better] = 1
        two = np.where(skip_ok, np.concatenate(([_NEG, _NEG], score[:-2])), _NEG)
        better = two > best
        best[better] = two[better]
        step[better] = 2
        score = np.maximum(best + em[t], _NEG)
        back[t] = step

    j = n_states - 1
    if n_states > 1 and optional[-1] and score[n_states - 2] > score[n_states - 1]:
        j = n_states - 2
    feasible = score[j] > _NEG / 2
    first = np.full(n_states, -1)
    last = np.full(n_states, -1)
    last[j] = n_frames - 1
    for t in range(n_frames - 1, 0, -1):
        step = int(back[t, j])
        if step:
            first[j] = t
            j -= step
            last[j] = t - 1
    first[j] = 0
    return first, last, feasible


def _hint(word):
    """The loose ``(start, end)`` a word is known to lie within."""
    return (word.get("seg_start", word.get("start")), word.get("seg_end", word.get("end")))


def align_words(logp, words, margin_s=1.0, vowel_min=VOWEL_MIN_FRAMES):
    """Force-align lyric words to phone posteriors.

    ``words`` is ``[{"w": str, "start": s, "end": s}]`` in sung order. The
    times only have to bracket the word loosely: ``seg_start``/``seg_end`` (the
    bounds of the Whisper segment it came from) are used when present, and
    either way they are widened by ``margin_s``.
    Returns ``(syllables, phones)``: one syllable per lyric vowel with ``t`` at
    the vowel's first frame, and the full phone-level alignment.
    """
    states = _build_states(words, vowel_min)
    n_frames = logp.shape[0]
    if len(states) <= 1 or n_frames < 2:
        return [], []

    def bounds_for(margin):
        if margin is None or any(_hint(w)[0] is None for w in words):
            return None
        b = np.zeros((len(states), 2), dtype=int)
        b[:, 1] = n_frames - 1
        for si, s in enumerate(states):
            if s["word"] >= 0:
                start, end = _hint(words[s["word"]])
                b[si, 0] = min(n_frames - 1, max(0, int((start - margin) / HOP_S)))
                b[si, 1] = min(n_frames - 1, max(0, int((end + margin) / HOP_S)))
        for si, s in enumerate(states):  # silences take their neighbours' range
            if s["word"] < 0:
                lo = b[si - 1, 0] if si > 0 else 0
                hi = b[si + 1, 1] if si + 1 < len(states) else n_frames - 1
                b[si] = (lo, max(lo, hi))
        return b

    first, last, feasible = _viterbi(logp, states, bounds_for(margin_s))
    if not feasible and margin_s is not None:
        # The time hints contradict the audio (a drifted Whisper timestamp):
        # align on order alone rather than return nonsense.
        logger.info("align: time hints infeasible, re-aligning without them")
        first, last, feasible = _viterbi(logp, states, None)
    if not feasible:
        return [], []

    phones = []
    for si, s in enumerate(states):
        if first[si] < 0:
            continue
        if not s["head"]:
            phones[-1]["end"] = (last[si] + 1) * HOP_S
            continue
        phones.append({"start": first[si] * HOP_S, "end": (last[si] + 1) * HOP_S,
                       "phone": s["phone"], "word": s["word"], "stress": s["stress"]})
    syllables = []
    for p in phones:
        if p["stress"] is None:
            continue
        syllables.append({
            "t": p["start"], "end": p["end"], "phone": p["phone"],
            "lexical_stress": p["stress"], "word": words[p["word"]]["w"].strip(),
            "vowel_logp": _vowel_logp(logp, p["start"], p["end"]), "source": "lyrics",
        })
    return syllables, phones


# ---------------------------------------------------------------------------
# Lyrics: where to point Whisper, and noticing what it skipped
# ---------------------------------------------------------------------------
def vocal_phrases(heard, gap_s=PHRASE_GAP_S):
    """Cluster heard vowels into phrases ``[(start_s, end_s, n_vowels)]``."""
    out = []
    for s in heard:
        if out and s["t"] - out[-1][1] < gap_s:
            out[-1][1] = s["end"]
            out[-1][2] += 1
        else:
            out.append([s["t"], s["end"], 1])
    return [tuple(p) for p in out if p[2] >= 2]


def transcription_windows(phrases, max_s=MAX_WINDOW_S, pad_s=0.25):
    """Pack phrases into windows of at most ``max_s`` that start on a phrase.

    Whisper drops or garbles a window that opens mid-word, so windows begin
    where the rapper does, not on a fixed 30 s grid.
    """
    wins, cur = [], None
    for start, end, _n in phrases:
        if cur is None:
            cur = [start, end]
        elif end - cur[0] <= max_s:
            cur[1] = end
        else:
            wins.append(tuple(cur))
            cur = [start, end]
        while cur[1] - cur[0] > max_s:  # one phrase longer than a window
            wins.append((cur[0], cur[0] + max_s))
            cur = [cur[0] + max_s, cur[1]]
    if cur:
        wins.append(tuple(cur))
    return [(max(0.0, a - pad_s), b + pad_s) for a, b in wins]


def transcribe(y16k, logp, transcribe_window, language=None):
    """Lyrics for the whole vocal: ``(words, info)``.

    ``transcribe_window(samples, t0, language)`` returns
    ``[{"w", "start", "end"}]`` for one window. After the first pass, any phrase
    where many vowels are heard but under half as many lyric syllables came
    back (Whisper skips repeated hooks and stacked vocals) is listened to again
    on its own.
    """
    sr = 16000
    heard, _ = textless_syllables(logp, min_dur_s=0.04)
    phrases = vocal_phrases(heard)
    wins = transcription_windows(phrases)
    words = []
    for a, b in wins:
        got = transcribe_window(y16k[int(a * sr):int(b * sr)], a, language)
        words.extend(w for w in got if w["start"] < b + 0.5)
        if language is None and got and got[0].get("lang"):
            language = got[0]["lang"]  # detected once, on the first window with words

    redo = []
    for a, b, n in phrases:
        if n < 4:
            continue
        got = count_syllables([w for w in words if a - 0.3 <= w["start"] <= b + 0.3])
        if got < 0.5 * n:
            if redo and a - redo[-1][1] < 1.0 and b - redo[-1][0] <= MAX_WINDOW_S:
                redo[-1][1] = b
            else:
                redo.append([a, b])
    relistened = 0
    for a, b in redo:
        a2, b2 = max(0.0, a - 0.25), min(len(y16k) / sr, b + 0.25)
        got = transcribe_window(y16k[int(a2 * sr):int(b2 * sr)], a2, language)
        if count_syllables(got) == 0:
            continue
        words = [w for w in words if not (a2 <= w["start"] <= b2)] + list(got)
        relistened += 1
    words.sort(key=lambda w: w["start"])
    langs = [w["lang"] for w in words if w.get("lang")]
    return words, {"windows": len(wins), "relistened": relistened,
                   "heard_vowels": len(heard), "lyric_syllables": count_syllables(words),
                   "language": max(set(langs), key=langs.count) if langs else language}


# ---------------------------------------------------------------------------
# Putting it together
# ---------------------------------------------------------------------------
def merge_close(syllables, min_gap_s=MIN_GAP_S):
    """Drop the weaker of two onsets closer than ``min_gap_s`` (never moves one)."""
    out = []
    for s in sorted(syllables, key=lambda s: s["t"]):
        if out and s["t"] - out[-1]["t"] < min_gap_s:
            def rank(x):
                return ((x.get("lexical_stress") or 0) > 0, x["end"] - x["t"], x.get("vowel_logp", 0.0))
            if rank(s) > rank(out[-1]):
                out[-1] = s
            continue
        out.append(s)
    return out


def split_hiatus(syllables, min_span_s=0.12):
    """Give two back-to-back lyric vowels ("do-ing", "they ain't") room each.

    With no consonant between them the phone classifier often cannot say where
    one ends, and the aligner then gives one of the two its minimum length,
    which puts their onsets a flam apart. Where the pair is long enough to be
    two taps, the second onset is moved to the middle as a starting point and
    flagged ``hiatus`` so the onset refiner searches for the real transition.
    """
    floor = VOWEL_MIN_FRAMES * HOP_S + 1e-6
    out = [dict(s) for s in sorted(syllables, key=lambda s: s["t"])]
    for a, b in zip(out[:-1], out[1:]):
        if abs(a["end"] - b["t"]) > 1e-6:
            continue
        if (a["end"] - a["t"]) > floor and (b["end"] - b["t"]) > floor:
            continue
        span = b["end"] - a["t"]
        if span >= min_span_s:
            a["end"] = b["t"] = a["t"] + span / 2.0
            b["hiatus"] = True
    return out


def combine(logp, words=None, margin_s=1.0, min_gap_s=MIN_GAP_S, weak_logp=-2.0,
            fill_guard_s=0.09, silence_run_s=0.12):
    """Syllables from lyrics where there are lyrics, from the classifier elsewhere.

    Returns ``(syllables, info)``. With no ``words`` this is the textless
    detector on its own.
    """
    heard, _ = textless_syllables(logp)
    if not words:
        out = merge_close(heard, min_gap_s)
        return out, {"aligned": 0, "filled": len(out), "dropped": 0}

    syllables, phones = align_words(logp, words, margin_s=margin_s)
    aligned = len(syllables)
    if not aligned:
        out = merge_close(heard, min_gap_s)
        return out, {"aligned": 0, "filled": len(out), "dropped": 0}

    # A lyric vowel the audio shows no vowel for: a word Whisper invented, or
    # one the rapper swallowed ("gonna" as "gon'").
    floor = VOWEL_MIN_FRAMES * HOP_S + 1e-6
    syllables = split_hiatus(syllables)
    kept = [s for s in syllables
            if not (s["vowel_logp"] < weak_logp and (s["end"] - s["t"]) <= floor)]
    dropped = aligned - len(kept)

    # Vowels heard where the aligner put silence: ad-libs, doubles, lines the
    # transcript lacks.
    silent = np.zeros(logp.shape[0], dtype=bool)
    for p in phones:
        if p["phone"] == "[SIL]" and p["end"] - p["start"] >= silence_run_s:
            silent[int(round(p["start"] / HOP_S)):int(round(p["end"] / HOP_S))] = True
    times = np.array([s["t"] for s in kept]) if kept else np.zeros(0)
    filled = 0
    for v in heard:
        a, b = int(round(v["t"] / HOP_S)), int(round(v["end"] / HOP_S))
        if b <= a or np.mean(silent[a:b]) < 0.8:
            continue
        if len(times) and np.min(np.abs(times - v["t"])) < fill_guard_s:
            continue
        if v["end"] - v["t"] < 0.05 or v["vowel_logp"] < -1.0:
            continue
        kept.append(dict(v))
        filled += 1
    return merge_close(kept, min_gap_s), {"aligned": aligned, "filled": filled, "dropped": dropped}


def detect(y16k, phone_posteriors, transcribe_window=None, refine_onsets=None,
           language=None):
    """The whole detector: 16 kHz vocal in, syllables out.

    The three model calls are passed in (see :mod:`speech`), so this runs, and
    is tested, without them. Returns ``(syllables, info)``; each syllable has

    ``t``              the vowel onset, the time a hit belongs on
    ``end``            where the vowel ends
    ``phone``          its vowel (CMU phone set)
    ``word``           the lyric word it belongs to, or ``None`` if only heard
    ``lexical_stress`` 1 primary, 2 secondary, 0 unstressed, ``None`` unknown
    ``source``         ``"lyrics"`` or ``"heard"``
    ``confidence``     the refiner's, 0-1 (absent without a refiner)

    A failed or non-English transcription is not an error: the syllables then
    come from the phone classifier alone.
    """
    logp = phone_posteriors(y16k)
    info = {"language": language, "words": 0, "lyrics": False}
    words = []
    if transcribe_window is not None:
        try:
            words, heard = transcribe(y16k, logp, transcribe_window, language)
            info.update(heard)
        except Exception as exc:  # the lyrics are an aid, never a requirement
            logger.exception("Lyric transcription failed; using heard vowels only.")
            info["lyrics_error"] = "%s: %s" % (type(exc).__name__, exc)
            words = []
        # The phone set and the pronouncing dictionary are English.
        if words and info.get("language") not in (None, "en", "english"):
            logger.info("Lyrics are %r, not English; using heard vowels only.", info["language"])
            words = []
    info["words"] = len(words)

    syllables, counts = combine(logp, words)
    info.update(counts)
    info["lyrics"] = counts["aligned"] > 0

    if refine_onsets is not None and syllables:
        times, confidence = refine_onsets(y16k, [s["t"] for s in syllables])
        for s, t, c in zip(syllables, times, confidence):
            s["t_coarse"] = s["t"]
            s["t"] = float(t)
            s["confidence"] = float(c)
        syllables = merge_close(syllables, MIN_GAP_S)
    info["syllables"] = len(syllables)
    return syllables, info
