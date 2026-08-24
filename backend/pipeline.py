import os
import subprocess
import tempfile
import urllib.parse
import logging
from fractions import Fraction
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
    import torch
    import torchcrepe

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
    rms_gate = 0.25 * median_voiced_rms
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

        # Strength gate: drop the weakest normalized onsets. These are usually
        # separation artifacts / breaths that would become spurious taps.
        if strength < 0.12:
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
_DUCKING_CLASSES = ("kick", "snare")


def sample_render(drum_score: dict, instrumental_wav: str, output_mix_wav: str,
                  kit_dir: str = None, duck_floor: float = None,
                  duck_release_ms: float = None, duck_band_limited: bool = None):
    """Render a drum score to audio and mix it with the instrumental.

    This is the *sound* layer: the drum score (from the ``groove`` stage) is
    played back through the velocity-layered, round-robin real-sample engine in
    :mod:`sampler`. The vocal-locked timing in the score is preserved verbatim -
    no quantization happens here. Kick and snare hits duck a band-limited copy of
    the instrumental, then the mix is loudness-normalized to -14 LUFS.

    Writes ``mix.wav``, ``mix_perc_only.wav``, ``mix_inst_only.wav`` and the
    ``.mid`` export (built from the score with real tempo meta), matching the
    previous output shape. Returns ``(mix, midi, perc_only, inst_only)`` paths.
    """
    import sampler as _sampler

    if duck_floor is None:
        duck_floor = float(os.environ.get('DUCK_FLOOR', 0.7))
    if duck_release_ms is None:
        duck_release_ms = float(os.environ.get('DUCK_RELEASE_MS', 80.0))
    if duck_band_limited is None:
        env_v = os.environ.get('DUCK_BAND_LIMITED')
        duck_band_limited = (env_v.strip().lower() not in ('0', 'false', 'no', 'off')
                             if env_v is not None else True)
    if kit_dir is None:
        kit_dir = os.environ.get('KIT_DIR', _sampler.DEFAULT_KIT_DIR)

    inst, sr = sf.read(instrumental_wav)
    if len(inst.shape) == 1:
        inst = np.column_stack((inst, inst))

    notes = drum_score.get('notes', []) if isinstance(drum_score, dict) else drum_score
    tempo = float(drum_score.get('tempo', 0.0)) if isinstance(drum_score, dict) else 0.0

    # Play the score through the real-sample engine.
    kit = _sampler.DrumKit.load(kit_dir, sr)
    perc_track, placed = _sampler.render_drum_score(notes, len(inst), sr, kit)

    # Match lengths: pad the instrumental if a drum tail runs past its end.
    if len(perc_track) > len(inst):
        pad = np.zeros((len(perc_track) - len(inst), inst.shape[1]), dtype=inst.dtype)
        inst = np.vstack((inst, pad))
    elif len(perc_track) < len(inst):
        pad = np.zeros((len(inst) - len(perc_track), 2), dtype=perc_track.dtype)
        perc_track = np.vstack((perc_track, pad))

    # Sidechain-style ducking envelope driven by kick/snare hits.
    duck_envelope = np.ones(len(inst))
    attack_samples = int(sr * 0.005)
    release_samples = int(sr * duck_release_ms / 1000.0)

    # MIDI export from the drum score with a real tempo meta message.
    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    ticks_per_beat = 480
    mid.ticks_per_beat = ticks_per_beat
    bpm = tempo if tempo and tempo > 0 else 120.0
    tempo_us = mido.bpm2tempo(bpm)
    track.append(mido.MetaMessage('set_tempo', tempo=tempo_us, time=0))
    note_len_ticks = max(1, ticks_per_beat // 8)
    last_tick = 0

    placed.sort(key=lambda a: a['t'])
    for a in placed:
        idx = int(a['t'] * sr)
        if a['drum_class'] in _DUCKING_CLASSES:
            a0 = max(0, idx - attack_samples)
            if idx > a0:
                ramp = np.linspace(1.0, duck_floor, idx - a0)
                duck_envelope[a0:idx] = np.minimum(duck_envelope[a0:idx], ramp)
            r_end = min(len(duck_envelope), idx + release_samples)
            if r_end > idx:
                rel = np.linspace(duck_floor, 1.0, r_end - idx)
                duck_envelope[idx:r_end] = np.minimum(duck_envelope[idx:r_end], rel)

        abs_tick = int(round(mido.second2tick(a['t'], ticks_per_beat, tempo_us)))
        delta = max(0, abs_tick - last_tick)
        vel = int(min(127, max(1, a['velocity'])))
        track.append(mido.Message('note_on', note=a['midi_note'], velocity=vel, time=delta))
        track.append(mido.Message('note_off', note=a['midi_note'], velocity=0, time=note_len_ticks))
        last_tick = abs_tick + note_len_ticks

    midi_path = output_mix_wav.replace('.wav', '.mid')
    mid.save(midi_path)

    perc_only_path = output_mix_wav.replace('.wav', '_perc_only.wav')

    # Mix: duck the (low band of the) instrumental around kick/snare hits, then
    # layer percussion on top.
    ducked_inst = _apply_duck(inst, duck_envelope, sr, duck_band_limited)
    mix = ducked_inst + perc_track

    # Loudness normalization to -14 LUFS.
    meter = pyln.Meter(sr)
    loudness = meter.integrated_loudness(mix)
    gain_db = -14.0 - loudness
    gain_linear = 10.0 ** (gain_db / 20.0)

    normalized_mix = mix * gain_linear
    normalized_perc = perc_track * gain_linear
    normalized_inst = ducked_inst * gain_linear

    # Safety limiter / true-peak guard. Loudness-normalizing a drum layer on top
    # of the bed can push peaks past 0 dBFS, which clips and adds crackle. If the
    # mix exceeds the ceiling, pull the whole mix down by the overshoot (applied
    # equally to the stems so their relationship is preserved) so nothing clips.
    ceiling = 0.985
    peak = float(np.max(np.abs(normalized_mix))) if normalized_mix.size else 0.0
    if peak > ceiling:
        limiter_gain = ceiling / peak
        normalized_mix = normalized_mix * limiter_gain
        normalized_perc = normalized_perc * limiter_gain
        normalized_inst = normalized_inst * limiter_gain

    sf.write(output_mix_wav, normalized_mix, sr)
    sf.write(perc_only_path, normalized_perc, sr)

    inst_only_path = output_mix_wav.replace('.wav', '_inst_only.wav')
    sf.write(inst_only_path, normalized_inst, sr)

    return output_mix_wav, midi_path, perc_only_path, inst_only_path
