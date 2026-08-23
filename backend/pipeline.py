import os
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

# Need numpy and others for later, just stubbing part 1
import numpy as np
import torch
import librosa
import soundfile as sf
import yt_dlp
import demucs.api


class IngestError(Exception):
    pass

def classify_yt_dlp_error(e: Exception) -> str:
    err_str = str(e)
    # AUTH_REQUIRED checks
    auth_indicators = [
        "Sign in to confirm you're not a bot",
        "Sign in to confirm your age",
        "cookies"
    ]
    if any(ind.lower() in err_str.lower() for ind in auth_indicators):
        return f"AUTH_REQUIRED: {err_str}"

    # VIDEO_UNAVAILABLE checks
    unavailable_indicators = [
        "Video unavailable",
        "Private video",
        "This video is unavailable",
        "blocked it on copyright grounds"
    ]
    if any(ind.lower() in err_str.lower() for ind in unavailable_indicators):
        return f"VIDEO_UNAVAILABLE: {err_str}"

    # 403 checks: YouTube refused to serve the media stream. This almost always
    # means the server's IP is rate-limited / flagged as a bot, or the chosen
    # format required a proof-of-origin (PO) token that could not be supplied.
    if "403" in err_str or "forbidden" in err_str.lower():
        return (
            "INGEST_FAILED: YouTube refused to serve the media (HTTP 403 Forbidden). "
            "This usually means the server's IP address is rate-limited or flagged as a bot. "
            "Configure a residential proxy (YT_PROXY) and/or account cookies (YT_COOKIES) and retry. "
            f"Original error: {err_str}"
        )

    return f"INGEST_FAILED: {err_str}"

def ingest_audio(input_url_or_path: str, output_path: str, yt_cookies: str = None, yt_proxy: str = None):
    """
    Ingests audio from a URL using yt-dlp, or from a local path/URL using ffmpeg.
    Normalizes to 44.1 kHz stereo WAV.
    """
    parsed = urllib.parse.urlparse(input_url_or_path)
    is_url = parsed.scheme in ('http', 'https')

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_download = os.path.join(tmpdir, "downloaded")

        if is_url and ("youtube.com" in input_url_or_path or "youtu.be" in input_url_or_path or "soundcloud.com" in input_url_or_path):
            # By default we do NOT pin a YouTube player client. yt-dlp's
            # maintainers keep the default client list (currently including
            # "visionos", whose formats do not require a GVS PO Token) tuned to
            # whatever currently downloads reliably, so deferring to it gives us
            # a PO-token-free fallback for free. A specific set can still be
            # forced without a code change via YT_PLAYER_CLIENT, e.g.
            # YT_PLAYER_CLIENT="tv,web_safari".
            #
            # The previous hard-coded ['ios', 'web'] override was actively
            # harmful: both clients now *require* a GVS PO Token for their
            # HTTPS/DASH formats, and the bgutil provider can only mint WebPO
            # tokens (and even then not reliably from a datacenter IP). yt-dlp
            # would then either drop every format ("Requested format is not
            # available") or, with formats=missing_pot, keep the token-gated
            # format and fail to download it with "HTTP Error 403: Forbidden".
            youtube_args = {}
            player_client_env = os.environ.get("YT_PLAYER_CLIENT")
            if player_client_env:
                youtube_args['player_client'] = [
                    c.strip() for c in player_client_env.split(',') if c.strip()
                ]

            extractor_args = {
                # The bgutil-ytdlp-pot-provider plugin registers itself with
                # yt-dlp's PO Token Provider Framework and supplies GVS tokens
                # for web-based clients automatically. server_home points at the
                # provider source built into the worker image.
                'youtubepot-bgutilscript': {
                    'server_home': ['/opt/bgutil/server'],
                },
            }
            if youtube_args:
                extractor_args['youtube'] = youtube_args

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': tmp_download,
                # yt-dlp needs an external JS runtime to solve YouTube's nsig
                # challenges (used to sign download URLs). Without one it
                # silently falls back to clients whose URLs expire/require no
                # signing, which results in "HTTP Error 403: Forbidden" when
                # downloading. The worker image ships Node.js (for
                # bgutil-ytdlp-pot-provider), so use it here instead of the
                # default "deno" runtime, which isn't installed.
                'js_runtimes': {'node': {}},
                'extractor_args': extractor_args,
                # NOTE: do NOT set formats=missing_pot. That option forces
                # yt-dlp to keep formats that require a GVS PO Token even when no
                # token is available; those formats then fail to download with
                # "HTTP Error 403: Forbidden". Leaving it unset lets yt-dlp skip
                # PO-Token-gated formats and fall back to a client/format that
                # does not need one.
                'retries': 3,
                'fragment_retries': 3,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'wav',
                }],
                'noplaylist': True,
            }
            if yt_cookies:
                cookies_path = os.path.join(tmpdir, "cookies.txt")
                with open(cookies_path, "w") as f:
                    f.write(yt_cookies)
                ydl_opts['cookiefile'] = cookies_path

            proxy = yt_proxy or os.environ.get("YT_PROXY")
            if proxy:
                ydl_opts['proxy'] = proxy

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(input_url_or_path, download=False)

                    if info_dict:
                        is_live = info_dict.get('is_live')
                        if is_live:
                            raise IngestError("UNSUPPORTED_SOURCE: Livestreams are not supported.")

                        # yt-dlp usually returns the video info if 'v' and 'list' are present because of noplaylist=True
                        # We also check for 'entries' in case it's a playlist only url
                        if 'entries' in info_dict:
                            # It's a playlist
                            entries = list(info_dict['entries'])
                            if len(entries) > 1 and not ('v=' in input_url_or_path and 'list=' in input_url_or_path):
                                raise IngestError("UNSUPPORTED_SOURCE: Playlist URLs are not supported. Please provide a single video URL.")

                        duration = info_dict.get('duration')
                        max_duration = int(os.environ.get("MAX_SOURCE_DURATION_SEC", 900))
                        if duration and duration > max_duration:
                            raise IngestError(f"UNSUPPORTED_SOURCE: Audio source exceeds maximum duration of {max_duration} seconds ({duration}s).")

                    ydl.download([input_url_or_path])
            except yt_dlp.utils.DownloadError as e:
                raise IngestError(classify_yt_dlp_error(e))

            tmp_download += ".wav" # yt-dlp appends .wav
        else:
            # If it's a direct url or local file, we just rely on ffmpeg to read it directly
            tmp_download = input_url_or_path

        # Resample to 44.1kHz stereo
        cmd = [
            "ffmpeg", "-y", "-i", tmp_download,
            "-ar", "44100", "-ac", "2", output_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return output_path

def separate_audio(input_wav: str, output_dir: str, drums_duck_db: float = -8.0):
    """
    Separates the input wav into vocals and instrumental using Demucs.

    The instrumental bed is built from drums + bass + other, but the real
    drums stem is attenuated by `drums_duck_db` first. Rendering synthetic,
    flow-derived percussion on top of an instrumental that still contains the
    original (full-level) drum track buries the new percussion under the
    existing beat, since they occupy the same low/mid frequency range and the
    original drums are usually the loudest element in the mix. Ducking the
    original drums a bit opens up headroom for the new percussion layer
    without removing the beat entirely (which would leave holes when the
    detector misses a syllable).
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    separator = demucs.api.Separator(model="htdemucs_ft")

    # demucs API returns a dict of stems: { "vocals": tensor, "no_vocals": tensor } if we configure it right,
    # but by default htdemucs_ft gives vocals, drums, bass, other.
    # We will compute no_vocals by subtracting vocals from the mixture, or summing the rest.

    # Load audio
    _, audio = separator.separate_audio_file(input_wav)
    # audio is a dict: {'drums': tensor, 'bass': tensor, 'other': tensor, 'vocals': tensor}

    vocals = audio['vocals'].cpu().numpy()

    drums_gain = 10 ** (drums_duck_db / 20)
    # combine drums (ducked), bass, other to get instrumental
    instrumental = (audio['drums'] * drums_gain) + audio['bass'] + audio['other']
    instrumental = instrumental.cpu().numpy()

    drums = (audio['drums'].cpu().numpy())

    # separator output is shape (channels, samples)
    # soundfile expects (samples, channels)
    vocals = vocals.T
    instrumental = instrumental.T
    drums = drums.T

    vocals_path = str(out_path / "vocals.wav")
    instrumental_path = str(out_path / "instrumental.wav")
    drums_path = str(out_path / "drums.wav")

    # Demucs standard sample rate is 44100 for htdemucs_ft
    sf.write(vocals_path, vocals, separator.samplerate)
    sf.write(instrumental_path, instrumental, separator.samplerate)
    # Original (unducked) drums stem. Kept for diagnostics/comparison and,
    # more importantly, reused downstream to sample real one-shots from the
    # track so the generated percussion can be built from the song's own
    # drum sounds rather than pure synthesis.
    sf.write(drums_path, drums, separator.samplerate)

    return vocals_path, instrumental_path, drums_path


import torchcrepe
import scipy.signal

def detect_syllables(vocals_wav: str):
    """
    Detects syllable events on the vocal stem using spectral onset detection.
    Returns a list of dicts: { "t": float, "strength": float, "f0": float, "periodicity": float, "dur": float }
    """
    # Load audio as mono, 22050 Hz for analysis
    sr_analysis = 22050
    y, sr = librosa.load(vocals_wav, sr=sr_analysis, mono=True)

    # 1. Use librosa's spectral onset detection
    hop_length = int(sr_analysis * 0.010) # 10 ms

    # Calculate onset envelope using spectral flux
    onset_env = librosa.onset.onset_strength(y=y, sr=sr_analysis, hop_length=hop_length)

    # Detect onsets with a minimum distance (e.g. ~60ms) and low threshold to catch unvoiced consonants
    peaks = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr_analysis,
        hop_length=hop_length,
        wait=int(0.060 / 0.010),
        pre_max=3,
        post_max=3,
        pre_avg=3,
        post_avg=5,
        delta=0.05,
        units='frames'
    )

    # 2. Get pitch and periodicity using torchcrepe
    # torchcrepe expects 16kHz audio, shape (1, samples)
    y_16k = librosa.resample(y, orig_sr=sr_analysis, target_sr=16000)
    audio_tensor = torch.from_numpy(y_16k).float().unsqueeze(0)

    fmin = 50
    fmax = 2000
    hop_length_crepe = int(16000 * 0.010) # 10 ms hop for alignment

    pitch, periodicity = torchcrepe.predict(
        audio_tensor,
        sample_rate=16000,
        hop_length=hop_length_crepe,
        fmin=fmin,
        fmax=fmax,
        model='tiny',
        return_periodicity=True
    )

    pitch = pitch.squeeze().numpy()
    periodicity = periodicity.squeeze().numpy()

    events = []

    # Normalize strength per track
    max_strength = np.max(onset_env) if len(onset_env) > 0 else 1.0

    for peak in peaks:
        # Align peak frame (22050Hz/10ms) to crepe frame (16000Hz/10ms)
        peak_time = peak * hop_length / sr_analysis

        crepe_frame = int(peak_time / 0.010)
        crepe_frame = min(crepe_frame, len(periodicity) - 1)

        onset_time = peak_time
        strength = onset_env[peak] / max_strength
        f0 = pitch[crepe_frame]
        per = periodicity[crepe_frame]

        events.append({
            "t": float(onset_time),
            "strength": float(strength),
            "f0": float(f0),
            "periodicity": float(per),
            "dur": 0.1
        })

    return events

import mido
import pyloudnorm as pyln


def _shaped_noise(duration, sr, band=None):
    """White noise, optionally band-limited, used as the "body" of a hit.

    Real percussion is mostly noise (broadband transient energy), not a pure
    tone. Leaning on shaped noise instead of a single sine gives the
    synthetic hits a broader spectral footprint that reads as a drum rather
    than a beep, and lets them sit alongside tonal content (bass/other)
    without simply doubling a single frequency.
    """
    n = int(sr * duration)
    noise = np.random.default_rng().standard_normal(n)
    if band is not None:
        sos = scipy.signal.butter(4, band, btype='bandpass', fs=sr, output='sos')
        noise = scipy.signal.sosfiltfilt(sos, noise)
    return noise


def _to_stereo(mono):
    """Duplicate a mono buffer into a (samples, 2) stereo buffer."""
    return np.column_stack((mono, mono))


def _normalize_peak(hit, target=1.0):
    """Scale so the absolute peak equals `target`, leaving silence untouched."""
    peak = np.max(np.abs(hit))
    if peak > 0:
        hit = hit / peak * target
    return hit


def _fold_to_range(freq, lo, hi):
    """Octave-fold `freq` into [lo, hi] so a pitch stays in a drum's register.

    Vocal fundamentals span a wide range; naively tuning a kick to a 400 Hz
    syllable would make it a mid tom. Folding by octaves keeps the drum in
    its natural register while still tracking the *pitch class* of the
    syllable, which is what makes the hit feel "in tune" with the vocal.
    """
    if freq is None or freq <= 0:
        return None
    f = float(freq)
    while f < lo:
        f *= 2
    while f > hi:
        f /= 2
    return f


def generate_hit(kind: str, sr=44100, pitch=None):
    """
    Synthesizes a percussive hit with a clear transient and a warm, tuned
    body instead of a thin decaying sine ("click"). Each hit blends a tonal
    component (for pitch identity) with a shaped-noise component (for the
    broadband transient real drums have).

    When `pitch` (a vocal fundamental in Hz) is provided, the tonal body is
    tuned to that pitch, octave-folded into the drum's natural register, so
    the generated hit matches the pitch of the syllable that triggered it.
    """
    if kind == 'low':  # kick: tuned sine sweep + short sub thump
        duration = 0.24
        t = np.linspace(0, duration, int(sr * duration), False)
        base = _fold_to_range(pitch, 40.0, 110.0) or 55.0
        # Exponential pitch drop from a punchy attack down to the tuned base
        # gives the classic kick "thump"; the tail settles on `base` so the
        # kick carries the syllable's pitch.
        f_start, f_end = base * 3.2, base
        freq = f_end + (f_start - f_end) * np.exp(-t * 16)
        phase = 2 * np.pi * np.cumsum(freq) / sr
        body = np.sin(phase) * np.exp(-t * 11)
        sub = np.sin(2 * np.pi * base * t) * np.exp(-t * 8) * 0.6
        click = _shaped_noise(duration, sr, band=[1200, 4500]) * np.exp(-t * 120)
        hit = body * 0.9 + sub + click * 0.18
    elif kind == 'mid':  # snare: tuned two-tone body + filtered noise crack
        duration = 0.2
        t = np.linspace(0, duration, int(sr * duration), False)
        base = _fold_to_range(pitch, 150.0, 300.0) or 190.0
        # Slightly detuned pair thickens the body and keeps it from sounding
        # like a pure sine beep.
        body = (np.sin(2 * np.pi * base * t) + 0.7 * np.sin(2 * np.pi * base * 1.5 * t))
        body *= np.exp(-t * 30)
        noise = _shaped_noise(duration, sr, band=[1500, 7000]) * np.exp(-t * 18)
        hit = body * 0.5 + noise * 0.8
    else:  # closed hat: short high-passed noise burst, optional faint ring
        duration = 0.08
        t = np.linspace(0, duration, int(sr * duration), False)
        noise = _shaped_noise(duration, sr, band=[6000, min(16000, sr / 2 - 100)])
        hit = noise * np.exp(-t * 60)
        ring = _fold_to_range(pitch, 3000.0, 8000.0)
        if ring is not None:
            hit = hit + 0.15 * np.sin(2 * np.pi * ring * t) * np.exp(-t * 90)

    hit = _normalize_peak(hit)
    return _to_stereo(hit)


def _estimate_pitch(mono, sr, fmin=40.0, fmax=400.0):
    """Best-effort dominant pitch (Hz) of a short sample, or None."""
    if len(mono) < int(sr * 0.02):
        return None
    try:
        f0 = librosa.yin(mono.astype(np.float64), fmin=fmin, fmax=fmax, sr=sr)
        f0 = f0[np.isfinite(f0)]
        f0 = f0[f0 > 0]
        if len(f0) == 0:
            return None
        return float(np.median(f0))
    except Exception:
        return None


def _repitch(sample_stereo, semitones, sr):
    """Pitch-shift a stereo sample by `semitones`, preserving its length."""
    if abs(semitones) < 0.1:
        return sample_stereo
    out = np.zeros_like(sample_stereo)
    for ch in range(sample_stereo.shape[1]):
        out[:, ch] = librosa.effects.pitch_shift(
            sample_stereo[:, ch].astype(np.float64), sr=sr, n_steps=semitones
        )
    return out


def build_drum_kit_from_stem(drums_wav: str, sr: int, min_hits: int = 6):
    """Sample real one-shots from the separated drum stem to build a kit.

    Rather than always synthesizing hits, this detects transients in the
    song's own drum stem, slices them into one-shots, and buckets them into
    low/mid/high by spectral centroid. The generated percussion can then be
    built from drum sounds that already belong to the track, which sits far
    more naturally in the mix than pure synthesis.

    Returns a dict like
        { 'low': {'sample': (n,2), 'pitch': float|None}, ... }
    for whichever buckets could be filled, or None if the stem does not yield
    enough usable transients (caller should fall back to synthesis).
    """
    try:
        y, file_sr = sf.read(drums_wav)
    except Exception:
        return None

    mono = y.mean(axis=1) if y.ndim > 1 else y
    if len(mono) < file_sr:  # need at least ~1s of material
        return None

    hop = 512
    try:
        onset_frames = librosa.onset.onset_detect(
            y=mono, sr=file_sr, hop_length=hop, backtrack=True, wait=2, delta=0.05
        )
    except Exception:
        return None

    if len(onset_frames) < min_hits:
        return None

    onset_samples = librosa.frames_to_samples(onset_frames, hop_length=hop)
    max_len = int(file_sr * 0.4)
    min_len = int(file_sr * 0.03)

    candidates = []
    for i, start in enumerate(onset_samples):
        nxt = onset_samples[i + 1] if i + 1 < len(onset_samples) else len(mono)
        end = min(start + max_len, nxt, len(mono))
        seg = mono[start:end]
        if len(seg) < min_len:
            continue
        # Short fade-out so slices don't click when they are cut before decay.
        fade_len = min(len(seg), int(file_sr * 0.01))
        window = np.ones(len(seg))
        window[-fade_len:] = np.linspace(1.0, 0.0, fade_len)
        seg = seg * window
        rms = float(np.sqrt(np.mean(seg ** 2)))
        if rms <= 0:
            continue
        centroid = float(np.mean(librosa.feature.spectral_centroid(y=seg, sr=file_sr)))
        candidates.append({"seg": seg, "centroid": centroid, "rms": rms})

    if len(candidates) < min_hits:
        return None

    centroids = np.array([c["centroid"] for c in candidates])
    lo_thr, hi_thr = np.percentile(centroids, [40, 70])

    buckets = {"low": [], "mid": [], "high": []}
    for c in candidates:
        if c["centroid"] <= lo_thr:
            buckets["low"].append(c)
        elif c["centroid"] <= hi_thr:
            buckets["mid"].append(c)
        else:
            buckets["high"].append(c)

    pitch_ranges = {
        "low": (40.0, 200.0),
        "mid": (120.0, 500.0),
        "high": (1000.0, 8000.0),
    }

    kit = {}
    for kind, members in buckets.items():
        if not members:
            continue
        # Pick the strongest (loudest) representative for a clean, punchy hit.
        best = max(members, key=lambda m: m["rms"])
        seg = best["seg"]
        if file_sr != sr:
            seg = librosa.resample(seg, orig_sr=file_sr, target_sr=sr)
        seg = _normalize_peak(seg, target=0.9)
        fmin, fmax = pitch_ranges[kind]
        pitch = _estimate_pitch(seg, sr, fmin=fmin, fmax=fmax)
        kit[kind] = {"sample": _to_stereo(seg), "pitch": pitch}

    if not kit:
        return None
    return kit


def group_events(events: list, min_gap: float = 0.06):
    """
    Merges onsets that land closer together than `min_gap` seconds into a
    single hit (keeping the strongest one of the group).

    Rap flow can produce syllable onsets only 30-60ms apart; rendering a hit
    per syllable at that density produces an indistinct wash of overlapping
    clicks rather than a recognizable beat. Enforcing a minimum inter-hit
    gap turns the dense onset stream into a sparser, more legible rhythmic
    pattern.
    """
    if not events:
        return []

    ordered = sorted(events, key=lambda e: e['t'])
    grouped = [ordered[0]]

    for e in ordered[1:]:
        if e['t'] - grouped[-1]['t'] < min_gap:
            if e['strength'] > grouped[-1]['strength']:
                grouped[-1] = e
        else:
            grouped.append(e)

    return grouped


def render_percussion(events: list, instrumental_wav: str, output_mix_wav: str, drums_wav: str = None):
    """
    Renders percussion from events and mixes with the instrumental.

    If `drums_wav` (the separated drum stem) is provided, real one-shots are
    sampled from it to build the kit; otherwise the kit is synthesized. Each
    hit is additionally tuned to the vocal fundamental (`f0`) of the syllable
    that triggered it, so the percussion tracks the pitch of the flow rather
    than firing a fixed-pitch click.
    """
    inst, sr = sf.read(instrumental_wav)
    if len(inst.shape) == 1:
        inst = np.column_stack((inst, inst))

    events = group_events(events)

    # Filter out unvoiced events for percentile calculation (to not skew the kick/snare thresholds)
    voiced_f0s = [e['f0'] for e in events if e.get('periodicity', 1.0) > 0.2 and e['f0'] > 0]
    if not voiced_f0s:
        voiced_f0s = [200]
    p33, p66 = np.percentile(voiced_f0s, [33, 66])

    # Prefer a kit sampled from the track's own drum stem; fall back to
    # synthesized hits for any bucket the stem could not fill (or entirely,
    # if no stem was provided / it yielded too few transients).
    sampled_kit = build_drum_kit_from_stem(drums_wav, sr) if drums_wav else None

    synth_samples = {
        'low': generate_hit('low', sr=sr),
        'mid': generate_hit('mid', sr=sr),
        'high': generate_hit('high', sr=sr),
    }

    # Registers used to fold a syllable's f0 into a musically sensible target
    # for each drum type when tuning the hit to the vocal pitch.
    tune_ranges = {'low': (40.0, 110.0), 'mid': (150.0, 300.0), 'high': (3000.0, 8000.0)}

    # Cache pitched variants keyed by (kind, rounded-semitone / rounded-pitch)
    # so repeated pitches don't repeatedly pay for pitch shifting / synthesis.
    variant_cache = {}

    def get_sample(kind, target_f0):
        target = _fold_to_range(target_f0, *tune_ranges[kind]) if target_f0 and target_f0 > 0 else None

        if sampled_kit and kind in sampled_kit:
            base = sampled_kit[kind]
            base_pitch = base['pitch']
            semitones = 0.0
            if target is not None and base_pitch and base_pitch > 0:
                # Bound the shift so sampled drums stay natural rather than
                # being warped into obviously artificial territory.
                semitones = float(np.clip(12.0 * np.log2(target / base_pitch), -6.0, 6.0))
            key = (kind, 'S', round(semitones))
            if key not in variant_cache:
                variant_cache[key] = _repitch(base['sample'], round(semitones), sr)
            return variant_cache[key]

        # Synthesized fallback: (re)generate the hit tuned to the target pitch.
        key = (kind, 'G', round(target) if target else 0)
        if key not in variant_cache:
            variant_cache[key] = generate_hit(kind, sr=sr, pitch=target) if target else synth_samples[kind]
        return variant_cache[key]

    perc_track = np.zeros_like(inst)
    # Sidechain-style ducking envelope: 1.0 = no ducking, dips toward
    # duck_floor briefly around every hit so the percussion punches through
    # the instrumental instead of getting masked by it.
    duck_envelope = np.ones(len(inst))
    duck_floor = 0.45  # ~ -7 dB dip under each hit
    duck_release_samples = int(sr * 0.12)

    # Also prepare MIDI
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)

    last_time_ticks = 0
    ticks_per_second = 1000 # easy mapping
    mid.ticks_per_beat = 500 # standard

    # Sort events by time
    events.sort(key=lambda x: x['t'])

    for e in events:
        t_sec = e['t']
        idx = int(t_sec * sr)
        per = e.get('periodicity', 1.0)
        f0 = e.get('f0', 0.0)

        # determine bucket
        if per <= 0.2:
            # Unvoiced consonants are explicitly mapped to hats
            kind = 'high'
            note = 42 # Closed Hat
        elif f0 < p33:
            kind = 'low'
            note = 36 # Kick
        elif f0 < p66:
            kind = 'mid'
            note = 38 # Snare
        else:
            kind = 'high'
            note = 42 # Closed Hat

        # Tune the hit to the syllable's pitch (skip for unvoiced hats, which
        # have no meaningful fundamental).
        tune_f0 = f0 if per > 0.2 else None
        samp = get_sample(kind, tune_f0)

        # Perceptual scaling, biased up from the original (0.6 exp, 0.8 max)
        # so hits are clearly audible rather than a subtle accent.
        gain = (e['strength'] ** 0.5)
        vel = int(min(127, max(1, e['strength'] * 127)))

        # Add to audio
        end_idx = min(idx + len(samp), len(perc_track))
        samp_len = end_idx - idx
        if samp_len > 0:
            perc_track[idx:end_idx] += samp[:samp_len] * gain

        # Duck the instrumental under this hit: quick dip, exponential release
        duck_end = min(idx + duck_release_samples, len(duck_envelope))
        duck_len = duck_end - idx
        if duck_len > 0:
            release = np.linspace(0, 1, duck_len)
            dip = duck_floor + (1.0 - duck_floor) * (1 - np.exp(-release * 8))
            duck_envelope[idx:duck_end] = np.minimum(duck_envelope[idx:duck_end], dip)

        # Add to MIDI
        t_ticks = int(t_sec * ticks_per_second)
        delta_ticks = t_ticks - last_time_ticks

        track.append(mido.Message('note_on', note=note, velocity=vel, time=delta_ticks))
        track.append(mido.Message('note_off', note=note, velocity=0, time=10)) # short duration
        last_time_ticks = t_ticks + 10

    midi_path = output_mix_wav.replace('.wav', '.mid')
    mid.save(midi_path)

    # Percussion-only diagnostic render, so the generated rhythm can be
    # listened to/verified in isolation from the instrumental bed.
    perc_only_path = output_mix_wav.replace('.wav', '_perc_only.wav')
    sf.write(perc_only_path, perc_track, sr)

    # Mix: duck the instrumental around each hit, then layer percussion at
    # (near) unity gain rather than attenuating it below the instrumental.
    ducked_inst = inst * duck_envelope[:, np.newaxis]
    mix = ducked_inst + perc_track

    # Loudness normalization
    meter = pyln.Meter(sr)
    loudness = meter.integrated_loudness(mix)

    # Calculate gain needed to reach -14 LUFS
    # gain_db = target_lufs - loudness
    gain_db = -14.0 - loudness
    gain_linear = 10.0 ** (gain_db / 20.0)

    normalized_mix = mix * gain_linear
    normalized_perc = perc_track * gain_linear
    normalized_inst = ducked_inst * gain_linear

    sf.write(output_mix_wav, normalized_mix, sr)

    # Write normalized individual stems
    sf.write(perc_only_path, normalized_perc, sr)

    inst_only_path = output_mix_wav.replace('.wav', '_inst_only.wav')
    sf.write(inst_only_path, normalized_inst, sr)

    return output_mix_wav, midi_path, perc_only_path, inst_only_path
