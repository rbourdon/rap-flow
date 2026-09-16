"""Staged, durable workflow for the rap-flow audio pipeline.

The heavy pipeline (see :mod:`pipeline`) is a short, linear DAG:

    ingest -> separate -> detect -> render -> finalize

Historically ``worker.py`` ran all of these inside a single Modal function, so
any failure - or any desire to re-run one stage with different parameters -
meant redoing *everything*, including the expensive/flaky YouTube download and
the GPU stem separation.

This module factors that DAG into independent, **cache-aware** stages. Each
stage writes its outputs to a durable artifact root (a Modal Volume in
production, a local directory for the CLI) under a key derived from the
*content of its inputs*. A stage that finds its output already present simply
reuses it instead of recomputing, which is what lets us:

  * reuse an existing download instead of re-downloading, and
  * retry / reprocess an individual stage (e.g. re-render with new ducking
    settings) while reusing the already-separated stems.

The functions here are pure Python and contain no Modal-specific code, so they
are equally usable from ``worker.py`` (each wrapped in its own Modal function)
and from ``cli.py`` (run locally, in-process). ``worker.py`` owns the
orchestration and progress callbacks; this module owns the compute + caching.
"""

import os
import re
import json
import hashlib
import urllib.parse

import pipeline


# Machine-readable stage identifiers, in execution order.
STAGES = ["ingest", "separate", "detect", "groove", "render", "finalize"]
STAGE_INDEX = {name: i for i, name in enumerate(STAGES)}

# Human-readable labels surfaced to the UI progress tracker. These must stay in
# sync with the ``STAGES`` array in ``frontend/src/components/JobProgress.tsx``.
STAGE_LABELS = {
    "ingest": "Downloading Audio",
    "separate": "Separating Vocals",
    "detect": "Detecting syllables",
    "groove": "Building the groove",
    "render": "Synthesizing Beats",
    "finalize": "Saving Results",
}

# Default artifact root. In Modal this is a persisted Volume mount; locally the
# CLI overrides it with a directory inside the output folder.
DEFAULT_ARTIFACTS_ROOT = os.environ.get("RAP_FLOW_ARTIFACTS", "/artifacts")

# Bumped whenever the *format* of a cached stage artifact changes. Both
# events.json (typed nucleus/transient events) and drum_score.json (two tagged
# layers) changed shape in the two-layer percussion rewrite; without this, a
# re-run on an already-processed source would hand old-format artifacts to new
# code and silently produce nonsense.
PIPELINE_VERSION = "2"

# Parameter keys that influence stem separation (and therefore the stems cache).
SEPARATE_PARAM_KEYS = ["drums_duck_db"]
# Values baked into the separate cache key when the caller does not override
# them. The pipeline default moved from -8 dB to -14 dB (the backbone is now
# transcribed from these same drums and replayed on top of them), and the stems
# key has to reflect the value actually used or a cached source would keep
# serving the old, louder instrumental.
SEPARATE_PARAM_DEFAULTS = {"drums_duck_db": -14.0}
# Parameter keys that influence syllable detection (and therefore events.json).
DETECT_PARAM_KEYS = [
    "syl_detector",
    "syl_min_gap_ms",
    "syl_prominence",
    "syl_voiced_threshold",
    "transient_enabled",
    "transient_min_gap_ms",
]
# Parameter keys that influence the groove (drum-score) stage: the backbone and
# flow layers. `layer_balance` deliberately does NOT live here - it is a render
# bus gain, so moving the slider invalidates only the render.
GROOVE_PARAM_KEYS = [
    "backbone_source",
    "backbone_snap_ms",
    "backbone_fill",
    "backbone_hats",
    "flow_accent_class",
    "flow_min_gap_ms",
    "merge_snare_guard_ms",
]
# Parameter keys that influence the percussion render (and therefore the render
# cache). Reprocessing with any of these changed produces a fresh cache key, so
# a re-render never returns a stale result.
RENDER_PARAM_KEYS = [
    "kit",
    "duck_floor",
    "duck_release_ms",
    "duck_band_limited",
    "layer_balance",
    "drum_drive",
    "drum_room",
    "drum_glue",
    "limiter_ceiling_dbtp",
    "render_layer_stems",
]


# ---------------------------------------------------------------------------
# Hashing / key helpers
# ---------------------------------------------------------------------------

def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: str, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_source(source_url: str) -> str:
    """Return a stable identifier for a media source.

    Two requests for the same YouTube video should hit the same ingest cache
    even if the URL differs cosmetically (``youtu.be`` vs ``watch?v=`` vs extra
    query params), so we canonicalize to the bare video id when we can. For any
    other source (SoundCloud, a direct file URL, an uploaded blob) we fall back
    to the trimmed URL as-is.
    """
    if not source_url:
        return ""
    url = source_url.strip()
    parsed = urllib.parse.urlparse(url)
    host = (parsed.netloc or "").lower()

    # youtu.be/<id>
    if host.endswith("youtu.be"):
        vid = parsed.path.lstrip("/").split("/")[0]
        if vid:
            return f"youtube:{vid}"

    # youtube.com/watch?v=<id> (and /shorts/<id>, /embed/<id>)
    if "youtube.com" in host:
        qs = urllib.parse.parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return f"youtube:{qs['v'][0]}"
        m = re.match(r"^/(?:shorts|embed|v)/([^/?#]+)", parsed.path)
        if m:
            return f"youtube:{m.group(1)}"

    return url


def _params_signature(params: dict, keys, defaults: dict = None) -> str:
    """Stable signature of the subset of ``params`` in ``keys``.

    ``None`` (meaning "use the pipeline/env default") is included explicitly so
    the signature is stable whether a caller passes the key or omits it —
    unless ``defaults`` supplies the value the pipeline would actually use, in
    which case that value is hashed so a changed default invalidates the cache.
    """
    params = params or {}
    defaults = defaults or {}
    subset = {
        k: (params[k] if params.get(k) is not None else defaults.get(k))
        for k in keys
    }
    return json.dumps(subset, sort_keys=True)


def _separate_signature(params: dict) -> str:
    return _params_signature(params, SEPARATE_PARAM_KEYS, SEPARATE_PARAM_DEFAULTS)


# ---------------------------------------------------------------------------
# Artifact location helpers
# ---------------------------------------------------------------------------

def source_key(source_url: str) -> str:
    return _sha256_text(normalize_source(source_url))


def source_dir(root: str, source_url: str) -> str:
    return os.path.join(root, "sources", source_key(source_url))


def source_input_path(root: str, source_url: str) -> str:
    return os.path.join(source_dir(root, source_url), "input.wav")


def _stems_key(input_hash: str, params: dict) -> str:
    return _sha256_text(input_hash + "|" + _separate_signature(params))


def stems_dir(root: str, input_hash: str, params: dict) -> str:
    return os.path.join(root, "inputs", _stems_key(input_hash, params))


def _detect_key(input_hash: str, params: dict) -> str:
    sig = (
        _separate_signature(params)
        + "|"
        + _params_signature(params, DETECT_PARAM_KEYS)
        + "|v"
        + PIPELINE_VERSION
    )
    return _sha256_text(input_hash + "|" + sig)


def detect_dir(root: str, input_hash: str, params: dict) -> str:
    """Where ``events.json`` lives.

    Detection has its own keyed directory (rather than sharing the stems one)
    so that ``PIPELINE_VERSION`` and the detector tunables can invalidate the
    event list without also invalidating the expensive Demucs separation.
    """
    return os.path.join(root, "events", _detect_key(input_hash, params))


def events_path(root: str, input_hash: str, params: dict) -> str:
    return os.path.join(detect_dir(root, input_hash, params), "events.json")


def _groove_key(input_hash: str, params: dict, inputs_hash: str = "") -> str:
    sig = (
        _separate_signature(params)
        + "|"
        + _params_signature(params, GROOVE_PARAM_KEYS)
        + "|v"
        + PIPELINE_VERSION
        + "|"
        + (inputs_hash or "")
    )
    return _sha256_text(input_hash + "|" + sig)


def groove_dir(root: str, input_hash: str, params: dict,
               inputs_hash: str = "") -> str:
    return os.path.join(root, "grooves",
                        _groove_key(input_hash, params, inputs_hash))


def groove_inputs_hash(root: str, input_hash: str, params: dict) -> str:
    """Hash of the two artifacts the groove stage reads.

    The stage now consumes ``drums.wav`` (transcribed into the backbone) as
    well as ``events.json``, so both have to be in its cache key: a re-detected
    event list or a re-separated drum stem must not be answered with the old
    drum score.
    """
    parts = []
    for path in (os.path.join(stems_dir(root, input_hash, params), "drums.wav"),
                 events_path(root, input_hash, params)):
        parts.append(_sha256_file(path) if os.path.exists(path) else "missing")
    return _sha256_text("|".join(parts))


def _render_key(input_hash: str, params: dict, inputs_hash: str = "") -> str:
    sig = (
        _separate_signature(params)
        + "|"
        + _params_signature(params, GROOVE_PARAM_KEYS)
        + "|"
        + _params_signature(params, RENDER_PARAM_KEYS)
        + "|v"
        + PIPELINE_VERSION
        + "|"
        + (inputs_hash or "")
    )
    return _sha256_text(input_hash + "|" + sig)


def render_dir(root: str, input_hash: str, params: dict,
               inputs_hash: str = "") -> str:
    return os.path.join(root, "renders",
                        _render_key(input_hash, params, inputs_hash))


def _filter_params(params: dict, keys) -> dict:
    """Return the subset of ``params`` in ``keys`` whose value is not ``None``.

    ``None`` values are dropped so the pipeline falls back to its own env-driven
    defaults rather than being forced to ``None``.
    """
    params = params or {}
    return {k: params[k] for k in keys if params.get(k) is not None}


# ---------------------------------------------------------------------------
# Stages
#
# Each stage returns a dict that always includes ``"reused"`` (True when the
# cached output was reused and no work was done). Stages derive all of their
# input/output locations from ``source_url`` + ``params``, so any stage can run
# on its own as long as its upstream artifacts already exist in ``root`` - this
# is what makes ``from_stage`` re-runs possible.
# ---------------------------------------------------------------------------

def _metadata_path(root: str, source_url: str) -> str:
    return os.path.join(source_dir(root, source_url), "metadata.json")


def stage_ingest(root: str, source_url: str, params: dict = None,
                 force: bool = False, yt_cookies: str = None,
                 yt_proxy: str = None) -> dict:
    """Download/normalize the source audio to ``input.wav`` (cache-aware)."""
    input_wav = source_input_path(root, source_url)
    os.makedirs(os.path.dirname(input_wav), exist_ok=True)
    meta_path = _metadata_path(root, source_url)

    if not force and os.path.exists(input_wav):
        metadata = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    metadata = json.load(f)
            except (ValueError, OSError):
                metadata = {}
        return {"input_wav": input_wav, "metadata": metadata, "reused": True}

    res = pipeline.ingest_audio(
        source_url, input_wav, yt_cookies=yt_cookies, yt_proxy=yt_proxy
    )
    metadata = res.get("metadata") or {} if isinstance(res, dict) else {}
    try:
        with open(meta_path, "w") as f:
            json.dump(metadata, f)
    except OSError:
        pass
    return {"input_wav": input_wav, "metadata": metadata, "reused": False}


def stage_separate(root: str, source_url: str, params: dict = None,
                   force: bool = False) -> dict:
    """Separate ``input.wav`` into vocals/instrumental/drums stems (cache-aware)."""
    params = params or {}
    input_wav = source_input_path(root, source_url)
    if not os.path.exists(input_wav):
        raise pipeline.IngestError(
            "MISSING_ARTIFACT: input.wav not found; run the 'ingest' stage first."
        )

    input_hash = _sha256_file(input_wav)
    out_dir = stems_dir(root, input_hash, params)
    os.makedirs(out_dir, exist_ok=True)

    vocals = os.path.join(out_dir, "vocals.wav")
    instrumental = os.path.join(out_dir, "instrumental.wav")
    drums = os.path.join(out_dir, "drums.wav")

    if not force and all(os.path.exists(p) for p in (vocals, instrumental, drums)):
        return {
            "input_hash": input_hash,
            "vocals_wav": vocals,
            "instrumental_wav": instrumental,
            "drums_wav": drums,
            "reused": True,
        }

    sep_kwargs = _filter_params(params, SEPARATE_PARAM_KEYS)
    vocals, instrumental, drums = pipeline.separate_audio(
        input_wav, out_dir, **sep_kwargs
    )
    return {
        "input_hash": input_hash,
        "vocals_wav": vocals,
        "instrumental_wav": instrumental,
        "drums_wav": drums,
        "reused": False,
    }


def stage_detect(root: str, source_url: str, params: dict = None,
                 force: bool = False) -> dict:
    """Detect syllables from the vocals stem -> events.json (cache-aware).

    ``pipeline.detect_syllables`` runs the vowel-nucleus detector (one event per
    syllable) plus the consonant/transient detector, falling back to the old
    spectral-flux detector and surfacing a non-fatal warning if the nucleus
    detector comes back empty or implausibly sparse. ``events.json`` itself
    stays a plain array so the frontend's marker overlay is unaffected.
    """
    params = params or {}
    input_wav = source_input_path(root, source_url)
    if not os.path.exists(input_wav):
        raise pipeline.IngestError(
            "MISSING_ARTIFACT: input.wav not found; run the 'ingest' stage first."
        )
    input_hash = _sha256_file(input_wav)
    vocals = os.path.join(stems_dir(root, input_hash, params), "vocals.wav")
    if not os.path.exists(vocals):
        raise pipeline.IngestError(
            "MISSING_ARTIFACT: vocals stem not found; run the 'separate' stage first."
        )

    out_dir = detect_dir(root, input_hash, params)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "events.json")
    meta_path = os.path.join(out_dir, "detect_meta.json")

    if not force and os.path.exists(out_path):
        with open(out_path) as f:
            events = json.load(f)
        meta = {}
        if os.path.exists(meta_path):
            try:
                with open(meta_path) as f:
                    meta = json.load(f)
            except (ValueError, OSError):
                meta = {}
        return {
            "input_hash": input_hash,
            "events_path": out_path,
            "events": events,
            "detector": meta.get("detector"),
            "warning": meta.get("warning"),
            "reused": True,
        }

    result = pipeline.detect_syllables(vocals)
    events = result["events"]
    with open(out_path, "w") as f:
        json.dump(events, f)
    with open(meta_path, "w") as f:
        json.dump({"detector": result.get("detector"),
                   "warning": result.get("warning")}, f)
    return {
        "input_hash": input_hash,
        "events_path": out_path,
        "events": events,
        "detector": result.get("detector"),
        "warning": result.get("warning"),
        "reused": False,
    }


def stage_groove(root: str, source_url: str, params: dict = None,
                 force: bool = False) -> dict:
    """Turn syllable events into a drum score -> drum_score.json (cache-aware).

    Runs the two-layer generator in :mod:`groove`: a **flow layer** placed at
    the exact syllable times and a **backbone** transcribed from the song's own
    ``drums.wav`` stem (which the ``separate`` stage already writes and caches).
    No model is involved, so this now runs on the standard CPU image. The score
    is the source of truth for both MIDI export and sample rendering.
    """
    import groove as groove_mod

    params = params or {}
    input_wav = source_input_path(root, source_url)
    if not os.path.exists(input_wav):
        raise pipeline.IngestError(
            "MISSING_ARTIFACT: input.wav not found; run the 'ingest' stage first."
        )
    input_hash = _sha256_file(input_wav)
    stems = stems_dir(root, input_hash, params)
    instrumental = os.path.join(stems, "instrumental.wav")
    drums = os.path.join(stems, "drums.wav")
    events_json = events_path(root, input_hash, params)
    for p, stage in ((instrumental, "separate"), (events_json, "detect")):
        if not os.path.exists(p):
            raise pipeline.IngestError(
                f"MISSING_ARTIFACT: {os.path.basename(p)} not found; "
                f"run the '{stage}' stage first."
            )

    inputs_hash = groove_inputs_hash(root, input_hash, params)
    out_dir = groove_dir(root, input_hash, params, inputs_hash)
    os.makedirs(out_dir, exist_ok=True)
    score_path = os.path.join(out_dir, "drum_score.json")

    if not force and os.path.exists(score_path):
        with open(score_path) as f:
            cached = json.load(f)
        return {
            "input_hash": input_hash,
            "drum_score_path": score_path,
            "model_used": cached.get("model_used", False),
            "warning": cached.get("warning"),
            "stats": cached.get("stats"),
            "reused": True,
        }

    with open(events_json) as f:
        events = json.load(f)

    groove_params = _filter_params(params, GROOVE_PARAM_KEYS)
    score = groove_mod.generate_drum_score(
        events, instrumental,
        drums_wav=drums if os.path.exists(drums) else None,
        params=groove_params,
    )
    with open(score_path, "w") as f:
        json.dump(score, f)
    return {
        "input_hash": input_hash,
        "drum_score_path": score_path,
        "model_used": score.get("model_used"),
        "warning": score.get("warning"),
        "stats": score.get("stats"),
        "reused": False,
    }


def stage_render(root: str, source_url: str, params: dict = None,
                 force: bool = False) -> dict:
    """Render the percussion mix from the drum score + stems (cache-aware).

    The render cache key incorporates the groove and render parameters, so
    reprocessing a job with different settings produces a fresh key and never
    returns a stale mix, while the upstream stems/events/score are reused.
    """
    params = params or {}
    input_wav = source_input_path(root, source_url)
    if not os.path.exists(input_wav):
        raise pipeline.IngestError(
            "MISSING_ARTIFACT: input.wav not found; run the 'ingest' stage first."
        )
    input_hash = _sha256_file(input_wav)
    stems = stems_dir(root, input_hash, params)
    instrumental = os.path.join(stems, "instrumental.wav")
    events_json = events_path(root, input_hash, params)
    inputs_hash = groove_inputs_hash(root, input_hash, params)
    score_path = os.path.join(
        groove_dir(root, input_hash, params, inputs_hash), "drum_score.json")
    for p, stage in ((instrumental, "separate"), (score_path, "groove")):
        if not os.path.exists(p):
            raise pipeline.IngestError(
                f"MISSING_ARTIFACT: {os.path.basename(p)} not found; "
                f"run the '{stage}' stage first."
            )

    with open(score_path) as f:
        drum_score = json.load(f)

    out_dir = render_dir(root, input_hash, params, inputs_hash)
    os.makedirs(out_dir, exist_ok=True)
    mix_wav = os.path.join(out_dir, "mix.wav")
    midi_path = mix_wav.replace(".wav", ".mid")
    perc_path = mix_wav.replace(".wav", "_perc_only.wav")
    inst_path = mix_wav.replace(".wav", "_inst_only.wav")

    if not force and all(
        os.path.exists(p) for p in (mix_wav, perc_path, inst_path)
    ):
        return {
            "input_hash": input_hash,
            "mix_wav": mix_wav,
            "midi_path": midi_path,
            "perc_wav": perc_path,
            "inst_wav": inst_path,
            "events_path": events_json,
            "reused": True,
        }

    render_kwargs = _filter_params(params, RENDER_PARAM_KEYS)
    kit_dir = render_kwargs.pop("kit", None)
    mix_out, midi_out, perc_out, inst_out = pipeline.sample_render(
        drum_score,
        instrumental,
        mix_wav,
        kit_dir=kit_dir,
        **render_kwargs,
    )
    return {
        "input_hash": input_hash,
        "mix_wav": mix_out,
        "midi_path": midi_out,
        "perc_wav": perc_out,
        "inst_wav": inst_out,
        "events_path": events_json,
        "reused": False,
    }


def resolve_render_outputs(root: str, source_url: str, params: dict = None) -> dict:
    """Locate the render + events artifacts for a source without recomputing.

    Used by the finalize step to know which files to upload.
    """
    params = params or {}
    input_wav = source_input_path(root, source_url)
    input_hash = _sha256_file(input_wav)
    inputs_hash = groove_inputs_hash(root, input_hash, params)
    out_dir = render_dir(root, input_hash, params, inputs_hash)
    mix_wav = os.path.join(out_dir, "mix.wav")
    return {
        "input_hash": input_hash,
        "mix_wav": mix_wav,
        "midi_path": mix_wav.replace(".wav", ".mid"),
        "perc_wav": mix_wav.replace(".wav", "_perc_only.wav"),
        "inst_wav": mix_wav.replace(".wav", "_inst_only.wav"),
        "events_path": events_path(root, input_hash, params),
    }


# ---------------------------------------------------------------------------
# Local (non-Modal) orchestration, used by the CLI.
# ---------------------------------------------------------------------------

# Stages that actually produce artifacts locally. ``finalize`` (blob upload) is
# a deploy-only concern handled by ``worker.py``.
_COMPUTE_STAGES = ["ingest", "separate", "detect", "groove", "render"]

_STAGE_FUNCS = {
    "ingest": stage_ingest,
    "separate": stage_separate,
    "detect": stage_detect,
    "groove": stage_groove,
    "render": stage_render,
}


def run_local(root: str, source_url: str, params: dict = None,
              from_stage: str = None, yt_cookies: str = None,
              yt_proxy: str = None, on_progress=None) -> dict:
    """Run the compute stages in-process (no Modal), honouring the cache.

    Caching always applies: a stage whose output already exists is reused. When
    ``from_stage`` is given, that stage and everything after it are *forced* to
    recompute (invalidating their cache) - this is how an individual stage is
    reprocessed while its upstream artifacts (e.g. the download) are reused.
    ``from_stage=None`` (the default) forces nothing, so a normal run reuses
    every artifact it can. Returns the ``render`` stage result dict.
    """
    params = params or {}
    start = STAGE_INDEX[from_stage] if from_stage else len(STAGES)
    results = {}
    for i, stage in enumerate(_COMPUTE_STAGES):
        force = i >= start
        if on_progress:
            on_progress(stage, STAGE_LABELS[stage], "RUNNING", None)
        if stage == "ingest":
            res = stage_ingest(
                root, source_url, params, force=force,
                yt_cookies=yt_cookies, yt_proxy=yt_proxy,
            )
        else:
            res = _STAGE_FUNCS[stage](root, source_url, params, force=force)
        results[stage] = res
        if on_progress:
            on_progress(
                stage, STAGE_LABELS[stage],
                "REUSED" if res.get("reused") else "COMPLETED",
                res.get("reused"),
            )
    return results.get("render", {})
