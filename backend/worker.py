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
    .add_local_python_source("pipeline")

volume = modal.Volume.from_name("demucs-models", create_if_missing=True)


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


@app.function(
    image=image,
    volumes={"/root/.cache/torch/hub/checkpoints": volume},
    gpu="T4",
    timeout=600,
    secrets=[modal.Secret.from_name("rap-flow-secrets")]
)
def process_job(job_id: str, input_url: str, callback_url: str, hmac_secret: str, blob_token: str = None):
    import sys
    sys.path.append("/root")
    import pipeline
    import requests

    print(f"Starting job {job_id} for {input_url}")
    outdir = f"/tmp/{job_id}"
    os.makedirs(outdir, exist_ok=True)

    # Try to get blob token from env if not passed explicitly (set up in modal secrets)
    token = blob_token or os.environ.get("BLOB_READ_WRITE_TOKEN")

    # Get optional cookies
    yt_cookies = os.environ.get("YT_COOKIES")

    # Get optional residential proxy (e.g. Decodo) to route yt-dlp requests through
    yt_proxy = os.environ.get("YT_PROXY")

    try:
        input_wav = os.path.join(outdir, "input.wav")
        pipeline.ingest_audio(input_url, input_wav, yt_cookies=yt_cookies, yt_proxy=yt_proxy)

        vocals_wav, inst_wav = pipeline.separate_audio(input_wav, outdir)

        events = pipeline.detect_syllables(vocals_wav)
        events_path = os.path.join(outdir, "events.json")
        with open(events_path, "w") as f:
            json.dump(events, f)

        mix_wav = os.path.join(outdir, "mix.wav")
        mix_out, midi_out = pipeline.render_percussion(events, inst_wav, mix_wav)

        mix_blob_url = ""
        events_blob_url = ""

        if token:
            print("Uploading results to Vercel Blob...")

            mix_blob_url = _upload_to_blob(
                mix_out, f"mix_{job_id}.wav", token, "audio/wav"
            )
            events_blob_url = _upload_to_blob(
                events_path, f"events_{job_id}.json", token, "application/json"
            )

        else:
            print("Warning: BLOB_READ_WRITE_TOKEN not provided, using dummy URLs.")
            mix_blob_url = "https://dummy.blob.vercel-storage.com/mix.wav"
            events_blob_url = "https://dummy.blob.vercel-storage.com/events.json"

        payload = {
            "jobId": job_id,
            "status": "COMPLETED",
            "resultUrl": mix_blob_url,
            "eventsUrl": events_blob_url,
            "events": events
        }

    except Exception as e:
        err_msg = str(e)
        print(f"Error in job {job_id}: {err_msg}")

        # If it's already classified, use it. Otherwise, assume ingest or general failure if it's from pipeline
        if not (err_msg.startswith("AUTH_REQUIRED") or
                err_msg.startswith("VIDEO_UNAVAILABLE") or
                err_msg.startswith("UNSUPPORTED_SOURCE") or
                err_msg.startswith("INGEST_FAILED") or
                err_msg.startswith("UPLOAD_FAILED")):
            err_msg = f"INGEST_FAILED: {err_msg}"

        payload = {
            "jobId": job_id,
            "status": "FAILED",
            "error": err_msg
        }

    body = json.dumps(payload).encode('utf-8')
    signature = hmac.new(hmac_secret.encode('utf-8'), body, hashlib.sha256).hexdigest()

    headers = {
        'Content-Type': 'application/json',
        'x-signature': signature
    }

    print(f"Calling webhook: {callback_url}")
    resp = requests.post(callback_url, data=body, headers=headers)
    print(f"Webhook response: {resp.status_code}")


@app.function(image=image)
@modal.fastapi_endpoint(method="POST")
def web_trigger(data: Dict[str, Any]):
    job_id = data.get("jobId")
    source_url = data.get("sourceUrl")
    callback_url = data.get("callbackUrl")
    hmac_secret = data.get("hmacSig")
    blob_token = data.get("blobToken")

    if not all([job_id, source_url, callback_url, hmac_secret]):
        return {"error": "Missing parameters", "status": 400}

    process_job.spawn(job_id, source_url, callback_url, hmac_secret, blob_token)
    return {"status": "started", "jobId": job_id}
