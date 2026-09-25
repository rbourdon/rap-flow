#!/usr/bin/env python3
"""A stand-in for the Modal worker, for local development and e2e tests.

It speaks the real worker's HTTP contract (see backend/worker.py) without GPUs,
downloads, or Modal:

* ``POST /`` accepts the same body as ``web_trigger`` and answers the same way.
* It then walks the stages from ``backend/workflow.py``, posting HMAC-signed
  progress callbacks to ``callbackUrl`` exactly as ``_post_callback`` does
  (``json.dumps`` body, hex SHA-256 HMAC keyed by ``hmacSig``, sent as
  ``x-signature``), so the frontend's real callback route is exercised.
* Finalize reports result URLs that point back at this server's
  ``GET /assets/<name>``, which serves small generated WAV/JSON/MIDI files, so
  the job page, player and download proxy all work end to end.

Steering a run (either works; the URL form lets a single running mock serve
different scenarios, which is what the e2e tests do):

* add ``mock-fail=<stage>`` (and optionally ``mock-error=<PREFIX>``) to the
  source URL's query string, e.g.
  ``https://youtu.be/x?mock-fail=detect&mock-error=VIDEO_UNAVAILABLE``;
* or set ``MOCK_FAIL_STAGE`` / ``MOCK_ERROR`` in this process's environment.

``MOCK_STAGE_DELAY`` (seconds, default 0.3) paces the stages so progress UI
can be observed. Stdlib only: runs on any python3 without the backend venv.

    python3 scripts/mock_worker.py --port 8765
"""

import argparse
import ast
import hashlib
import hmac
import json
import math
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_PY = os.path.join(ROOT, "backend", "workflow.py")
ASSET_DIR = os.path.join(ROOT, ".dev", "mock-assets")


def _workflow_constants():
    """STAGES / STAGE_LABELS read from backend/workflow.py without importing it
    (importing pulls in numpy/librosa; this keeps the mock dependency-free
    while staying in lockstep with the real stage list)."""
    with open(WORKFLOW_PY, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in ("STAGES", "STAGE_LABELS"):
                found[name] = ast.literal_eval(node.value)
    return found["STAGES"], found["STAGE_LABELS"]


STAGES, STAGE_LABELS = _workflow_constants()
STAGE_INDEX = {s: i for i, s in enumerate(STAGES)}


# ---------------------------------------------------------------------------
# Generated result assets
# ---------------------------------------------------------------------------

def _write_wav(path, freqs, seconds=6.0, sr=22050):
    n = int(seconds * sr)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        # A click every half second so the waveform has visible structure.
        env = math.exp(-((t % 0.5) * 18.0))
        v = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
        s = int(max(-1.0, min(1.0, 0.6 * env * v)) * 32767)
        frames += struct.pack("<hh", s, s)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))


def _write_midi(path):
    # One-track SMF: a GM kick on each of 8 beats at 96 ticks/beat.
    events = b"".join(
        b"\x00\x99\x24\x64" + b"\x60\x89\x24\x00" for _ in range(8)
    ) + b"\x00\xff\x2f\x00"
    track = b"MTrk" + struct.pack(">I", len(events)) + events
    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, 96) + track)


def ensure_assets():
    os.makedirs(ASSET_DIR, exist_ok=True)
    specs = {
        "mix.wav": lambda p: _write_wav(p, [110.0, 220.0, 330.0]),
        "perc.wav": lambda p: _write_wav(p, [180.0]),
        "inst.wav": lambda p: _write_wav(p, [110.0, 165.0]),
        "beat.mid": _write_midi,
        "events.json": lambda p: json.dump(
            [{"t": round(0.5 + 0.25 * i, 3), "strength": 0.6, "f0": 140.0,
              "periodicity": 0.8, "dur": 0.15, "kind": "nucleus",
              "subtype": "voiced", "stress": 0.5} for i in range(20)],
            open(p, "w")),
    }
    for name, make in specs.items():
        path = os.path.join(ASSET_DIR, name)
        if not os.path.exists(path):
            make(path)


CONTENT_TYPES = {
    ".wav": "audio/wav",
    ".mid": "audio/midi",
    ".json": "application/json",
}


# ---------------------------------------------------------------------------
# Fake workflow
# ---------------------------------------------------------------------------

def post_callback(payload, callback_url, secret):
    body = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        callback_url, data=body, method="POST",
        headers={"Content-Type": "application/json", "x-signature": sig},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            status = res.status
    except urllib.error.HTTPError as e:
        status = e.code
    except OSError as e:
        print(f"[mock-worker] callback failed ({payload.get('stageKey')}): {e}", flush=True)
        return
    if status != 200:
        print(f"[mock-worker] callback {payload.get('stageKey')} -> HTTP {status}", flush=True)


def run_workflow(data, base_url):
    job_id = data["jobId"]
    cb, secret = data["callbackUrl"], data["hmacSig"]
    source_url = data["sourceUrl"]
    from_stage = data.get("fromStage")
    delay = float(os.environ.get("MOCK_STAGE_DELAY", "0.3"))

    query = urllib.parse.parse_qs(urllib.parse.urlparse(source_url).query)
    fail_stage = (query.get("mock-fail") or [os.environ.get("MOCK_FAIL_STAGE")])[0]
    error_prefix = (query.get("mock-error") or [os.environ.get("MOCK_ERROR", "INGEST_FAILED")])[0]

    start = STAGE_INDEX[from_stage] if from_stage else len(STAGES)
    print(f"[mock-worker] job {job_id}: from_stage={from_stage} fail={fail_stage}", flush=True)

    def progress(stage, state, **extra):
        post_callback({
            "jobId": job_id, "status": "PROCESSING",
            "stage": STAGE_LABELS[stage], "stageKey": stage,
            "stageState": state, **extra,
        }, cb, secret)

    for i, stage in enumerate(STAGES):
        if stage == fail_stage:
            progress(stage, "RUNNING")
            time.sleep(delay)
            post_callback({
                "jobId": job_id, "status": "FAILED", "stageKey": stage,
                "error": f"{error_prefix}: mock worker failed the {stage} stage",
            }, cb, secret)
            return
        if stage == "finalize":
            break
        # Stages before fromStage are cache hits in the real worker; a fresh
        # job (no fromStage) recomputes nothing it already has - mimic a cold
        # run for new jobs and cache reuse for reprocesses.
        reused = from_stage is not None and i < start
        progress(stage, "RUNNING")
        time.sleep(0 if reused else delay)
        if stage == "ingest":
            post_callback({
                "jobId": job_id, "status": "PROCESSING",
                "title": "Mock worker track", "durationSec": 6,
                "uploader": "mock-worker",
            }, cb, secret)
        progress(stage, "REUSED" if reused else "COMPLETED", reused=reused)

    progress("finalize", "RUNNING")
    time.sleep(delay)
    asset = lambda name: f"{base_url}/assets/{name}"  # noqa: E731
    post_callback({
        "jobId": job_id, "status": "COMPLETED", "stage": "COMPLETED",
        "stageKey": "finalize", "stageState": "COMPLETED",
        "resultUrl": asset("mix.wav"), "eventsUrl": asset("events.json"),
        "percUrl": asset("perc.wav"), "instUrl": asset("inst.wav"),
        "midUrl": asset("beat.mid"),
        # No Opus copies: the player falls back to the WAVs, as for old jobs.
        "mixOpusUrl": None, "percOpusUrl": None, "instOpusUrl": None,
    }, cb, secret)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "rap-flow-mock-worker"

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/healthz":
            return self._json({"ok": True, "stages": STAGES})
        if path.startswith("/assets/"):
            name = os.path.basename(path)
            full = os.path.join(ASSET_DIR, name)
            if not os.path.isfile(full):
                return self._json({"error": "not found"}, 404)
            with open(full, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPES.get(os.path.splitext(name)[1], "application/octet-stream"))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "invalid JSON"}, 400)
        # Mirror web_trigger's validation and (HTTP-200) error shape.
        if not all(data.get(k) for k in ("jobId", "sourceUrl", "callbackUrl", "hmacSig")):
            return self._json({"error": "Missing parameters", "status": 400})
        from_stage = data.get("fromStage")
        if from_stage is not None and from_stage not in STAGE_INDEX:
            return self._json({"error": f"Invalid fromStage '{from_stage}'", "status": 400})
        threading.Thread(
            target=run_workflow, args=(data, self.server.base_url), daemon=True
        ).start()
        self._json({"status": "started", "jobId": data["jobId"]})

    def log_message(self, fmt, *args):
        if os.environ.get("MOCK_WORKER_VERBOSE"):
            sys.stderr.write("[mock-worker] " + fmt % args + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOCK_WORKER_PORT", "8765")))
    args = parser.parse_args()

    ensure_assets()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.base_url = f"http://{args.host}:{args.port}"
    print(f"[mock-worker] listening on {server.base_url} (stages: {', '.join(STAGES)})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
