# rap-flow backend

Audio ingestion + processing pipeline. Audio is downloaded with
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp), separated into stems with Demucs,
analysed for syllable onsets, and rendered into a percussion mix. The heavy
lifting runs on [Modal](https://modal.com) (`worker.py`); `cli.py` runs the same
pipeline locally.

## Staged durable workflow

The pipeline is a short, linear DAG that is split into independent, cache-aware
stages coordinated by a lightweight Modal-native orchestrator:

```
ingest -> separate -> detect -> groove -> render -> finalize
```

- **Per-stage workers.** Each stage is its own Modal function (`worker.py`) with
  a resource profile suited to its work: `ingest`, `groove` and `render` run on
  cheap CPU containers, while `separate` (Demucs) and `detect` (torchcrepe) get a
  GPU. The `groove` stage additionally runs in its **own image** with Magenta
  baked in (see below), isolated from the demucs/torch worker image. Every stage
  has its own timeout and is retried independently.
- **Durable artifacts + download reuse.** Every stage writes its outputs to a
  persistent Modal Volume (`rap-flow-artifacts`, mounted at `/artifacts`) under a
  key derived from the *content of its inputs* (see `workflow.py`). The ingest
  key is the normalized source id (e.g. the bare YouTube video id), so a second
  job on the same source — or any retry — **reuses the existing download instead
  of re-downloading**. Stems are keyed by the input audio hash and the render by
  the render parameters, so nothing stale is ever served.
- **Retry / reprocess individual stages.** The orchestrator accepts a
  `fromStage` argument: stages before it are near-free cache hits, while that
  stage and everything after it are forced to recompute. This lets you, for
  example, re-render with different ducking/quantization settings while reusing
  the already-separated stems. Changing render parameters yields a fresh render
  cache key, so a reprocess never returns a stale mix.
- **Thin frontend.** The Next.js app only *triggers* runs (optionally at a
  specific stage / with parameter overrides) via the `web_trigger` endpoint and
  receives signed progress callbacks — it never owns the workflow state machine.

The pure stage/caching logic lives in `workflow.py` (no Modal-specific code), so
it is reused unchanged by both `worker.py` (each stage wrapped in a Modal
function) and `cli.py` (run locally, in-process).

## Percussion synthesis

Percussion is generated in **two stages** — a *decision* layer that decides which
drum plays when, and a *sound* layer that plays real samples — so the vocal
onsets stay the rhythmic source of truth and the drums are actual recorded (or
high-quality synthesized) one-shots rather than per-syllable beeps.

### 1. `groove` — syllable events → drum score (GrooVAE tap2drum)

- **GrooVAE tap2drum.** The vocal onsets are turned into a monophonic *tap*
  sequence at their **raw onset times** (velocity from onset strength — no global
  quantization, so the drums stay locked to the voice), then fed in 2-bar windows
  through Magenta's `groovae_2bar_tap_fixed_velocity` model. The model expands the
  taps into a full 9-class drum performance (kick, snare, closed/open hat, three
  toms, crash, ride) with per-note velocity and micro-timing. The instrumental is
  beat-tracked (`librosa.beat.beat_track`), and the resulting **beat times** — not
  just a single global BPM — drive the model path so it tracks tempo changes: each
  2-bar window is cut at *actual* beat boundaries (every 8 beats) and the model's
  fixed 120 BPM output is rescaled into that window's own real duration, then
  soft-snapped to the local (drift-following) 16th grid. Tracks with a degenerate
  beat grid fall back to uniform windows at the global tempo.
- **Isolated environment.** Magenta pins an old TensorFlow that conflicts with the
  demucs/torch worker image, so the model call lives in `groovae.py` and runs in a
  **separate Modal function with its own image** (Magenta + note-seq, checkpoint
  baked in at build time). None of those dependencies touch the main worker image;
  `groove.py` imports `groovae` lazily.
- **Heuristic fallback.** If the model is disabled (`GROOVE_ENABLED=0`),
  unavailable (e.g. Magenta not installed locally), or fails for any reason, a
  metrical heuristic maps the onsets to drums instead — kicks on beats 1 & 3,
  snares on 2 & 4, hats on the subdivisions, open hats on the off-beats, and a
  crash at phrase starts — so a job **never fails because of Magenta**. The
  fallback is surfaced as a non-fatal warning in the stage state.
- **Output.** The drum score (`{t, midi_note, velocity, drum_class}` list plus
  tempo) is saved as `drum_score.json` and is the single source of truth for both
  MIDI export and sample rendering. `GROOVE_TEMPERATURE` (default `0.5`) controls
  the model's sampling temperature.

### 2. `render` — drum score → audio (real-sample sampler)

- **Velocity-layered, round-robin sampler.** `sampler.py` plays a kit of real
  one-shots laid out as `kits/<kit>/<drum_class>/v<layer>_rr<variant>.wav` (see
  `kits/README.md`). The MIDI velocity picks the nearest velocity layer and is
  fine-scaled with gain; a single-layer class additionally darkens soft hits with
  a gentle lowpass. Round-robin variants are cycled and never repeated twice in a
  row; a single-variant class gets a ±3% random varispeed instead, so no two
  consecutive hits are bit-identical. Samples play at **native pitch** — there is
  no f0-tuning or pitch-shifting anywhere in the drum path.
- **Choke groups.** A `hat_closed` or `kick` event chokes any still-ringing
  `hat_open` with a fast 10 ms tail fade.
- **Bundled kit.** `kits/default/` ships CC0 synthesized placeholder one-shots so
  the pipeline works out of the box (2 velocity layers × 2 round-robins for
  kick/snare/hat_closed, one shot for the rest). Drop a real kit into any
  conforming folder and select it with `KIT_DIR` / `--kit` — no code changes
  needed. Regenerate the placeholders with `python kits/generate_default_kit.py`.
- **Band-limited ducking.** Kick and snare hits (not hats) duck the instrumental.
  The bed is split with a ~400 Hz Linkwitz-Riley crossover and only the low band
  is ducked, so the mix doesn't pump. Each dip has a 5 ms attack ramp and a linear
  release (default 80 ms) to a floor of `0.7` (~ -3 dB). Set `DUCK_BAND_LIMITED=0`
  for full-band ducking with the same gentle envelope.
- **MIDI export.** The `.mid` file is built from the drum score with proper
  `ticks_per_beat` math, so it imports on-grid into a DAW with the full set of
  9-class drum notes and per-note velocities. When the score carries the tracked
  beat times, a **drifting tempo map** (one `set_tempo` per beat interval) is
  written so the DAW's bar grid follows the song's tempo changes; otherwise a
  single tempo meta is used.


## YouTube ingestion & the "HTTP Error 403: Forbidden" problem

YouTube increasingly requires a **GVS proof-of-origin (PO) token** to download
media, and it rate-limits / bot-flags requests coming from datacenter IP ranges
(which is exactly where Modal runs). When a download fails with
`HTTP Error 403: Forbidden`, it is almost always one of these two things.

How the pipeline mitigates it:

- **No hard-coded player client.** We let yt-dlp choose its own default clients.
  Its maintainers keep that list pointed at whatever currently works, and it
  includes a client (`visionos`) whose formats do not require a PO token, so
  there is always a fallback when a token cannot be minted. Pinning
  `player_client` to clients that *require* a token (e.g. `ios`) is what caused
  the previous 403s.
- **No `formats=missing_pot`.** That flag makes yt-dlp keep token-gated formats
  even when it has no token, which then 403 on download. Leaving it off lets
  yt-dlp skip those and pick a token-free format instead.
- **A PO token provider** ([bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider))
  is built into the worker image so web-based clients can still get tokens when
  possible. The pip plugin and the built server are pinned to the same version.
- **Node.js as the JS runtime** so yt-dlp can solve YouTube's `nsig` challenge
  (required to sign `web` download URLs).

> Providing a PO token does **not** guarantee bypassing 403 / bot checks. From a
> flagged datacenter IP the only reliably working options are a residential
> proxy and/or account cookies (see below).

## Configuration (environment variables)

These are read from the environment (in Modal, set them as secrets on the
`rap-flow-secrets` secret):

| Variable | Purpose |
| --- | --- |
| `YT_PROXY` | Route yt-dlp traffic through a proxy. A **residential** proxy is the most reliable fix for 403 / bot detection from datacenter IPs. |
| `YT_COOKIES` | Contents of a Netscape-format `cookies.txt` exported from a logged-in YouTube session. Helps with age-gated / bot-gated videos. |
| `YT_PLAYER_CLIENT` | Comma-separated override for the yt-dlp player clients (e.g. `tv,web_safari`). Leave unset to use yt-dlp's maintained defaults. |
| `MAX_SOURCE_DURATION_SEC` | Maximum accepted source duration in seconds (default `900`). |
| `BLOB_READ_WRITE_TOKEN` | Vercel Blob token used to upload results. |
| `GROOVE_ENABLED` | Run the GrooVAE tap2drum model for the drum score (default on); set `0`/`false` to force the heuristic drum mapping. |
| `GROOVE_TEMPERATURE` | GrooVAE sampling temperature (default `0.5`). |
| `KIT_DIR` | Path to the drum kit directory used by the sampler (default: the bundled `kits/default`). See `kits/README.md`. |
| `DUCK_FLOOR` | Ducking floor gain applied under kick/snare hits (default `0.7`, ~ -3 dB). |
| `DUCK_RELEASE_MS` | Ducking release time in milliseconds (default `80`). |
| `DUCK_BAND_LIMITED` | Duck only the low band via a ~400 Hz crossover when truthy (default on); set `0`/`false` for full-band ducking. |

## Running locally

```bash
pip install -r requirements.txt
python cli.py "https://youtu.be/VIDEO_ID" --outdir output
```

Magenta is **not** in `requirements.txt` (it conflicts with the demucs/torch
stack), so local runs automatically use the heuristic drum mapping. Pass
`--no-groove` to force it explicitly, and `--kit /path/to/kit` to use a different
drum kit:

```bash
python cli.py "https://youtu.be/VIDEO_ID" --outdir output --no-groove --kit kits/default
```

Intermediate artifacts are cached under `<outdir>/artifacts` (override with
`--artifacts`), so re-running the same source reuses the download and stems. To
force recomputation from a given stage while reusing everything before it, pass
`--from-stage` (one of `ingest`, `separate`, `detect`, `groove`, `render`), e.g.
re-render without re-downloading or re-separating:

```bash
python cli.py "https://youtu.be/VIDEO_ID" --outdir output --from-stage render
```
