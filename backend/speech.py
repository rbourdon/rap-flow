"""The three speech models behind :mod:`syllables`, and nothing else.

Kept apart from :mod:`syllables` so that module stays plain numpy (and
testable without torch): everything here needs ``torch`` and ``transformers``
and is imported lazily by :func:`pipeline.detect_syllables`.

* :func:`phone_posteriors` - ``charsiu/en_w2v2_fc_10ms``, a wav2vec2 frame
  classifier over the CMU phone set (Zhu et al., "Phone-to-audio alignment
  without text", ICASSP 2022). 10 ms frames.
* :func:`transcribe_window` - ``openai/whisper-large-v3-turbo``, for the lyrics.
* :func:`refine_onsets` - a 0.5 MB CNN (``models/onset_refiner.pt``, trained on
  TIMIT's hand-labelled vowel onsets; see ``models/README.md``) that places
  each onset on a 2 ms grid.

Model weights are fetched from the Hugging Face hub on first use and cached
under ``HF_HOME`` (the worker points that at its model volume).
"""

import functools
import logging
import os
import re

import numpy as np

logger = logging.getLogger(__name__)

SR = 16000
PHONE_MODEL = os.environ.get("SYL_PHONE_MODEL", "charsiu/en_w2v2_fc_10ms")
ASR_MODEL = os.environ.get("SYL_ASR_MODEL", "openai/whisper-large-v3-turbo")
REFINER_PATH = os.environ.get(
    "SYL_ONSET_REFINER",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "onset_refiner.pt"),
)


def _device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


# ---------------------------------------------------------------------------
# Phone posteriors
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _phone_model():
    from transformers import Wav2Vec2ForCTC
    model = Wav2Vec2ForCTC.from_pretrained(PHONE_MODEL)
    return model.eval().to(_device())


def phone_posteriors(y16k, chunk_s=20.0, overlap_s=2.0):
    """Per-frame phone log-posteriors, ``(frames, 42)`` at a 10 ms hop.

    Column order is :data:`syllables.PHONES`. Long audio is run in overlapping
    chunks and cross-faded, so a chunk's context-starved edges count less.
    """
    import torch
    model = _phone_model()
    device = next(model.parameters()).device
    y16k = np.asarray(y16k, dtype=np.float32)
    hop = 160
    size, step = int(chunk_s * SR), int((chunk_s - overlap_s) * SR)
    n_frames = int(round(len(y16k) / hop))
    n_classes = model.config.vocab_size
    out = np.zeros((n_frames, n_classes), dtype=np.float32)
    weight = np.zeros(n_frames, dtype=np.float32)
    pos = 0
    while True:
        chunk = y16k[pos:pos + size]
        if len(chunk) < 400:
            break
        x = (chunk - chunk.mean()) / (chunk.std() + 1e-7)
        with torch.no_grad():
            logits = model(torch.from_numpy(x).unsqueeze(0).to(device)).logits[0]
            logp = torch.log_softmax(logits.float(), dim=-1).cpu().numpy()
        first = int(round(pos / hop))
        n = min(len(logp), n_frames - first)
        if n > 0:
            w = np.minimum(np.arange(1, n + 1), np.arange(n, 0, -1)).astype(np.float32)
            w = np.minimum(w, overlap_s * 100 / 2) + 1e-3
            out[first:first + n] += logp[:n] * w[:, None]
            weight[first:first + n] += w
        if pos + size >= len(y16k):
            break
        pos += step
    covered = weight > 0
    out[covered] /= weight[covered][:, None]
    out[~covered] = np.log(1.0 / n_classes)
    return out


# ---------------------------------------------------------------------------
# Lyrics
# ---------------------------------------------------------------------------
@functools.lru_cache(maxsize=1)
def _asr_model():
    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    device = _device()
    dtype = torch.float16 if device == "cuda" else torch.float32
    processor = WhisperProcessor.from_pretrained(ASR_MODEL)
    model = WhisperForConditionalGeneration.from_pretrained(ASR_MODEL, torch_dtype=dtype)
    return processor, model.eval().to(device)


_LANG_TOKEN = re.compile(r"<\|([a-z]{2,3})\|>")


def transcribe_window(y16k, t0=0.0, language=None):
    """Transcribe one window of at most 30 s.

    Returns ``[{"w", "start", "end", "seg_start", "seg_end", "lang"}]`` in song
    time (``t0`` is the window's offset). Whisper timestamps whole segments, not
    words: ``seg_*`` are the segment's bounds, and ``start``/``end`` spread its
    words evenly across it, which is only good enough to say which phrase a
    word belongs to. :func:`syllables.align_words` finds the real times.
    ``language=None`` lets Whisper detect it; the answer comes back as ``lang``.
    """
    import torch
    processor, model = _asr_model()
    param = next(model.parameters())
    y16k = np.asarray(y16k, dtype=np.float32)
    if len(y16k) < SR // 10:
        return []
    feats = processor(y16k, sampling_rate=SR, return_tensors="pt").input_features
    feats = feats.to(param.device, param.dtype)
    lang = language
    with torch.no_grad():
        if lang is None:
            token = processor.tokenizer.convert_ids_to_tokens(model.detect_language(feats))[0]
            found = _LANG_TOKEN.fullmatch(token)
            lang = found.group(1) if found else None
        ids = model.generate(
            feats, language=lang, task="transcribe", return_timestamps=True,
            num_beams=1, max_new_tokens=440,
        )[0].cpu()
    decoded = processor.tokenizer.decode(ids, output_offsets=True, skip_special_tokens=False,
                                         decode_with_timestamps=False)
    window_s = len(y16k) / SR
    words = []
    for off in decoded.get("offsets", []):
        a, b = off["timestamp"]
        if a is None:
            continue
        b = window_s if b is None else b
        tokens = re.sub(r"<\|[^|]*\|>", " ", off["text"]).split()
        for i, tok in enumerate(tokens):
            words.append({
                "w": tok,
                "start": t0 + a + (b - a) * i / len(tokens),
                "end": t0 + a + (b - a) * (i + 1) / len(tokens),
                "seg_start": t0 + a, "seg_end": t0 + b, "lang": lang,
            })
    return words


# ---------------------------------------------------------------------------
# Onset refiner
# ---------------------------------------------------------------------------
_R_HOP = 32            # 2 ms at 16 kHz
_R_HOP_S = _R_HOP / SR
_R_NFFT = 512
_R_WINS = (96, 400)    # 6 ms (sharp in time) and 25 ms (sharp in frequency)
_R_MELS = 40
_R_OUT = 35            # output bins each side of the coarse time: +-70 ms
_R_FLOOR_DB = -70.0


def _refiner_net(channels, dilations):
    import torch
    import torch.nn as nn

    class OnsetRefiner(nn.Module):
        """1-D CNN over time with mel bins as channels.

        Unpadded kernel-5 convolutions, so each of the 71 output bins sees the
        same +-40 ms of context and the net is translation-equivariant in time.
        """

        def __init__(self):
            super().__init__()
            self.ctx = _R_OUT + 2 * sum(dilations)
            layers = []
            for i, d in enumerate(dilations):
                layers += [
                    nn.Conv1d(_R_MELS * len(_R_WINS) if i == 0 else channels, channels, 5, dilation=d),
                    nn.BatchNorm1d(channels), nn.ReLU(inplace=True),
                ]
            self.body = nn.Sequential(*layers)
            self.head = nn.Conv1d(channels, 1, 1)
            self.pos_bias = nn.Parameter(torch.zeros(2 * _R_OUT + 1))
            self.register_buffer("fb", torch.zeros(_R_MELS, _R_NFFT // 2 + 1))

        def forward(self, x):
            return self.head(self.body(x)).squeeze(1) + self.pos_bias

    return OnsetRefiner()


@functools.lru_cache(maxsize=1)
def _refiner():
    import torch
    ckpt = torch.load(REFINER_PATH, map_location="cpu", weights_only=True)
    net = _refiner_net(int(ckpt.get("ch", 64)), tuple(ckpt.get("dils", (1, 2, 2, 2, 3))))
    net.load_state_dict(ckpt["state"])
    net.prior_sd_ms = float(ckpt.get("cfg", {}).get("prior_sd_ms", 30.0))
    return net.eval()


def _refiner_features(y16k, fb):
    """Two-resolution log-mel in dB, ``(80, T)``, frame ``i`` centred on sample ``i * 32``."""
    import torch
    y = torch.as_tensor(np.asarray(y16k, dtype=np.float32))
    n_frames = len(y) // _R_HOP + 1
    padded = torch.nn.functional.pad(y, (_R_NFFT // 2, _R_NFFT // 2 + _R_HOP))
    out = torch.empty(len(_R_WINS) * _R_MELS, n_frames)
    for a in range(0, n_frames, 8000):
        b = min(n_frames, a + 8000)
        seg = padded[a * _R_HOP:(b - 1) * _R_HOP + _R_NFFT]
        for k, win in enumerate(_R_WINS):
            w = torch.hann_window(win)
            spec = torch.stft(seg, _R_NFFT, hop_length=_R_HOP, win_length=win, window=w,
                              center=False, return_complex=True)
            power = (spec.real ** 2 + spec.imag ** 2) / float(w.sum()) ** 2
            out[k * _R_MELS:(k + 1) * _R_MELS, a:b] = 10.0 * torch.log10(fb @ power + 1e-10)
    return out


def refine_onsets(y16k, coarse_times, prior_sd_ms=None, min_gap_ms=30.0, batch=512):
    """Move coarse vowel onsets (good to a few 10 ms frames) onto the onset itself.

    For every coarse time the network scores each 2 ms step within +-70 ms as
    "the vowel starts here". Those scores are multiplied by a Gaussian prior
    centred on the coarse time (``prior_sd_ms``), so where the audio shows no
    clear landmark the answer stays put. Returns ``(times, confidence)``;
    confidence is the posterior mass within +-6 ms of the chosen time.

    If two neighbours are pulled to within ``min_gap_ms`` of each other, the
    less confident one goes back to its coarse time with confidence 0, so one
    acoustic landmark never produces two hits.
    """
    import torch
    coarse = np.asarray(coarse_times, dtype=np.float64)
    if coarse.size == 0:
        return coarse.copy(), np.zeros(0)
    net = _refiner()
    prior_sd_ms = net.prior_sd_ms if prior_sd_ms is None else float(prior_sd_ms)
    with torch.no_grad():
        feats = _refiner_features(y16k, net.fb)
        n_frames = feats.shape[1]
        centres = torch.from_numpy(np.round(coarse / _R_HOP_S).astype(np.int64))
        span = torch.arange(-net.ctx, net.ctx + 1)
        logits = []
        for a in range(0, len(centres), batch):
            idx = centres[a:a + batch, None] + span[None, :]
            inside = (idx >= 0) & (idx < n_frames)
            x = feats[:, idx.clamp(0, n_frames - 1)]
            x = torch.where(inside[None], x, torch.full_like(x, -100.0)).permute(1, 0, 2)
            # Each resolution is scaled by its own window maximum: gain-invariant.
            x = x.reshape(x.shape[0], len(_R_WINS), _R_MELS, -1)
            x = x - x.amax(dim=(2, 3), keepdim=True)
            x = x.clamp(min=_R_FLOOR_DB) / (-_R_FLOOR_DB / 2) + 1.0
            logits.append(net(x.reshape(x.shape[0], len(_R_WINS) * _R_MELS, -1)))
        logp = torch.log_softmax(torch.cat(logits), dim=1)
        offsets_ms = torch.arange(-_R_OUT, _R_OUT + 1, dtype=torch.float32) * (_R_HOP_S * 1000.0)
        if prior_sd_ms > 0:
            logp = logp - 0.5 * (offsets_ms / prior_sd_ms) ** 2
        post = torch.softmax(logp, dim=1)
        peak = post.argmax(1)
        near = (torch.arange(2 * _R_OUT + 1)[None, :] - peak[:, None]).abs() <= 3
        mass = (post * near).sum(1)
        shift_ms = (post * near * offsets_ms[None, :]).sum(1) / mass.clamp(min=1e-9)
    times = np.maximum(centres.numpy() * _R_HOP_S + shift_ms.numpy().astype(np.float64) / 1000.0, 0.0)
    conf = mass.numpy().astype(np.float64)
    if min_gap_ms and len(times) > 1:
        order = np.argsort(coarse, kind="stable")
        gap = min_gap_ms / 1000.0
        for a, b in zip(order[:-1], order[1:]):
            if times[b] - times[a] < min(gap, coarse[b] - coarse[a] - 1e-9):
                k = a if conf[a] < conf[b] else b
                times[k], conf[k] = coarse[k], 0.0
    return times, conf
