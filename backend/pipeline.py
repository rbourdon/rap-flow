import os
import subprocess
import tempfile
import urllib.parse
import logging
from pathlib import Path

# Lightweight, always-available deps. The heavy ML stack (torch, demucs,
# torchcrepe) and yt-dlp are imported lazily inside the stages that need them so
# the render/sample stage (and any importer that only needs sample_render, e.g.
# the isolated groove image) does not have to pull in the full torch/demucs
# stack just to import this module.
import numpy as np
import librosa
import soundfile as sf


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
    import yt_dlp
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

def separate_audio(input_wav: str, output_dir: str, drums_duck_db: float = -14.0):
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

    This is deeper than the -8 dB it used to be, and that change is load-bearing
    for the new backbone: the bed is now *transcribed from these same drums* and
    re-played with kit samples on top of them (see :mod:`backbone`), so the
    record's kick and the sampled kick both sound and any timing difference
    between them flams. Keeping the record's drums well under the kit is what
    stops the backbone smearing; the tight ``BACKBONE_SNAP_MS`` window is the
    other half of the same problem.
    """
    import torch  # noqa: F401 - kept for parity; demucs pulls torch in
    import demucs.api

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


import scipy.signal

# ---------------------------------------------------------------------------
# Syllable detection
#
# The detector answers one question: *where does each syllable start?* Every
# drum in the flow layer is placed at one of these times verbatim (see
# :mod:`flow`), so an event list that is not one-per-syllable makes a drum score
# that cannot line up with the voice no matter what happens downstream.
#
# Two detectors run side by side:
#   * **nuclei** - peaks in the smoothed vowel-band (~300-3400 Hz) loudness
#     envelope, masked to voiced frames. One peak == one syllable. This replaces
#     the old raw spectral-flux peak-picker, which fired on *any* spectral
#     change (breaths, sibilance, Demucs artefacts, note changes inside one held
#     vowel) and so both over-fired inside long vowels and missed soft syllables.
#   * **transients** - high-band (>= 4 kHz) flux peaks on *unvoiced* frames, i.e.
#     sibilants and plosives, which get their own drum role instead of being
#     discarded.
#
# The old flux detector is kept as :func:`_flux_events` and is used as a
# fallback when the nucleus detector comes back empty or implausibly sparse.
# ---------------------------------------------------------------------------

# Analysis rate / hop shared by every envelope here and by torchcrepe, so frame
# indices are directly comparable across detectors.
_ANALYSIS_SR = 22050
_HOP_SECONDS = 0.010
_N_FFT = 1024

# Vowel formant band used for the nucleus envelope.
_VOWEL_BAND_HZ = (300.0, 3400.0)
# High band used for the consonant/transient detector.
_TRANSIENT_BAND_HZ = 4000.0

# A transient up to ``TRANSIENT_SUPPRESS_MS`` *before* a nucleus attack is taken
# to be that syllable's own onset consonant, which the nucleus event already
# represents; a hat on it sounds as an early flam ahead of the syllable. Onsets
# like "st"/"str" run 80-200 ms, so the old 45 ms window let most of them
# through. It is a trade-off, not a classifier: in fast rap a word-final
# consonant also sits 50-160 ms before the next syllable. On the JamendoLyrics
# hip-hop tracks, 150 ms drops ~70-75% of word-initial consonant hats and keeps
# ~30-50% of word-final ones.
_TRANSIENT_SUPPRESS_MS_DEFAULT = 150.0

# Safety-net grouping applied *within* each event kind (nuclei are already
# one-per-syllable, so the old whole-stream grouping is gone).
_EVENT_GROUP_MIN_GAP = 0.045

# Window searched backwards from a nucleus peak for the syllable's attack.
_ATTACK_SEARCH_S = 0.080

# Below this many nuclei per second of voiced audio the nucleus detector is
# considered to have failed and the flux fallback takes over.
_MIN_NUCLEI_PER_VOICED_SECOND = 0.8

# Frames per torchcrepe forward pass. Left unset, torchcrepe puts the whole
# track in one batch, which for a 4-minute vocal is a multi-GB activation.
_CREPE_BATCH_FRAMES = 2048

# "Loud" vocal frames are those within this many dB of the vowel band's P95.
# If fewer than _MIN_VOICED_FRACTION of them read as voiced, the pitch tracker
# has failed (it is what a broken decoder looks like), not the vocal.
_LOUD_FRAME_RANGE_DB = 20.0
_MIN_VOICED_FRACTION = 0.15


def _env_float(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, TypeError, ValueError):
        return float(default)


def _env_flag(name, default):
    val = os.environ.get(name)
    if val is None:
        return bool(default)
    return val.strip().lower() not in ("0", "false", "no", "off")


def _syllable_params():
    """Resolve the syllable-detector tunables from the environment."""
    return {
        "detector": (os.environ.get("SYL_DETECTOR") or "nucleus").strip().lower(),
        "min_gap_ms": _env_float("SYL_MIN_GAP_MS", 70.0),
        "prominence_db": _env_float("SYL_PROMINENCE_DB", 3.0),
        "voiced_threshold": _env_float("SYL_VOICED_THRESHOLD", 0.35),
        "transient_enabled": _env_flag("TRANSIENT_ENABLED", True),
        "transient_min_gap_ms": _env_float("TRANSIENT_MIN_GAP_MS", 40.0),
        "transient_suppress_ms": _env_float(
            "TRANSIENT_SUPPRESS_MS", _TRANSIENT_SUPPRESS_MS_DEFAULT),
    }


def _smooth_frames(x, fps, win_seconds):
    """Smooth a frame-rate signal with a normalized Hann window."""
    n = max(3, int(round(win_seconds * fps)))
    if n % 2 == 0:
        n += 1
    if n >= len(x):
        return np.full_like(x, float(np.mean(x))) if len(x) else x
    win = np.hanning(n)
    win /= float(np.sum(win))
    return np.convolve(x, win, mode="same")


def _vowel_band_attack_envelope(y, sr, hop_length):
    """A high-time-resolution vowel-band envelope, for locating attacks.

    The nucleus envelope is built from a 1024-point STFT and smoothed over
    50 ms, which is what makes one vowel read as one peak — but that window
    smears an onset ~30 ms earlier than it happened, and the attack is the one
    number in an event that has to be right to the millisecond. So the attack
    search runs on a time-domain bandpass plus a single-hop RMS instead:
    frame-aligned to the STFT (both centred on ``i * hop``), ~±5 ms resolution.
    """
    sos = scipy.signal.butter(
        4, [_VOWEL_BAND_HZ[0], _VOWEL_BAND_HZ[1]], btype='band', fs=sr,
        output='sos',
    )
    band = scipy.signal.sosfilt(sos, y)
    rms = librosa.feature.rms(
        y=band, frame_length=hop_length, hop_length=hop_length, center=True
    )[0]
    return 20.0 * np.log10(rms + 1e-10)


def _band_energy(mag, freqs, lo_hz, hi_hz=None):
    """Sum STFT magnitude over a frequency band."""
    if hi_hz is None:
        band = freqs >= lo_hz
    else:
        band = (freqs >= lo_hz) & (freqs <= hi_hz)
    if not np.any(band):
        return np.zeros(mag.shape[1], dtype=float)
    return np.asarray(mag[band, :].sum(axis=0), dtype=float)


def _crepe_pitch(y, sr):
    """Run torchcrepe; returns ``(pitch, periodicity)``.

    The decoder has to be ``weighted_argmax``, not torchcrepe's default Viterbi.
    On a separated rap vocal the ``tiny`` model's per-frame activations are
    fairly flat (median peak ~0.6), and Viterbi's transition matrix, whose edge
    rows have fewer neighbours and so larger entries, then makes it cheaper to
    park the path on the top pitch bin: every frame decodes to ~1980 Hz and
    ``periodicity``, read at that bin, comes out ~0. Nothing is "voiced", the
    nucleus detector finds almost no syllables, and the transient detector
    fires on everything, so the flow layer became a stream of hats. The network
    itself was fine: argmax decoding of the same activations reads ~65% of the
    frames as voiced at a plausible pitch.
    """
    import torch
    import torchcrepe

    y_16k = librosa.resample(y, orig_sr=sr, target_sr=16000)
    audio_tensor = torch.from_numpy(y_16k).float().unsqueeze(0)
    pitch, periodicity = torchcrepe.predict(
        audio_tensor,
        sample_rate=16000,
        hop_length=int(16000 * _HOP_SECONDS),
        fmin=50,
        fmax=2000,
        model='tiny',
        decoder=torchcrepe.decode.weighted_argmax,
        return_periodicity=True,
        batch_size=_CREPE_BATCH_FRAMES,
    )
    pitch = np.atleast_1d(pitch.squeeze().numpy()).astype(float)
    periodicity = np.atleast_1d(periodicity.squeeze().numpy()).astype(float)
    return pitch, periodicity


def _align_to_frames(values, n_frames):
    """Resample a per-frame series onto ``n_frames`` frames of the same hop.

    torchcrepe and librosa disagree by a frame or two on how many frames a
    buffer yields; both use a 10 ms hop, so a linear resample over frame index
    keeps them aligned without a time shift.
    """
    values = np.asarray(values, dtype=float)
    if n_frames <= 0:
        return np.zeros(0, dtype=float)
    if len(values) == 0:
        return np.zeros(n_frames, dtype=float)
    if len(values) == n_frames:
        return values
    src = np.linspace(0.0, 1.0, len(values))
    dst = np.linspace(0.0, 1.0, n_frames)
    return np.interp(dst, src, values)


def _relative_stress(times, prominences, window_s=2.0):
    """Prominence relative to the loudest event in a ~``window_s`` window.

    Accents are a *local* judgement: a quiet passage should still get its own
    accents rather than being flattened by a shouted hook elsewhere in the
    track.
    """
    times = np.asarray(times, dtype=float)
    prominences = np.asarray(prominences, dtype=float)
    out = np.zeros(len(times), dtype=float)
    half = window_s / 2.0
    for i, t in enumerate(times):
        lo = np.searchsorted(times, t - half, side="left")
        hi = np.searchsorted(times, t + half, side="right")
        local_max = float(np.max(prominences[lo:hi])) if hi > lo else 0.0
        if local_max > 0:
            out[i] = float(np.clip(prominences[i] / local_max, 0.0, 1.0))
    return out


def _normalize_strength(prominences):
    """Normalize prominences by the track's **P95**, not its max.

    The old detector divided by ``np.max``, so a single outlier peak (a shout, a
    separation artefact) compressed every other strength toward zero and made
    the whole track render quiet.
    """
    prominences = np.asarray(prominences, dtype=float)
    if not len(prominences):
        return prominences
    ref = float(np.percentile(prominences, 95))
    if ref <= 0:
        ref = float(np.max(prominences)) or 1.0
    return np.clip(prominences / ref, 0.0, 1.0)


def _nucleus_events(mag, freqs, fps, periodicity, pitch, params,
                    attack_env_db=None):
    """Detect one event per voiced syllable from the vowel-band envelope."""
    energy = _band_energy(mag, freqs, *_VOWEL_BAND_HZ)
    env_db = 20.0 * np.log10(energy + 1e-10)
    env = _smooth_frames(env_db, fps, 0.050)
    if not len(env):
        return []
    # Work on a floored copy so "zeroing" unvoiced frames really is a floor.
    floor = float(np.min(env))
    env = env - floor
    # Peak-picking wants the 50 ms smoothing (it is what makes one vowel read as
    # one nucleus); locating the *attack* wants time resolution. See
    # :func:`_vowel_band_attack_envelope`.
    if attack_env_db is None:
        env_fine = env
    else:
        env_fine = _align_to_frames(attack_env_db, len(env))
        env_fine = env_fine - float(np.min(env_fine))

    # Peak prominence is gated in dB. The envelope is already log-scaled, so a
    # fixed dB prominence is level-independent on its own. The previous gate, a
    # fraction of the whole track's P90-P10 envelope range, grew with how much
    # of the track is silence or instrumental break rather than with how deep
    # the dips between syllables are (3-8 dB across a voiced consonant in
    # connected rap), and on real vocals it dropped a third or more of them.
    min_prominence = params["prominence_db"]

    voiced = periodicity >= params["voiced_threshold"]
    # Breaths and separation hiss sit in unvoiced frames; flooring them there
    # means they cannot form a nucleus peak at all.
    env_voiced = np.where(voiced, env, 0.0)

    distance = max(1, int(round(params["min_gap_ms"] / 1000.0 * fps)))
    candidates, _ = scipy.signal.find_peaks(env_voiced, distance=distance)
    if not len(candidates):
        return []

    # Prominence is measured on the *unmasked* envelope. Zeroing unvoiced frames
    # is what stops breaths forming peaks, but it also puts a cliff at every
    # voiced/unvoiced boundary, and a peak sitting next to that cliff would
    # inherit its prominence and pass the gate as a phantom syllable.
    prominences = scipy.signal.peak_prominences(env, candidates)[0]
    keep = prominences >= min_prominence
    keep &= periodicity[candidates] >= params["voiced_threshold"]
    peaks = candidates[keep]
    prominences = np.asarray(prominences[keep], dtype=float)
    if not len(peaks):
        return []

    hop = 1.0 / fps

    # Syllable boundaries are the minima between consecutive nuclei.
    boundaries = [max(0, int(peaks[0]) - distance)]
    for a, b in zip(peaks[:-1], peaks[1:]):
        seg = env_voiced[a:b]
        boundaries.append(int(a + (np.argmin(seg) if len(seg) else 0)))
    boundaries.append(min(len(env) - 1, int(peaks[-1]) + distance))

    attack_span = max(1, int(round(_ATTACK_SEARCH_S * fps)))
    strengths = _normalize_strength(prominences)

    events = []
    attack_frames = []
    for i, peak in enumerate(peaks):
        peak = int(peak)
        # ``t`` is the ATTACK, not the nucleus. Drums have to hit where the
        # syllable starts; using the loudness peak would place every hit late by
        # roughly half a vowel. The attack is the steepest rise of the envelope
        # in the window preceding the peak.
        lo = max(0, peak - attack_span)
        if i > 0:
            lo = max(lo, int(peaks[i - 1]) + 1)
        seg = env_fine[lo:peak + 1]
        if len(seg) >= 2:
            attack = lo + int(np.argmax(np.diff(seg)))
        else:
            attack = peak
        attack_frames.append(attack)

        dur = max(0.04, float(boundaries[i + 1] - boundaries[i]) * hop)
        frame = min(peak, len(periodicity) - 1)
        events.append({
            "t": float(attack * hop),
            "strength": float(strengths[i]),
            "f0": float(pitch[frame]) if len(pitch) else 0.0,
            "periodicity": float(periodicity[frame]) if len(periodicity) else 0.0,
            "dur": dur,
            "kind": "nucleus",
            "subtype": "voiced",
            "stress": 0.0,
        })

    stress = _relative_stress([e["t"] for e in events], prominences)
    for e, s in zip(events, stress):
        e["stress"] = float(s)
    return events


def _transient_events(mag, freqs, fps, periodicity, pitch, params,
                      nucleus_times):
    """Detect unvoiced consonants (sibilants, plosives) in the high band."""
    hf = _band_energy(mag, freqs, _TRANSIENT_BAND_HZ)
    if not len(hf):
        return []
    flux = np.maximum(0.0, np.diff(hf, prepend=float(hf[0])))
    flux = _smooth_frames(flux, fps, 0.015)

    p10, p90 = np.percentile(flux, [10, 90])
    min_prominence = 0.25 * max(float(p90 - p10), 1e-12)
    distance = max(1, int(round(params["transient_min_gap_ms"] / 1000.0 * fps)))
    peaks, props = scipy.signal.find_peaks(
        flux, distance=distance, prominence=min_prominence
    )
    if not len(peaks):
        return []

    # Adaptive HF floor: a real consonant sits well above the track's typical
    # high-band level, so a fixed threshold is not needed (or portable).
    hf_floor = float(np.percentile(hf, 70))
    prominences = np.asarray(props["prominences"], dtype=float)
    hop = 1.0 / fps
    decay_limit = int(round(0.150 * fps))
    suppress_s = params["transient_suppress_ms"] / 1000.0

    kept, kept_prom = [], []
    for peak, prom in zip(peaks, prominences):
        peak = int(peak)
        frame = min(peak, len(periodicity) - 1)
        if len(periodicity) and periodicity[frame] >= params["voiced_threshold"]:
            continue  # voiced: the nucleus detector owns this frame
        if hf[peak] <= hf_floor:
            continue

        t = float(peak * hop)
        # A transient just before a nucleus attack is that syllable's own onset
        # consonant, already represented by the nucleus event.
        if any(0.0 <= (nt - t) <= suppress_s for nt in nucleus_times):
            continue

        # Sub-classify by how long the high band keeps ringing: sustained noise
        # is a sibilant, a sharp burst is a plosive.
        half = hf[peak] * 0.5
        end = peak
        stop = min(len(hf), peak + decay_limit)
        while end + 1 < stop and hf[end + 1] > half:
            end += 1
        decay_s = float(end - peak) * hop
        subtype = "sibilant" if decay_s >= 0.040 else "plosive"

        kept.append({
            "t": t,
            "strength": 0.0,
            "f0": float(pitch[frame]) if len(pitch) else 0.0,
            "periodicity": float(periodicity[frame]) if len(periodicity) else 0.0,
            "dur": max(0.02, decay_s),
            "kind": "transient",
            "subtype": subtype,
            "stress": 0.0,
        })
        kept_prom.append(float(prom))

    if not kept:
        return []
    strengths = _normalize_strength(kept_prom)
    stress = _relative_stress([e["t"] for e in kept], kept_prom)
    for e, s, st in zip(kept, strengths, stress):
        e["strength"] = float(s)
        e["stress"] = float(st)
    return kept


def _flux_events(y, sr, pitch, periodicity):
    """The **previous** spectral-flux onset detector, kept as a fallback.

    Fires on any spectral change and prunes with two fixed thresholds
    (``strength < 0.12``, local RMS under a quarter of the voiced median) and a
    60 ms peak-picking wait. It over-fires inside long vowels and misses soft
    unvoiced syllables, which is why it is no longer the default - but it is a
    known-working escape hatch when the nucleus detector finds nothing.
    """
    hop_length = int(sr * _HOP_SECONDS)
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    peaks = librosa.onset.onset_detect(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=hop_length,
        wait=int(0.060 / _HOP_SECONDS),
        pre_max=3,
        post_max=3,
        pre_avg=3,
        post_avg=5,
        delta=0.05,
        units='frames',
    )

    rms = librosa.feature.rms(
        y=y, frame_length=2 * hop_length, hop_length=hop_length
    )[0]
    voiced_mask = (periodicity > 0.2)[:len(rms)] if len(periodicity) else np.zeros(len(rms), bool)
    voiced_rms = rms[:len(voiced_mask)][voiced_mask]
    median_voiced_rms = float(np.median(voiced_rms)) if len(voiced_rms) else 0.0
    rms_gate = 0.25 * median_voiced_rms
    rms_half_win = 2

    max_strength = np.max(onset_env) if len(onset_env) > 0 else 1.0
    events = []
    for peak in peaks:
        peak_time = peak * hop_length / sr
        crepe_frame = min(int(peak_time / _HOP_SECONDS), len(periodicity) - 1)
        strength = onset_env[peak] / max_strength
        if strength < 0.12:
            continue
        if rms_gate > 0:
            lo = max(0, peak - rms_half_win)
            hi = min(len(rms), peak + rms_half_win + 1)
            local_rms = float(np.mean(rms[lo:hi])) if hi > lo else 0.0
            if local_rms < rms_gate:
                continue
        per = float(periodicity[crepe_frame]) if len(periodicity) else 0.0
        events.append({
            "t": float(peak_time),
            "strength": float(strength),
            "f0": float(pitch[crepe_frame]) if len(pitch) else 0.0,
            "periodicity": per,
            "dur": 0.1,
            # Flux onsets carry no nucleus/consonant distinction, so they are
            # typed by voicing alone and the flow layer treats them as syllables.
            "kind": "nucleus",
            "subtype": "voiced" if per >= 0.35 else "plosive",
            "stress": float(strength),
        })
    return events


def _voicing_warning(mag, freqs, fps, periodicity, voiced_threshold):
    """A warning if the pitch tracker calls a clearly audible vocal unvoiced.

    Rap is mostly voiced, so when almost none of the loud vowel-band frames
    clear ``voiced_threshold`` the tracker has failed, not the vocal. That is
    exactly what the Viterbi-decoder bug in :func:`_crepe_pitch` looked like,
    and it went unnoticed because the nucleus-rate check divides by the same
    (near-zero) voiced time. Returns ``None`` when the vocal is too short or too
    quiet to judge.
    """
    energy_db = 20.0 * np.log10(_band_energy(mag, freqs, *_VOWEL_BAND_HZ) + 1e-10)
    if not len(energy_db) or not len(periodicity):
        return None
    ref = float(np.percentile(energy_db, 95))
    if ref < -80.0:
        return None  # effectively silent: nothing to judge
    loud = energy_db >= ref - _LOUD_FRAME_RANGE_DB
    if np.sum(loud) < fps:  # under a second of audible vocal
        return None
    # ``periodicity`` is already aligned to the STFT frames (same length).
    fraction = float(np.mean(periodicity[loud] >= voiced_threshold))
    if fraction >= _MIN_VOICED_FRACTION:
        return None
    return (
        f"Pitch tracker read only {fraction:.0%} of the audible vocal as voiced; "
        f"syllable detection is unreliable on this track."
    )


def detect_syllables(vocals_wav: str):
    """Detect syllable events on the vocal stem.

    Returns ``{"events": [...], "detector": "nucleus"|"flux", "warning": str|None}``.

    Each event is::

        {"t": 12.418, "strength": 0.72, "f0": 148.3, "periodicity": 0.81,
         "dur": 0.19, "kind": "nucleus"|"transient",
         "subtype": "voiced"|"sibilant"|"plosive", "stress": 0.64}

    ``t`` is the syllable's **attack** and is the only timing the flow layer
    ever uses - no stage downstream is allowed to move a flow hit off it.
    """
    import rhythm

    params = _syllable_params()

    y, sr = librosa.load(vocals_wav, sr=_ANALYSIS_SR, mono=True)
    hop_length = int(sr * _HOP_SECONDS)
    fps = sr / float(hop_length)

    mag = np.abs(librosa.stft(y, n_fft=_N_FFT, hop_length=hop_length))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=_N_FFT)
    n_frames = mag.shape[1]

    pitch, periodicity = _crepe_pitch(y, sr)
    pitch = _align_to_frames(pitch, n_frames)
    periodicity = _align_to_frames(periodicity, n_frames)

    warnings = []
    voicing_warning = _voicing_warning(mag, freqs, fps, periodicity,
                                       params["voiced_threshold"])
    if voicing_warning:
        logger.warning(voicing_warning)
        warnings.append(voicing_warning)
    detector = params["detector"] if params["detector"] in ("nucleus", "flux") else "nucleus"

    events = []
    if detector == "nucleus":
        attack_env = _vowel_band_attack_envelope(y, sr, hop_length)
        nuclei = _nucleus_events(mag, freqs, fps, periodicity, pitch, params,
                                 attack_env_db=attack_env)
        voiced_seconds = float(np.sum(periodicity >= params["voiced_threshold"])) / fps
        rate = (len(nuclei) / voiced_seconds) if voiced_seconds > 0 else 0.0
        if not nuclei or rate < _MIN_NUCLEI_PER_VOICED_SECOND:
            fallback_warning = (
                f"Nucleus syllable detector produced {len(nuclei)} events "
                f"({rate:.2f}/voiced-second); fell back to the spectral-flux detector."
            )
            logger.warning(fallback_warning)
            warnings.append(fallback_warning)
            detector = "flux"
        else:
            transients = []
            if params["transient_enabled"]:
                transients = _transient_events(
                    mag, freqs, fps, periodicity, pitch, params,
                    [e["t"] for e in nuclei],
                )
            # Safety net applied *within* each kind: nuclei are already
            # one-per-syllable, so grouping across the whole stream (as the old
            # pipeline did) would silently drop consonant events.
            events = (
                rhythm.group_events(nuclei, min_gap=_EVENT_GROUP_MIN_GAP)
                + rhythm.group_events(transients, min_gap=_EVENT_GROUP_MIN_GAP)
            )

    if detector == "flux":
        events = rhythm.group_events(
            _flux_events(y, sr, pitch, periodicity), min_gap=_EVENT_GROUP_MIN_GAP
        )

    events.sort(key=lambda e: e["t"])
    logger.info(
        "detect: %d events (%s detector)", len(events), detector
    )
    warning = " ".join(warnings) if warnings else None
    return {"events": events, "detector": detector, "warning": warning}


import mido
import pyloudnorm as pyln


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


# Roles that duck the instrumental (kick & snare); hats/cymbals/toms do not.
# Membership in this set is *not* sufficient on its own: the duck is driven only
# by notes whose ``layer`` is "bed". Flow ghosts are quiet snares, so a
# class-only test would make every ghost note pump the instrumental.
_DUCKING_CLASSES = ("kick", "snare")
_DUCKING_LAYER = "bed"

# Clamp per-beat tempi derived from beat tracking to a sane BPM range so a
# spurious beat interval can't emit an absurd MIDI tempo.
_MIDI_MIN_BPM = 20.0
_MIDI_MAX_BPM = 320.0

# Static carve in the instrumental under the kit's kick, always on. Cutting a
# narrow notch where the sampled kick's fundamental sits makes room for it
# without the blunt whole-low-band duck the old render used.
_KICK_NOTCH_DB = -3.0
_KICK_NOTCH_Q = 1.4
_KICK_NOTCH_FALLBACK_HZ = 65.0


def _build_midi_tempo_map(beat_times, ticks_per_beat, fallback_tempo_us):
    """Build a drifting MIDI tempo map + a seconds->absolute-tick mapper.

    Given the tracked ``beat_times`` (seconds), place beat *i* at tick
    ``i * ticks_per_beat`` and set one ``set_tempo`` per beat interval to that
    interval's real duration. A DAW then reconstructs each note's wall-clock time
    while showing a bar grid that follows the song's tempo changes.

    Returns ``(tempo_changes, sec_to_tick)`` where ``tempo_changes`` is a list of
    ``(abs_tick, tempo_us)`` (sorted, tick >= 0) and ``sec_to_tick`` maps a time
    in seconds to an absolute tick along the same piecewise-linear beat grid.
    Falls back to a single constant tempo when the beat grid is degenerate.
    """
    beats = np.asarray(beat_times, dtype=float) if beat_times is not None else None
    if beats is None or len(beats) < 2:
        def sec_to_tick(t):
            return int(round(mido.second2tick(max(0.0, t), ticks_per_beat,
                                              fallback_tempo_us)))
        return [(0, fallback_tempo_us)], sec_to_tick

    beats = np.sort(beats)
    n = len(beats)
    min_us = int(round(60_000_000.0 / _MIDI_MAX_BPM))
    max_us = int(round(60_000_000.0 / _MIDI_MIN_BPM))

    tempo_changes = []
    for i in range(n - 1):
        dur = beats[i + 1] - beats[i]
        if dur <= 0:
            continue
        tempo_us = int(round(dur * 1_000_000.0))
        tempo_us = max(min_us, min(max_us, tempo_us))
        tempo_changes.append((i * ticks_per_beat, tempo_us))
    if not tempo_changes:
        tempo_changes = [(0, fallback_tempo_us)]

    def sec_to_tick(t):
        if t <= beats[0]:
            dur = beats[1] - beats[0]
            tick = (t - beats[0]) / dur * ticks_per_beat if dur > 0 else 0.0
        elif t >= beats[-1]:
            dur = beats[-1] - beats[-2]
            base = (n - 1) * ticks_per_beat
            tick = base + ((t - beats[-1]) / dur * ticks_per_beat if dur > 0 else 0.0)
        else:
            i = int(np.searchsorted(beats, t, side="right")) - 1
            dur = beats[i + 1] - beats[i]
            frac = (t - beats[i]) / dur if dur > 0 else 0.0
            tick = (i + frac) * ticks_per_beat
        return max(0, int(round(tick)))

    return tempo_changes, sec_to_tick


def _resolve(value, env_name, default, cast=float):
    """Resolve a render tunable: explicit argument, then env, then default."""
    if value is not None:
        try:
            return cast(value) if cast is not bool else _as_bool(value)
        except (TypeError, ValueError):
            return default
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        return cast(raw) if cast is not bool else _as_bool(raw)
    except (TypeError, ValueError):
        return default


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ('0', 'false', 'no', 'off')


def _kick_notch_hz(kit):
    """Dominant low frequency of the kit's loudest kick sample."""
    import bus as _bus

    layers = kit.layers.get("kick") or []
    if not layers:
        return _KICK_NOTCH_FALLBACK_HZ
    variants = layers[-1]  # loudest velocity layer
    if not variants:
        return _KICK_NOTCH_FALLBACK_HZ
    return _bus.dominant_frequency(variants[0], kit.sr) or _KICK_NOTCH_FALLBACK_HZ


def _remap_missing_classes(notes, kit):
    """Fall back to ``hat_closed`` for flow classes the kit does not ship.

    ``FLOW_ACCENT_CLASS`` defaults to ``ride`` because ``kits/default`` has no
    rim/side-stick samples; a kit that also lacks a ride would otherwise lose
    every accent silently. Validated here, at render time, because this is the
    first point where the kit is actually loaded.
    """
    warned = set()
    out = []
    for note in notes:
        drum_class = note.get("drum_class")
        if (drum_class and note.get("layer") == "flow" and not kit.has(drum_class)
                and kit.has(_sampler_fallback_class())):
            if drum_class not in warned:
                logger.warning(
                    "Kit has no %r samples; flow hits fall back to %r.",
                    drum_class, _sampler_fallback_class(),
                )
                warned.add(drum_class)
            note = dict(note)
            note["drum_class"] = _sampler_fallback_class()
            note["midi_note"] = _class_to_midi()[_sampler_fallback_class()]
        out.append(note)
    return out


def _sampler_fallback_class():
    import flow as _flow
    return _flow.ACCENT_FALLBACK_CLASS


def _class_to_midi():
    import sampler as _sampler
    return _sampler.CLASS_TO_MIDI


def balance_sub_buses(sub_buses: dict, layer_balance: float):
    """Sum the flow/bed sub-buses with equal-power balance gains.

    Separated out because this is where "total percussion level" exists as a
    single thing: downstream the mix goes through a program-dependent limiter
    and loudness normalization, whose time-varying gain is shared with the
    instrumental and therefore cannot be used to reason about the balance law.
    """
    import bus as _bus

    flow_gain, bed_gain = _bus.layer_gains(layer_balance)
    gains = {"flow": flow_gain, "bed": bed_gain}
    total = None
    for name, buf in sub_buses.items():
        scaled = (buf * gains.get(name, bed_gain)).astype(np.float32)
        total = scaled if total is None else total + scaled
    return total if total is not None else np.zeros((0, 2), dtype=np.float32)


def _bed_duck_envelope(placed, n_samples, sr, duck_floor, duck_release_ms):
    """Ducking envelope driven **only** by bed kick/snare hits."""
    env = np.ones(n_samples)
    attack_samples = int(sr * 0.005)
    release_samples = int(sr * duck_release_ms / 1000.0)
    for a in placed:
        if a.get("layer") != _DUCKING_LAYER:
            continue
        if a["drum_class"] not in _DUCKING_CLASSES:
            continue
        idx = int(a['t'] * sr)
        a0 = max(0, idx - attack_samples)
        if idx > a0:
            ramp = np.linspace(1.0, duck_floor, idx - a0)
            env[a0:idx] = np.minimum(env[a0:idx], ramp)
        r_end = min(n_samples, idx + release_samples)
        if r_end > idx:
            rel = np.linspace(duck_floor, 1.0, r_end - idx)
            env[idx:r_end] = np.minimum(env[idx:r_end], rel)
    return env


def _write_midi(placed, tempo, beat_times, midi_path):
    """Export the placed score as a .mid with a drifting tempo map."""
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    ticks_per_beat = 480
    mid.ticks_per_beat = ticks_per_beat
    bpm = tempo if tempo and tempo > 0 else 120.0
    fallback_tempo_us = mido.bpm2tempo(bpm)
    tempo_changes, sec_to_tick = _build_midi_tempo_map(
        beat_times, ticks_per_beat, fallback_tempo_us)
    note_len_ticks = max(1, ticks_per_beat // 8)

    # Merge tempo metas and note on/off events on an absolute-tick timeline, then
    # emit them in order as delta times. Rank orders ties at the same tick so a
    # tempo change and note_off precede a note_on.
    events = []  # (abs_tick, rank, builder)
    for abs_tick, tempo_us in tempo_changes:
        events.append((abs_tick, 0,
                       lambda d, tu=tempo_us: mido.MetaMessage('set_tempo', tempo=tu, time=d)))
    for a in placed:
        on_tick = sec_to_tick(a['t'])
        off_tick = on_tick + note_len_ticks
        vel = int(min(127, max(1, a['velocity'])))
        note = int(a['midi_note'])
        events.append((off_tick, 1,
                       lambda d, nt=note: mido.Message('note_off', note=nt, velocity=0, time=d)))
        events.append((on_tick, 2,
                       lambda d, nt=note, v=vel: mido.Message('note_on', note=nt, velocity=v, time=d)))

    events.sort(key=lambda e: (e[0], e[1]))
    last_tick = 0
    for abs_tick, _rank, builder in events:
        delta = max(0, abs_tick - last_tick)
        track.append(builder(delta))
        last_tick = abs_tick

    mid.save(midi_path)
    return midi_path


def sample_render(drum_score: dict, instrumental_wav: str, output_mix_wav: str,
                  kit_dir: str = None, duck_floor: float = None,
                  duck_release_ms: float = None, duck_band_limited: bool = None,
                  layer_balance: float = None, drum_drive: float = None,
                  drum_room: float = None, drum_glue: bool = None,
                  limiter_ceiling_dbtp: float = None,
                  render_layer_stems: bool = None):
    """Render a drum score to audio and mix it with the instrumental.

    This is the *sound* layer. The score's vocal-locked timing is preserved
    verbatim — no quantization happens here, and no hit is moved — but the kit
    no longer sits *on top of* a mastered record:

    * the two layers render into separate sub-buses, each transient-shaped per
      class, soft-clip saturated and given a short (fixed-seed) room send;
    * ``layer_balance`` mixes them with equal-power gains, so the total
      percussion level stays put across the sweep and the control changes only
      the balance. It is a **bus gain**, which is why changing it invalidates
      only the render cache and a re-render takes seconds;
    * the summed percussion bus gets glue compression;
    * the instrumental gets a static notch at the kit kick's own fundamental
      plus the existing envelope duck — now driven **only** by ``layer: "bed"``
      kick and snare hits, since flow ghosts are snares and would otherwise make
      the bed pump on every syllable;
    * a real true-peak limiter replaces the old whole-mix gain multiply, then
      the mix is loudness-normalized to -14 LUFS.

    Writes ``mix.wav``, ``mix_perc_only.wav``, ``mix_inst_only.wav`` and the
    ``.mid`` export. With ``RENDER_LAYER_STEMS=1`` it additionally writes
    ``mix_flow_only.wav`` / ``mix_bed_only.wav`` for local debugging (these are
    not uploaded). Returns ``(mix, midi, perc_only, inst_only)`` paths.
    """
    import sampler as _sampler
    import bus as _bus

    duck_floor = _resolve(duck_floor, 'DUCK_FLOOR', 0.7)
    duck_release_ms = _resolve(duck_release_ms, 'DUCK_RELEASE_MS', 80.0)
    duck_band_limited = _resolve(duck_band_limited, 'DUCK_BAND_LIMITED', True, bool)
    layer_balance = _resolve(layer_balance, 'LAYER_BALANCE', 0.5)
    drum_drive = _resolve(drum_drive, 'DRUM_DRIVE', 1.6)
    drum_room = _resolve(drum_room, 'DRUM_ROOM', 0.14)
    drum_glue = _resolve(drum_glue, 'DRUM_GLUE', True, bool)
    ceiling_dbtp = _resolve(limiter_ceiling_dbtp, 'LIMITER_CEILING_DBTP', -1.0)
    layer_stems = _resolve(render_layer_stems, 'RENDER_LAYER_STEMS', False, bool)
    if kit_dir is None:
        kit_dir = os.environ.get('KIT_DIR', _sampler.DEFAULT_KIT_DIR)

    inst, sr = sf.read(instrumental_wav)
    if len(inst.shape) == 1:
        inst = np.column_stack((inst, inst))

    notes = drum_score.get('notes', []) if isinstance(drum_score, dict) else drum_score
    tempo = float(drum_score.get('tempo', 0.0)) if isinstance(drum_score, dict) else 0.0
    beat_times = drum_score.get('beat_times') if isinstance(drum_score, dict) else None

    kit = _sampler.DrumKit.load(kit_dir, sr)
    notes = _remap_missing_classes(notes, kit)

    # 1. Two sub-buses, with per-class transient shaping on each one-shot.
    sub_buses, placed = _sampler.render_drum_score(
        notes, len(inst), sr, kit,
        shaper=lambda sample, cls: _bus.shape_sample(sample, sr, cls),
        split_layers=True,
    )
    placed.sort(key=lambda a: a['t'])

    total_len = max([len(b) for b in sub_buses.values()] + [len(inst)])
    if len(inst) < total_len:
        pad = np.zeros((total_len - len(inst), inst.shape[1]), dtype=inst.dtype)
        inst = np.vstack((inst, pad))

    # 2-3. Saturation and room send, per sub-bus. One IR instance, generated
    # from a fixed seed, so identical inputs render identically.
    room_impulse = _bus.room_ir(sr) if drum_room else None
    processed = {}
    for name, buf in sub_buses.items():
        if len(buf) < total_len:
            buf = np.vstack((buf, np.zeros((total_len - len(buf), 2),
                                           dtype=buf.dtype)))
        buf = _bus.saturate(buf, drum_drive)
        if drum_room:
            buf = _bus.room_send(buf, sr, drum_room, ir=room_impulse)
        processed[name] = buf

    # 4. Equal-power balance, then sum to one percussion bus.
    flow_gain, bed_gain = _bus.layer_gains(layer_balance)
    flow_bus = processed.get("flow")
    bed_bus = processed.get("bed")
    perc_track = balance_sub_buses(processed, layer_balance)

    # 5. Bus glue on the summed percussion.
    if drum_glue:
        perc_track = _bus.glue_compress(perc_track, sr)

    # 6. Bed processing: static kick carve + bed-driven envelope duck.
    notch_hz = _kick_notch_hz(kit)
    inst_processed = _bus.peaking_eq(inst, sr, notch_hz, _KICK_NOTCH_DB,
                                     _KICK_NOTCH_Q)
    duck_envelope = _bed_duck_envelope(placed, total_len, sr, duck_floor,
                                       duck_release_ms)
    inst_processed = _apply_duck(inst_processed, duck_envelope, sr,
                                 duck_band_limited)

    midi_path = _write_midi(placed, tempo, beat_times,
                            output_mix_wav.replace('.wav', '.mid'))

    mix = inst_processed + perc_track

    # 7. True-peak limiter *before* loudness normalization, then re-measure and
    # normalize, then a second pass as the safety clamp. Every gain is applied
    # identically to the stems so `perc + inst` still equals `mix` (the player
    # sums the two stems at unity).
    stems = [perc_track, inst_processed]
    if layer_stems:
        stems += [
            (flow_bus if flow_bus is not None else np.zeros_like(perc_track)) * flow_gain,
            (bed_bus if bed_bus is not None else np.zeros_like(perc_track)) * bed_gain,
        ]

    def _apply_gain(g):
        nonlocal mix, stems
        if np.isscalar(g):
            mix = mix * g
            stems = [s * g for s in stems]
        else:
            mix = mix * g[:, np.newaxis]
            stems = [s * g[:, np.newaxis] for s in stems]

    _apply_gain(_bus.true_peak_gain(mix, sr, ceiling_dbtp))

    # Normalize, then limit again as the safety clamp — and repeat, because the
    # second limiter pass is program-dependent and can itself pull the loudness
    # back down (heavily transient material cannot reach -14 LUFS under a
    # -1 dBTP ceiling in one pass). Two or three iterations converge; the loop
    # exits as soon as the measurement is on target.
    meter = pyln.Meter(sr)
    for _ in range(3):
        loudness = meter.integrated_loudness(mix)
        if not np.isfinite(loudness):
            break
        gain_db = -14.0 - loudness
        if abs(gain_db) < 0.1:
            break
        _apply_gain(10.0 ** (gain_db / 20.0))
        _apply_gain(_bus.true_peak_gain(mix, sr, ceiling_dbtp))

    ceiling = 10.0 ** (ceiling_dbtp / 20.0)
    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > ceiling:
        _apply_gain(ceiling / peak)

    perc_out, inst_out = stems[0], stems[1]

    sf.write(output_mix_wav, mix, sr)
    perc_only_path = output_mix_wav.replace('.wav', '_perc_only.wav')
    inst_only_path = output_mix_wav.replace('.wav', '_inst_only.wav')
    sf.write(perc_only_path, perc_out, sr)
    sf.write(inst_only_path, inst_out, sr)

    if layer_stems:
        sf.write(output_mix_wav.replace('.wav', '_flow_only.wav'), stems[2], sr)
        sf.write(output_mix_wav.replace('.wav', '_bed_only.wav'), stems[3], sr)

    return output_mix_wav, midi_path, perc_only_path, inst_only_path
