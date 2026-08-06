import modal
import os
import json
import hmac
import hashlib
from typing import Dict, Any

# Modal configuration
app = modal.App("rap-flow-worker")

image = modal.Image.debian_slim(python_version="3.12") \
    .apt_install("ffmpeg") \
    .pip_install(
        "yt-dlp", "ffmpeg-python", "demucs", "librosa",
        "torchcrepe", "numpy", "soundfile", "mido", "pyloudnorm", "requests"
    )

volume = modal.Volume.from_name("demucs-models", create_if_missing=True)

@app.function(
    image=image,
    volumes={"/root/.cache/torch/hub/checkpoints": volume},
    gpu="T4",
    timeout=600,
    secrets=[modal.Secret.from_name("rap-flow-secrets", require_match=False)],
    mounts=[modal.Mount.from_local_file("pipeline.py", remote_path="/root/pipeline.py")]
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

    try:
        input_wav = os.path.join(outdir, "input.wav")
        pipeline.ingest_audio(input_url, input_wav)

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

            # Upload Mix
            with open(mix_out, "rb") as f:
                res = requests.put(
                    f"https://blob.vercel-storage.com/mix_{job_id}.wav",
                    data=f,
                    headers={"authorization": f"Bearer {token}"}
                )
                if res.ok:
                    mix_blob_url = res.json().get("url", "")

            # Upload Events
            with open(events_path, "rb") as f:
                res = requests.put(
                    f"https://blob.vercel-storage.com/events_{job_id}.json",
                    data=f,
                    headers={"authorization": f"Bearer {token}"}
                )
                if res.ok:
                    events_blob_url = res.json().get("url", "")

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
        print(f"Error in job {job_id}: {e}")
        payload = {
            "jobId": job_id,
            "status": "FAILED",
            "error": str(e)
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
@modal.web_endpoint(method="POST")
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
