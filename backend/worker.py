import modal
import os
import json
import hmac
import hashlib
from typing import Dict, Any

# Modal configuration
app = modal.App("rap-flow-worker")

# Keep the bgutil PO Token provider plugin (installed from PyPI below) pinned to
# the exact same version as the provider server we build from source. A version
# mismatch between the two halves can make the plugin silently refuse to
# generate PO Tokens, which in turn causes yt-dlp to drop or 403 on every
# downloadable YouTube format.
BGUTIL_VERSION = "1.3.1"

image = modal.Image.debian_slim(python_version="3.12") \
    .apt_install("ffmpeg", "git", "curl") \
    .run_commands(
        # debian_slim ships an ancient Node.js (v12) which can't run modern
        # tooling (npm, tsc) required by bgutil-ytdlp-pot-provider. Install a
        # current Node.js LTS (>=20) from NodeSource instead.
        "curl -fsSL https://deb.nodesource.com/setup_24.x | bash -",
        "apt-get install -y nodejs",
        f"git clone --single-branch --branch {BGUTIL_VERSION} https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil",
        "cd /opt/bgutil/server && npm ci && npx tsc"
    ) \
    .pip_install(
        "yt-dlp", f"bgutil-ytdlp-pot-provider=={BGUTIL_VERSION}", "ffmpeg-python", "demucs", "librosa",
        "torchcrepe", "numpy", "soundfile", "mido", "pyloudnorm", "requests", "scipy"
    ) \
    .env({"KIT_DIR": "/root/kits/default"}) \
    .add_local_dir("kits", "/root/kits") \
    .add_local_python_source("pipeline", "workflow", "rhythm", "sampler")

# Isolated image for the GrooVAE (tap2drum) groove stage. Magenta pins an old
# TensorFlow that conflicts with the demucs/torch worker image above, so the
# groove stage runs in its own image with Magenta/note-seq and the checkpoint
# baked in at build time. None of these dependencies leak into the main worker
# image. The checkpoint is fetched once during the build.
GROOVE_CKPT_URL = (
    "https://storage.googleapis.com/magentadata/models/music_vae/checkpoints/"
    "groovae_2bar_tap_fixed_velocity.tar"
)
groove_image = modal.Image.debian_slim(python_version="3.10") \
    .apt_install(
        # ``python-rtmidi`` (pulled in transitively via magenta/note-seq) links
        # its C++ extension against the ALSA and JACK shared libraries at
        # runtime. We install a prebuilt wheel below (see ``python-rtmidi`` in
        # ``pip_install``) rather than compiling from source, but keep the dev
        # packages so the runtime ``libasound2``/``libjack`` shared objects the
        # wheel dlopens are present.
        "ffmpeg", "curl", "libsndfile1", "libasound2-dev", "libjack-dev",
    ) \
    .pip_install(
        # Magenta 2.1.4 hard-pins ``python-rtmidi==1.1.2``, whose Cython-generated
        # C++ still references ``tp_print`` and ``PyUnicode_GET_SIZE`` - symbols
        # CPython removed in 3.9/3.10 - so it fails to compile on this image's
        # Python 3.10 (Modal's minimum supported version). Modal can't run an
        # older interpreter, so instead we list a Python 3.10-compatible
        # ``python-rtmidi`` (which ships a manylinux wheel, no compilation) FIRST.
        # With the legacy resolver, the first-stated top-level pin wins, so this
        # satisfies magenta's dependency without building the incompatible 1.1.2.
        # rtmidi is only used for realtime hardware MIDI I/O, which the groove
        # (tap2drum) stage never touches, so the newer version is functionally
        # equivalent here.
        "python-rtmidi==1.5.8",
        # Pin Magenta to its final release so the build is deterministic and
        # matches the transitive dependency set this image was validated against.
        "magenta==2.1.4", "note-seq", "librosa", "soundfile", "numpy", "scipy",
        "mido", "pyloudnorm", "requests",
        # Magenta transitively requires ``apache-beam[gcp]>=2.14.0``, whose
        # google-cloud extras form an enormous, loosely-bounded dependency tree.
        # pip's backtracking resolver explores it for over an hour before giving
        # up with ``ResolutionTooDeep: 200000``, which is what stalls and fails
        # the deploy. Magenta predates that resolver and was only ever meant to
        # install with the legacy (first-fit) one, so use it here to avoid the
        # combinatorial backtracking explosion.
        extra_options="--use-deprecated=legacy-resolver",
    ) \
    .run_commands(
        "mkdir -p /models",
        f"curl -fsSL {GROOVE_CKPT_URL} -o /models/groovae_2bar_tap_fixed_velocity.tar",
    ) \
    .env({"GROOVE_CKPT": "/models/groovae_2bar_tap_fixed_velocity.tar"}) \
    .add_local_python_source(
        "pipeline", "workflow", "rhythm", "sampler", "groove", "groovae"
    )

# Demucs model weights, cached across runs.
volume = modal.Volume.from_name("demucs-models", create_if_missing=True)

# Durable artifact store shared by every stage. Each stage writes its outputs
# here under a content-derived key (see workflow.py), so a later stage - or a
# re-run of the workflow - can reuse an existing download / stems / events
# instead of recomputing them. This is the backbone of the durable workflow:
# state survives container restarts and individual stage retries.
artifacts = modal.Volume.from_name("rap-flow-artifacts", create_if_missing=True)

ARTIFACTS_ROOT = "/artifacts"

# Where each stage stores/reads durable artifacts inside the container.
_ARTIFACT_MOUNT = {ARTIFACTS_ROOT: artifacts}
# Stem separation + syllable detection also need the demucs/torch model cache.
_MODEL_MOUNT = {
    ARTIFACTS_ROOT: artifacts,
    "/root/.cache/torch/hub/checkpoints": volume,
}

_SECRETS = [modal.Secret.from_name("rap-flow-secrets")]


def _upload_to_blob(local_path: str, pathname: str, token: str, content_type: str):
    """Upload a file to Vercel Blob and return its URL.

    Mirrors the request shape used by the `@vercel/blob` SDK (the same
    client the frontend depends on): uploads go to `vercel.com/api/blob`
    with the pathname as a query parameter, not as a URL path segment on
    `blob.vercel-storage.com`, and require an `x-api-version` header. Prior
    to this fix, requests were sent to the wrong URL/without this header,
    so the Blob API silently rejected every upload.

    Uploads are made with `private` access because Blob stores configured
    for private access reject `public` uploads (400 "Cannot use public
    access on a private store"). Private blobs aren't fetchable directly
    from a browser; the frontend must proxy reads through a server route
    that attaches the `BLOB_READ_WRITE_TOKEN` as a bearer token.
    """
    import requests

    with open(local_path, "rb") as f:
        res = requests.put(
            "https://vercel.com/api/blob/",
            params={"pathname": pathname},
            data=f,
            headers={
                "authorization": "Bearer " + token,
                "x-api-version": "12",
                "x-vercel-blob-access": "private",
                "x-content-type": content_type,
            }
        )

    if not res.ok:
        raise RuntimeError(
            f"UPLOAD_FAILED: Failed to upload {pathname} to Vercel Blob "
            f"(status {res.status_code}): {res.text[:500]}"
        )

    return res.json().get("url", "")


def _transcode_to_opus(src_wav: str, dst_ogg: str) -> str:
    """Transcode a WAV to Opus-in-Ogg for lightweight streaming playback.

    The WAVs remain the download artifacts; these compressed copies are what the
    player streams so a completed job doesn't pull 100+ MB of WAV. Returns the
    output path on success or None if ffmpeg fails (playback then falls back to
    the WAV).
    """
    import subprocess

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", src_wav, "-c:a", "libopus", "-b:a", "128k", dst_ogg],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return dst_ogg
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"Opus transcode failed for {src_wav}: {e}")
        return None


def _post_callback(payload: dict, callback_url: str, hmac_secret: str, timeout: int = 10):
    """Sign and POST a status/progress payload to the frontend webhook."""
    import requests

    if not (callback_url and hmac_secret):
        return None

    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(hmac_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    headers = {"Content-Type": "application/json", "x-signature": signature}
    try:
        return requests.post(callback_url, data=body, headers=headers, timeout=timeout)
    except Exception as e:  # progress updates are best-effort
        print(f"Failed to POST callback ({payload.get('stageKey')}): {e}")
        return None


import workflow  # noqa: E402  (imported after helpers; part of the image sources)


def _stage_start(job_id, stage, callback_url, hmac_secret):
    _post_callback({
        "jobId": job_id,
        "status": "PROCESSING",
        "stage": workflow.STAGE_LABELS[stage],
        "stageKey": stage,
        "stageState": "RUNNING",
    }, callback_url, hmac_secret, timeout=5)


def _stage_done(job_id, stage, reused, callback_url, hmac_secret):
    _post_callback({
        "jobId": job_id,
        "status": "PROCESSING",
        "stage": workflow.STAGE_LABELS[stage],
        "stageKey": stage,
        "stageState": "REUSED" if reused else "COMPLETED",
        "reused": bool(reused),
    }, callback_url, hmac_secret, timeout=5)


# ---------------------------------------------------------------------------
# Per-stage Modal functions.
#
# Each stage runs in its own container with a resource profile suited to its
# work (only separation/detection need a GPU), has its own timeout, and is
# retried independently. Every stage reloads the artifact volume before reading
# and commits it after writing so downstream stages see its outputs.
# ---------------------------------------------------------------------------

@app.function(image=image, volumes=_ARTIFACT_MOUNT, timeout=600,
              retries=2, secrets=_SECRETS)
def stage_ingest(job_id: str, source_url: str, params: dict, force: bool,
                 callback_url: str, hmac_secret: str, blob_token: str = None) -> dict:
    artifacts.reload()
    _stage_start(job_id, "ingest", callback_url, hmac_secret)
    res = workflow.stage_ingest(
        ARTIFACTS_ROOT, source_url, params, force=force,
        yt_cookies=os.environ.get("YT_COOKIES"),
        yt_proxy=os.environ.get("YT_PROXY"),
    )
    artifacts.commit()
    # Forward captured source metadata (title/thumbnail/duration/uploader) so
    # the frontend can give the job an identity. Best-effort: only sent when we
    # actually captured something (URL sources; uploads carry no metadata here).
    meta = res.get("metadata") or {}
    meta_payload = {
        k: v for k, v in {
            "title": meta.get("title"),
            "thumbnailUrl": meta.get("thumbnail"),
            "durationSec": meta.get("duration"),
            "uploader": meta.get("uploader"),
        }.items() if v is not None
    }
    if meta_payload:
        _post_callback({
            "jobId": job_id,
            "status": "PROCESSING",
            **meta_payload,
        }, callback_url, hmac_secret, timeout=5)
    _stage_done(job_id, "ingest", res.get("reused"), callback_url, hmac_secret)
    return res


@app.function(image=image, volumes=_MODEL_MOUNT, gpu="T4", timeout=900,
              retries=1, secrets=_SECRETS)
def stage_separate(job_id: str, source_url: str, params: dict, force: bool,
                   callback_url: str, hmac_secret: str, blob_token: str = None) -> dict:
    artifacts.reload()
    _stage_start(job_id, "separate", callback_url, hmac_secret)
    res = workflow.stage_separate(ARTIFACTS_ROOT, source_url, params, force=force)
    artifacts.commit()
    _stage_done(job_id, "separate", res.get("reused"), callback_url, hmac_secret)
    return res


@app.function(image=image, volumes=_MODEL_MOUNT, gpu="T4", timeout=600,
              retries=1, secrets=_SECRETS)
def stage_detect(job_id: str, source_url: str, params: dict, force: bool,
                 callback_url: str, hmac_secret: str, blob_token: str = None) -> dict:
    artifacts.reload()
    _stage_start(job_id, "detect", callback_url, hmac_secret)
    res = workflow.stage_detect(ARTIFACTS_ROOT, source_url, params, force=force)
    artifacts.commit()
    _stage_done(job_id, "detect", res.get("reused"), callback_url, hmac_secret)
    # Drop the (potentially large) events list from the return value; downstream
    # stages re-read events.json from the volume.
    res.pop("events", None)
    return res


@app.function(image=groove_image, volumes=_ARTIFACT_MOUNT, timeout=900,
              retries=1, secrets=_SECRETS)
def stage_groove(job_id: str, source_url: str, params: dict, force: bool,
                 callback_url: str, hmac_secret: str, blob_token: str = None) -> dict:
    """GrooVAE (tap2drum) drum-score stage, in its own Magenta image.

    Falls back to a heuristic drum score inside ``workflow.stage_groove`` if
    Magenta is unavailable or fails, so this stage never fails the job; any
    fallback surfaces a warning in the stage state.
    """
    artifacts.reload()
    _stage_start(job_id, "groove", callback_url, hmac_secret)
    res = workflow.stage_groove(ARTIFACTS_ROOT, source_url, params, force=force)
    artifacts.commit()
    _post_callback({
        "jobId": job_id,
        "status": "PROCESSING",
        "stage": workflow.STAGE_LABELS["groove"],
        "stageKey": "groove",
        "stageState": "REUSED" if res.get("reused") else "COMPLETED",
        "reused": bool(res.get("reused")),
        **({"warning": res["warning"]} if res.get("warning") else {}),
    }, callback_url, hmac_secret, timeout=5)
    return res


@app.function(image=image, volumes=_ARTIFACT_MOUNT, timeout=600,
              retries=1, secrets=_SECRETS)
def stage_render(job_id: str, source_url: str, params: dict, force: bool,
                 callback_url: str, hmac_secret: str, blob_token: str = None) -> dict:
    artifacts.reload()
    _stage_start(job_id, "render", callback_url, hmac_secret)
    res = workflow.stage_render(ARTIFACTS_ROOT, source_url, params, force=force)
    artifacts.commit()
    _stage_done(job_id, "render", res.get("reused"), callback_url, hmac_secret)
    return res


@app.function(image=image, volumes=_ARTIFACT_MOUNT, timeout=600,
              retries=2, secrets=_SECRETS)
def stage_finalize(job_id: str, source_url: str, params: dict, force: bool,
                   callback_url: str, hmac_secret: str, blob_token: str = None) -> dict:
    """Upload the render outputs to Vercel Blob and notify the frontend."""
    artifacts.reload()
    _stage_start(job_id, "finalize", callback_url, hmac_secret)

    out = workflow.resolve_render_outputs(ARTIFACTS_ROOT, source_url, params)
    token = blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN")

    if token:
        print("Uploading results to Vercel Blob...")
        mix_url = _upload_to_blob(out["mix_wav"], f"mix_{job_id}.wav", token, "audio/wav")
        events_url = _upload_to_blob(out["events_path"], f"events_{job_id}.json", token, "application/json")
        perc_url = _upload_to_blob(out["perc_wav"], f"perc_{job_id}.wav", token, "audio/wav")
        inst_url = _upload_to_blob(out["inst_wav"], f"inst_{job_id}.wav", token, "audio/wav")

        # The MIDI file was previously generated then discarded; upload it so the
        # user can download it alongside the audio stems.
        mid_url = None
        midi_path = out.get("midi_path")
        if midi_path and os.path.exists(midi_path):
            mid_url = _upload_to_blob(midi_path, f"beat_{job_id}.mid", token, "audio/midi")

        # Compressed Opus playback copies. Transcode into the render dir, then
        # upload. Any failure leaves the URL None and playback falls back to WAV.
        render_dir = os.path.dirname(out["mix_wav"])
        mix_opus_url = perc_opus_url = inst_opus_url = None
        for wav_key, name, setter in (
            ("mix_wav", "mix", "mix"),
            ("perc_wav", "perc", "perc"),
            ("inst_wav", "inst", "inst"),
        ):
            ogg = _transcode_to_opus(out[wav_key], os.path.join(render_dir, f"{name}.opus.ogg"))
            if ogg:
                url = _upload_to_blob(ogg, f"{name}_{job_id}.opus.ogg", token, "audio/ogg")
                if setter == "mix":
                    mix_opus_url = url
                elif setter == "perc":
                    perc_opus_url = url
                else:
                    inst_opus_url = url
    else:
        print("Warning: BLOB_READ_WRITE_TOKEN not provided, using dummy URLs.")
        mix_url = "https://dummy.blob.vercel-storage.com/mix.wav"
        events_url = "https://dummy.blob.vercel-storage.com/events.json"
        perc_url = "https://dummy.blob.vercel-storage.com/perc.wav"
        inst_url = "https://dummy.blob.vercel-storage.com/inst.wav"
        mid_url = None
        mix_opus_url = perc_opus_url = inst_opus_url = None

    _post_callback({
        "jobId": job_id,
        "status": "COMPLETED",
        "stage": "COMPLETED",
        "stageKey": "finalize",
        "stageState": "COMPLETED",
        "resultUrl": mix_url,
        "eventsUrl": events_url,
        "percUrl": perc_url,
        "instUrl": inst_url,
        "midUrl": mid_url,
        "mixOpusUrl": mix_opus_url,
        "percOpusUrl": perc_opus_url,
        "instOpusUrl": inst_opus_url,
    }, callback_url, hmac_secret)

    return {"mix_url": mix_url, "reused": False}


# Modal function for each stage, keyed by stage name, in execution order.
_STAGE_FUNCS = {
    "ingest": stage_ingest,
    "separate": stage_separate,
    "detect": stage_detect,
    "groove": stage_groove,
    "render": stage_render,
    "finalize": stage_finalize,
}


def _classify_error(err_msg: str) -> str:
    if not (err_msg.startswith("AUTH_REQUIRED") or
            err_msg.startswith("VIDEO_UNAVAILABLE") or
            err_msg.startswith("UNSUPPORTED_SOURCE") or
            err_msg.startswith("INGEST_FAILED") or
            err_msg.startswith("MISSING_ARTIFACT") or
            err_msg.startswith("UPLOAD_FAILED")):
        err_msg = f"INGEST_FAILED: {err_msg}"
    return err_msg


@app.function(image=image, timeout=3600, secrets=_SECRETS)
def run_workflow(job_id: str, source_url: str, callback_url: str, hmac_secret: str,
                 blob_token: str = None, from_stage: str = None, params: dict = None):
    """Thin orchestrator: chain the per-stage functions in order.

    Caching in each stage means stages before ``from_stage`` are near-free
    (they just verify their cached artifact exists), while ``from_stage`` and
    everything after it are forced to recompute. ``from_stage=None`` runs a
    normal job that reuses whatever artifacts already exist (e.g. a previously
    downloaded source).
    """
    params = params or {}
    start = workflow.STAGE_INDEX[from_stage] if from_stage else len(workflow.STAGES)

    print(f"Starting workflow for job {job_id} ({source_url}), from_stage={from_stage}")

    current_stage = "ingest"
    try:
        for i, stage in enumerate(workflow.STAGES):
            current_stage = stage
            force = i >= start
            fn = _STAGE_FUNCS[stage]
            fn.remote(job_id, source_url, params, force,
                      callback_url, hmac_secret, blob_token)
    except Exception as e:
        err_msg = _classify_error(str(e))
        print(f"Error in job {job_id} at stage {current_stage}: {err_msg}")
        _post_callback({
            "jobId": job_id,
            "status": "FAILED",
            "stageKey": current_stage,
            "error": err_msg,
        }, callback_url, hmac_secret)


@app.function(image=image)
@modal.fastapi_endpoint(method="POST")
def web_trigger(data: Dict[str, Any]):
    job_id = data.get("jobId")
    source_url = data.get("sourceUrl")
    callback_url = data.get("callbackUrl")
    hmac_secret = data.get("hmacSig")
    blob_token = data.get("blobToken")
    # Optional: start (force) at a specific stage and/or override render params.
    # This is how the frontend reprocesses an individual stage while reusing the
    # existing download/stems.
    from_stage = data.get("fromStage")
    params = data.get("params") or {}

    if not all([job_id, source_url, callback_url, hmac_secret]):
        return {"error": "Missing parameters", "status": 400}

    if from_stage is not None and from_stage not in workflow.STAGE_INDEX:
        return {"error": f"Invalid fromStage '{from_stage}'", "status": 400}

    run_workflow.spawn(job_id, source_url, callback_url, hmac_secret,
                       blob_token, from_stage, params)
    return {"status": "started", "jobId": job_id}
