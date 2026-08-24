import os
import subprocess
import tempfile
import urllib.parse
import logging
from fractions import Fraction
from pathlib import Path

# Need numpy and others for later, just stubbing part 1
import numpy as np
import torch
import librosa
import soundfile as sf
import yt_dlp
import demucs.api


logger = logging.getLogger(__name__)


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

    # Source metadata captured from yt-dlp's info_dict (title/thumbnail/etc.) so
    # the frontend can give the job an identity. Empty for direct/local sources.
    metadata: dict = {}

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

                        # Capture identity metadata for the frontend. Guard each
                        # field: not every extractor supplies all of them.
                        metadata = {
                            "title": info_dict.get("title"),
                            "thumbnail": info_dict.get("thumbnail"),
                            "duration": int(duration) if duration else None,
                            "uploader": info_dict.get("uploader")
                            or info_dict.get("channel")
                            or info_dict.get("uploader_id"),
                        }

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

    return {"output_path": output_path, "metadata": metadata}

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

    Demucs vocal stems carry breaths, sibilance and separation artifacts that
    the raw spectral-flux detector happily fires on. Two gates prune those:
    an RMS gate discards onsets whose local vocal energy is well below the
    track's typical voiced level (breaths/artifacts sit in near-silence), and
    a strength gate discards the weakest normalized onsets.
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

    # Per-frame vocal RMS (10 ms hop, aligned to the onset frames) used to
    # gate out low-energy false onsets. The reference level is the median RMS
    # over voiced regions (periodicity > 0.2), i.e. where the rapper is
    # actually singing, so breaths and separation hiss fall well below it.
    rms = librosa.feature.rms(
        y=y, frame_length=2 * hop_length, hop_length=hop_length
    )[0]
    voiced_mask = (periodicity > 0.2)[:len(rms)] if len(periodicity) else np.zeros(len(rms), bool)
    voiced_rms = rms[:len(voiced_mask)][voiced_mask]
    median_voiced_rms = float(np.median(voiced_rms)) if len(voiced_rms) else 0.0
    rms_gate = 0.2 * median_voiced_rms
    # Window of +/-25 ms around an onset, in 10 ms frames.
    rms_half_win = 2

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

        # Strength gate: drop the weakest normalized onsets.
        if strength < 0.1:
            continue

        # RMS gate: drop onsets sitting in near-silence relative to the voiced
        # level (breaths, sibilance, Demucs artifacts).
        if rms_gate > 0:
            lo = max(0, peak - rms_half_win)
            hi = min(len(rms), peak + rms_half_win + 1)
            local_rms = float(np.mean(rms[lo:hi])) if hi > lo else 0.0
            if local_rms < rms_gate:
                continue

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

    When `pitch` (the track's median voiced vocal fundamental, in Hz) is
    provided, the tonal body is tuned to it, octave-folded into the drum's
    natural register, so the synthesized kit piece is tuned once to the track
    rather than retuned per hit.
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


def _varispeed_repitch(sample_stereo, source_pitch, target_pitch):
    """Repitch a stereo one-shot by *resampling* (varispeed), not phase vocoder.

    Playing a sample back faster raises its pitch and shortens it, the way a
    hardware sampler's "varispeed" tuning works. This preserves the drum's
    transient shape (a phase vocoder / `pitch_shift` smears it into a soft,
    unnatural blob), which matters far more for percussion than keeping the
    original length. The tuning happens once per kit piece, so the small
    change in decay length is inaudible.

    Returns the sample unchanged when either pitch is unknown or the ratio is
    within ~2%. The ratio is clamped to +/-1 octave so a bad pitch estimate
    can't warp a drum into obviously artificial territory.
    """
    if (source_pitch is None or source_pitch <= 0 or
            target_pitch is None or target_pitch <= 0):
        return sample_stereo
    ratio = float(target_pitch) / float(source_pitch)
    ratio = float(np.clip(ratio, 0.5, 2.0))
    if abs(ratio - 1.0) < 0.02:
        return sample_stereo
    # To raise pitch by `ratio` we speed the sample up by `ratio`, i.e. output
    # fewer samples: len_out = len_in / ratio. resample_poly(x, up, down)
    # scales length by up/down, so up/down = 1/ratio.
    frac = Fraction(1.0 / ratio).limit_denominator(200)
    up, down = frac.numerator, frac.denominator
    if up <= 0 or down <= 0:
        return sample_stereo
    out = [scipy.signal.resample_poly(sample_stereo[:, ch], up, down)
           for ch in range(sample_stereo.shape[1])]
    return np.column_stack(out)


def build_drum_kit_from_stem(drums_wav: str, sr: int, min_hits: int = 6,
                             candidates_per_bucket: int = 3):
    """Sample real one-shots from the separated drum stem to build a kit.

    Rather than always synthesizing hits, this detects transients in the
    song's own drum stem, slices them into one-shots, and buckets them into
    low/mid/high by spectral centroid. The generated percussion can then be
    built from drum sounds that already belong to the track, which sits far
    more naturally in the mix than pure synthesis.

    Slices are scored by isolation as well as loudness: an onset with a large
    gap (> 250 ms) to the next onset decays cleanly with little bleed from an
    overlapping kick/hat, so it makes a better one-shot. Up to
    `candidates_per_bucket` slices are kept per bucket (ranked by
    ``rms * isolation_bonus``) so the render can cycle through them and avoid
    machine-gun repetition of a single identical hit.

    Returns a dict like
        { 'low': [{'sample': (n,2), 'pitch': float|None}, ...], ... }
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
    isolation_gap = int(file_sr * 0.25)  # 250 ms of clean decay

    candidates = []
    for i, start in enumerate(onset_samples):
        nxt = onset_samples[i + 1] if i + 1 < len(onset_samples) else len(mono)
        gap = nxt - start
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
        # Well-isolated slices decay without an overlapping hit muddying them.
        isolation_bonus = 1.5 if gap >= isolation_gap else 1.0
        score = rms * isolation_bonus
        candidates.append({"seg": seg, "centroid": centroid, "rms": rms, "score": score})

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
        # Keep the top-scored representatives (isolation-weighted loudness) so
        # the render can round-robin through them instead of repeating one.
        members = sorted(members, key=lambda m: m["score"], reverse=True)
        fmin, fmax = pitch_ranges[kind]
        variants = []
        for member in members[:candidates_per_bucket]:
            seg = member["seg"]
            if file_sr != sr:
                seg = librosa.resample(seg, orig_sr=file_sr, target_sr=sr)
            seg = _normalize_peak(seg, target=0.9)
            pitch = _estimate_pitch(seg, sr, fmin=fmin, fmax=fmax)
            variants.append({"sample": _to_stereo(seg), "pitch": pitch})
        if variants:
            kit[kind] = variants

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


def track_beats(instrumental_wav: str):
    """Track beats on the **instrumental** stem.

    Returns ``(beat_times, tempo)`` where ``beat_times`` is a 1-D array of beat
    times in seconds and ``tempo`` is the estimated BPM. Beat tracking is run
    on the instrumental (not the vocals) because the syllable-derived
    percussion should lock to the musical grid of the backing track, not to
    the rapper's phrasing.

    The caller is expected to build its grid from the returned beat *times*
    (which follow tempo drift) rather than assuming a single constant BPM.
    """
    try:
        y, sr = librosa.load(instrumental_wav, sr=22050, mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units='time')
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Beat tracking failed on %s: %s", instrumental_wav, exc)
        return np.asarray([], dtype=float), 0.0
    tempo = float(np.atleast_1d(tempo)[0]) if np.size(tempo) else 0.0
    return np.asarray(beats, dtype=float), tempo


def _build_sixteenth_grid(beat_times):
    """Build a 16th-note grid by interpolating between consecutive beats.

    Returns parallel arrays ``(grid_times, grid_beat_index, grid_subdivision)``
    where ``grid_subdivision`` is 0-3 (0 = on the beat, 1-3 = the intervening
    16th notes). Interpolating between the *actual* beat times keeps the grid
    aligned even when the tempo drifts, which a fixed BPM grid would not.
    """
    times, beat_idx, sub_idx = [], [], []
    for i in range(len(beat_times) - 1):
        b0, b1 = beat_times[i], beat_times[i + 1]
        for s in range(4):
            times.append(b0 + (b1 - b0) * s / 4.0)
            beat_idx.append(i)
            sub_idx.append(s)
    # Include the final beat itself as an on-beat gridline.
    times.append(beat_times[-1])
    beat_idx.append(len(beat_times) - 1)
    sub_idx.append(0)
    return np.asarray(times), np.asarray(beat_idx), np.asarray(sub_idx)


def _estimate_downbeat_offset(beat_times, instrumental_wav):
    """Estimate the 4/4 downbeat phase (beat-index offset 0-3).

    Chooses the offset whose beats 1 and 3 (the strong beats of a 4/4 bar)
    capture the most onset energy in the instrumental, so kicks land where the
    track's own accents already fall.
    """
    if len(beat_times) < 4:
        return 0
    try:
        y, sr = librosa.load(instrumental_wav, sr=22050, mono=True)
        hop = 512
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
        env_times = librosa.frames_to_time(
            np.arange(len(onset_env)), sr=sr, hop_length=hop
        )
    except Exception:
        return 0
    beat_strengths = np.interp(beat_times, env_times, onset_env,
                               left=0.0, right=0.0)
    best_offset, best_energy = 0, -1.0
    for offset in range(4):
        # Beats 1 and 3 are bar positions 0 and 2, i.e. even beats relative
        # to the offset.
        mask = ((np.arange(len(beat_times)) - offset) % 2) == 0
        energy = float(np.sum(beat_strengths[mask]))
        if energy > best_energy:
            best_energy, best_offset = energy, offset
    return best_offset


# Metrical role -> (kit bucket, General MIDI note).
_ROLE_KICK = ("low", 36)
_ROLE_SNARE = ("mid", 38)
_ROLE_HAT = ("high", 42)


def _quantize_and_assign_roles(events, grid, offset, quantize_strength):
    """Quantize events to the 16th grid and assign metrical drum roles.

    ``grid`` is ``(grid_times, grid_beat_index, grid_subdivision)`` or None to
    disable quantization (degenerate beat tracking). Returns a time-sorted list
    of assignment dicts: ``{t, strength, kind, note}`` where ``kind`` is the kit
    bucket (low/mid/high) and ``note`` the GM drum note (36/38/42).

    Roles come from the *quantized metrical position*, not from pitch:
      - on-beat, bar beats 1 & 3 -> kick
      - on-beat, bar beats 2 & 4 -> snare
      - 8th/16th offbeats         -> hat
      - unvoiced events           -> hat (always)
    Density is gated per bar (<=4 kicks, <=2 snares; weakest overflow demoted
    to hats) and empty grid positions are left as rests.
    """
    if grid is None:
        # Fallback: keep raw times, alternate kick/snare on voiced events and
        # route unvoiced ones to hats. Musically crude, but only reached when
        # beat tracking is unreliable.
        assignments = []
        voiced_count = 0
        for e in sorted(events, key=lambda x: x['t']):
            per = e.get('periodicity', 1.0)
            if per <= 0.2:
                kind, note = _ROLE_HAT
            else:
                kind, note = _ROLE_KICK if voiced_count % 2 == 0 else _ROLE_SNARE
                voiced_count += 1
            assignments.append({'t': float(e['t']), 'strength': float(e['strength']),
                                'kind': kind, 'note': note})
        return assignments

    grid_times, grid_beat, grid_sub = grid

    # Snap each event to its nearest gridline, deduping collisions (keep the
    # stronger of any two events that land on the same 16th position).
    slots = {}
    for e in events:
        t = float(e['t'])
        k = int(np.argmin(np.abs(grid_times - t)))
        gt = float(grid_times[k])
        dt = gt - t
        if abs(dt) <= 0.015:  # within 15 ms -> snap fully
            qt = gt
        else:
            qt = t + quantize_strength * dt
        assign = {
            't': qt,
            'strength': float(e['strength']),
            'periodicity': float(e.get('periodicity', 1.0)),
            'beat_index': int(grid_beat[k]),
            'sub': int(grid_sub[k]),
        }
        prev = slots.get(k)
        if prev is None or assign['strength'] > prev['strength']:
            slots[k] = assign

    ordered = sorted(slots.values(), key=lambda a: a['t'])

    for a in ordered:
        if a['periodicity'] <= 0.2:
            a['kind'], a['note'] = _ROLE_HAT
        elif a['sub'] == 0:
            bar_pos = (a['beat_index'] - offset) % 4  # 0..3 -> beats 1..4
            a['kind'], a['note'] = _ROLE_KICK if bar_pos in (0, 2) else _ROLE_SNARE
        else:
            a['kind'], a['note'] = _ROLE_HAT

    _apply_density_gating(ordered, offset)
    return ordered


def _apply_density_gating(assignments, offset):
    """Cap kicks/snares per 4/4 bar, demoting weakest overflow to hats."""
    bars = {}
    for a in assignments:
        bar = (a['beat_index'] - offset) // 4
        bars.setdefault(bar, []).append(a)

    for items in bars.values():
        kicks = [a for a in items if a['kind'] == _ROLE_KICK[0]]
        snares = [a for a in items if a['kind'] == _ROLE_SNARE[0]]
        for a in sorted(kicks, key=lambda x: x['strength'])[:max(0, len(kicks) - 4)]:
            a['kind'], a['note'] = _ROLE_HAT
        for a in sorted(snares, key=lambda x: x['strength'])[:max(0, len(snares) - 2)]:
            a['kind'], a['note'] = _ROLE_HAT


def _tune_kit(sampled_kit, sr, median_f0):
    """Tune the whole kit ONCE to the track's median voiced vocal pitch.

    Kick/snare/hat are each fixed-pitch for the track: the median voiced vocal
    f0 is octave-folded into each drum's register and the kit piece is repitched
    a single time (by resampling for sampled one-shots, or generated at the
    target pitch for the synthesized fallback). Returns ``{kind: [sample, ...]}``
    with up to three variants per bucket for round-robin playback.
    """
    tune_ranges = {'low': (40.0, 110.0), 'mid': (150.0, 300.0), 'high': (3000.0, 8000.0)}
    kit = {}
    for kind in ('low', 'mid', 'high'):
        target = (_fold_to_range(median_f0, *tune_ranges[kind])
                  if median_f0 and median_f0 > 0 else None)
        variants = []
        if sampled_kit and kind in sampled_kit:
            for v in sampled_kit[kind]:
                variants.append(_varispeed_repitch(v['sample'], v['pitch'], target))
        else:
            variants.append(generate_hit(kind, sr=sr, pitch=target))
        kit[kind] = variants
    return kit


def _apply_duck(inst, env, sr, band_limited):
    """Apply a ducking gain envelope, optionally only to the low band.

    Band-limited ducking splits the instrumental with a ~400 Hz Linkwitz-Riley
    style crossover (4th-order Butterworth low band, complementary high band so
    the two sum back to the original) and ducks only the lows, where the kick
    and snare compete. This keeps hats, vocals-in-the-bed and cymbals from
    pumping. Full-band ducking is available as a fallback.
    """
    if not band_limited:
        return inst * env[:, np.newaxis]
    sos = scipy.signal.butter(4, 400.0, btype='low', fs=sr, output='sos')
    low = np.empty_like(inst)
    for ch in range(inst.shape[1]):
        low[:, ch] = scipy.signal.sosfiltfilt(sos, inst[:, ch])
    high = inst - low
    return low * env[:, np.newaxis] + high


def render_percussion(events: list, instrumental_wav: str, output_mix_wav: str,
                      drums_wav: str = None, quantize_strength: float = None,
                      duck_floor: float = None, duck_release_ms: float = None,
                      duck_band_limited: bool = None):
    """
    Renders percussion from events and mixes with the instrumental.

    The rhythm is locked to a beat-tracked 16th-note grid (built from the
    instrumental) via soft quantization, and drum roles are assigned by
    metrical position rather than vocal pitch: kicks on beats 1 & 3, snares on
    2 & 4, hats on the subdivisions. The kit is fixed-pitch for the track -
    tuned once to the median voiced vocal f0 - and, when a drum stem is
    provided, built from real one-shots sampled from it (cycled to avoid
    machine-gun repetition), otherwise synthesized. Kick and snare hits duck a
    band-limited (low-frequency) copy of the instrumental so the beat punches
    through without pumping the whole mix.

    Tunables default from the environment (``QUANTIZE_STRENGTH``,
    ``DUCK_FLOOR``, ``DUCK_RELEASE_MS``, ``DUCK_BAND_LIMITED``) and can be
    overridden per call.
    """
    if quantize_strength is None:
        quantize_strength = float(os.environ.get('QUANTIZE_STRENGTH', 0.65))
    if duck_floor is None:
        duck_floor = float(os.environ.get('DUCK_FLOOR', 0.7))
    if duck_release_ms is None:
        duck_release_ms = float(os.environ.get('DUCK_RELEASE_MS', 80.0))
    if duck_band_limited is None:
        env_v = os.environ.get('DUCK_BAND_LIMITED')
        duck_band_limited = (env_v.strip().lower() not in ('0', 'false', 'no', 'off')
                             if env_v is not None else True)

    inst, sr = sf.read(instrumental_wav)
    if len(inst.shape) == 1:
        inst = np.column_stack((inst, inst))

    # Merge near-coincident onsets BEFORE quantization (quantization may
    # collapse events onto the same gridline, handled during assignment).
    events = group_events(events)

    # Beat-track the instrumental and build a 16th-note grid. Degenerate
    # tracking (too few beats) disables quantization and falls back to raw
    # times with a warning.
    beat_times, tempo = track_beats(instrumental_wav)
    if len(beat_times) < 8:
        logger.warning(
            "Beat tracking degenerate (%d beats) on %s; rendering unquantized.",
            len(beat_times), instrumental_wav,
        )
        grid = None
        offset = 0
    else:
        grid = _build_sixteenth_grid(beat_times)
        offset = _estimate_downbeat_offset(beat_times, instrumental_wav)

    assignments = _quantize_and_assign_roles(events, grid, offset, quantize_strength)

    # Fixed-pitch kit tuned once to the track's median voiced vocal f0.
    voiced_f0s = [e['f0'] for e in events
                  if e.get('periodicity', 1.0) > 0.2 and e.get('f0', 0.0) > 0]
    median_f0 = float(np.median(voiced_f0s)) if voiced_f0s else None

    sampled_kit = build_drum_kit_from_stem(drums_wav, sr) if drums_wav else None
    kit = _tune_kit(sampled_kit, sr, median_f0)

    # Round-robin cursor per bucket so repeated hits aren't identical.
    rr = {'low': 0, 'mid': 0, 'high': 0}

    def next_sample(kind):
        variants = kit[kind]
        samp = variants[rr[kind] % len(variants)]
        rr[kind] += 1
        return samp

    perc_track = np.zeros_like(inst)
    # Sidechain-style ducking envelope: 1.0 = no ducking, dips toward
    # duck_floor around each kick/snare so the beat punches through. Every dip
    # has a short linear attack ramp and a linear release (no instantaneous
    # gain steps, which would click).
    duck_envelope = np.ones(len(inst))
    attack_samples = int(sr * 0.005)  # 5 ms attack ramp into every dip
    release_samples = int(sr * duck_release_ms / 1000.0)

    # MIDI with a real tempo so it imports on-grid into a DAW.
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    ticks_per_beat = 480
    mid.ticks_per_beat = ticks_per_beat
    bpm = tempo if tempo and tempo > 0 else 120.0
    tempo_us = mido.bpm2tempo(bpm)
    track.append(mido.MetaMessage('set_tempo', tempo=tempo_us, time=0))
    note_len_ticks = max(1, ticks_per_beat // 8)  # ~32nd-note gate
    last_tick = 0

    assignments.sort(key=lambda a: a['t'])

    for a in assignments:
        t_sec = a['t']
        idx = int(t_sec * sr)
        kind = a['kind']
        note = a['note']
        strength = a['strength']

        samp = next_sample(kind)

        # Perceptual gain curve: floor of 0.3, rising with strength, clamped to
        # 0.9 so nothing clips or dominates.
        gain = min(0.9, 0.3 + 0.6 * (strength ** 0.6))
        vel = int(min(127, max(1, strength * 127)))

        end_idx = min(idx + len(samp), len(perc_track))
        samp_len = end_idx - idx
        if samp_len > 0:
            perc_track[idx:end_idx] += samp[:samp_len] * gain

        # Only kick and snare duck the instrumental; hats do not.
        if kind in (_ROLE_KICK[0], _ROLE_SNARE[0]):
            a0 = max(0, idx - attack_samples)
            if idx > a0:
                ramp = np.linspace(1.0, duck_floor, idx - a0)
                duck_envelope[a0:idx] = np.minimum(duck_envelope[a0:idx], ramp)
            r_end = min(len(duck_envelope), idx + release_samples)
            if r_end > idx:
                rel = np.linspace(duck_floor, 1.0, r_end - idx)
                duck_envelope[idx:r_end] = np.minimum(duck_envelope[idx:r_end], rel)

        # MIDI: absolute tick from real time, delta-encoded.
        abs_tick = int(round(mido.second2tick(t_sec, ticks_per_beat, tempo_us)))
        delta = max(0, abs_tick - last_tick)
        track.append(mido.Message('note_on', note=note, velocity=vel, time=delta))
        track.append(mido.Message('note_off', note=note, velocity=0, time=note_len_ticks))
        last_tick = abs_tick + note_len_ticks

    midi_path = output_mix_wav.replace('.wav', '.mid')
    mid.save(midi_path)

    # Percussion-only diagnostic render, so the generated rhythm can be
    # listened to/verified in isolation from the instrumental bed.
    perc_only_path = output_mix_wav.replace('.wav', '_perc_only.wav')
    sf.write(perc_only_path, perc_track, sr)

    # Mix: duck the (low band of the) instrumental around kick/snare hits, then
    # layer percussion on top.
    ducked_inst = _apply_duck(inst, duck_envelope, sr, duck_band_limited)
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
