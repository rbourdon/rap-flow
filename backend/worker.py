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
        "torchcrepe", "numpy", "soundfile", "mido", "pyloudnorm", "requests"
    ) \
    .add_local_python_source("pipeline", "workflow")

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
    else:
        print("Warning: BLOB_READ_WRITE_TOKEN not provided, using dummy URLs.")
        mix_url = "https://dummy.blob.vercel-storage.com/mix.wav"
        events_url = "https://dummy.blob.vercel-storage.com/events.json"
        perc_url = "https://dummy.blob.vercel-storage.com/perc.wav"
        inst_url = "https://dummy.blob.vercel-storage.com/inst.wav"

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
    }, callback_url, hmac_secret)

    return {"mix_url": mix_url, "reused": False}


# Modal function for each stage, keyed by stage name, in execution order.
_STAGE_FUNCS = {
    "ingest": stage_ingest,
    "separate": stage_separate,
    "detect": stage_detect,
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
