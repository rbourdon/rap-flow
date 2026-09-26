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
  GPU. There is a **single image** — the second, Magenta/TensorFlow one that used
  to exist purely for the `groove` stage went away with GrooVAE. Every stage has
  its own timeout and is retried independently.
- **Durable artifacts + download reuse.** Every stage writes its outputs to a
  persistent Modal Volume (`rap-flow-artifacts`, mounted at `/artifacts`) under a
  key derived from the *content of its inputs* (see `workflow.py`). The ingest
  key is the normalized source id (e.g. the bare YouTube video id), so a second
  job on the same source — or any retry — **reuses the existing download instead
  of re-downloading**. Stems are keyed by the input audio hash, the event list by
  the detector parameters, the drum score by the drum-stem and event hashes, and
  the render by the render parameters, so nothing stale is ever served. A
  `PIPELINE_VERSION` constant feeds the detect and groove keys, so a change to
  either artifact *format* invalidates them rather than handing an old-format
  file to new code.
- **Retry / reprocess individual stages.** The orchestrator accepts a
  `fromStage` argument: stages before it are near-free cache hits, while that
  stage and everything after it are forced to recompute. This lets you, for
  example, re-render at a different flow/backbone balance while reusing the
  already-separated stems, the syllables and the drum score — which is exactly
  what the result page's balance slider does. Changing render parameters yields a
  fresh render cache key, so a reprocess never returns a stale mix.
- **Thin frontend.** The Next.js app only *triggers* runs (optionally at a
  specific stage / with parameter overrides) via the `web_trigger` endpoint and
  receives signed progress callbacks — it never owns the workflow state machine.

The pure stage/caching logic lives in `workflow.py` (no Modal-specific code), so
it is reused unchanged by both `worker.py` (each stage wrapped in a Modal
function) and `cli.py` (run locally, in-process).

## Percussion

**Every hit traces either to a syllable or to the record's own beat.** Nothing is
sampled from a model, so nothing drifts. This is the load-bearing rule of the
whole percussion path, and one invariant sits above all the others:

> No code path may move a flow-layer hit off its source syllable's time.

`backend/tests/test_flow.py` asserts it with float **equality**, not a tolerance.

### 1. `detect` — vocal stem → syllable events

Two detectors run side by side on the Demucs vocal stem (`pipeline.py`):

- **Voicing (`torchcrepe`).** Periodicity comes from the `tiny` model decoded
  with **`weighted_argmax`**, not torchcrepe's default Viterbi. On separated rap
  vocals the model's activations are flat enough that Viterbi parks every frame
  on the top pitch bin and reads periodicity ~0. When that happened, the vocal
  looked unvoiced, the nucleus detector found almost nothing, and the flow layer
  was a stream of consonant hats. If fewer than 15% of the clearly audible
  vocal frames read as voiced, detection now surfaces a non-fatal warning
  instead of failing silently.
- **Vowel nuclei (voiced syllables).** The ~300–3400 Hz band energy is converted
  to dB and smoothed over ~50 ms; frames below `SYL_VOICED_THRESHOLD`
  periodicity are floored so breaths and separation hiss cannot form peaks.
  Peaks in what is left are the syllable nuclei — one per syllable. Peak
  prominence is gated at a fixed `SYL_PROMINENCE_DB` (3 dB). The envelope is
  already in dB, so that is level-independent by itself. The previous gate, a
  fraction of the whole track's P90−P10 range, grew with how much of the track
  is silence, sat above the 3–8 dB dips between connected syllables, and
  dropped a third or more of them. Prominence is measured on the *unmasked*
  envelope, so a peak next to a voicing boundary can't inherit that cliff's
  prominence. Because prominence is relative, a nucleus also has to be within
  `SYL_LEVEL_FLOOR_DB` (30 dB) of the envelope's P95. Without that floor,
  instrument bleed in a "silent" intro or outro of the stem (read as voiced by
  the pitch tracker) produced syllables where nobody was rapping.
- **`t` is the attack, not the nucleus.** Drums have to hit where the syllable
  *starts*; using the loudness peak would place every hit late by roughly half a
  vowel. The attack is the steepest rise in the 80 ms before the peak, found on a
  high-time-resolution bandpass+RMS envelope (the 50 ms smoothing that makes
  peak-picking robust would smear the onset ~30 ms early).
- **Transients (unvoiced consonants).** High-band (≥ 4 kHz) flux peaks on
  unvoiced frames, sub-classified by HF decay length into `sibilant` or
  `plosive`, and suppressed within `TRANSIENT_SUPPRESS_MS` (150 ms) *before* a
  nucleus attack. That is the syllable's own onset consonant, already
  represented by the nucleus, and a hat on it plays as an early flam. Onsets
  like "st" run 80–200 ms, so the old 45 ms window let most of them through.
  In fast rap a word-final consonant also sits 50–160 ms ahead of the next
  syllable, so this is a trade-off: on the JamendoLyrics hip-hop tracks, 150 ms
  drops ~70–75% of word-initial consonant hats and keeps ~30–50% of word-final
  ones.
- **Strength and stress.** `strength` is the prominence over the track's **P95**
  (not its max — one outlier peak used to compress every other strength toward
  zero); `stress` is prominence relative to a 2 s moving window, so a quiet
  passage still gets its own accents.
- **Fallback.** The previous spectral-flux detector is kept as `_flux_events`.
  If the nucleus detector returns nothing, or under 0.8 events per voiced second,
  it takes over and a non-fatal warning is surfaced. `SYL_DETECTOR=flux` forces
  it explicitly.

Events keep their original keys and add `kind` (`nucleus` | `transient`),
`subtype` (`voiced` | `sibilant` | `plosive`) and `stress`.

### 2. `groove` — events + drums stem → drum score (two layers)

- **Flow layer** (`flow.py`) — one event, one hit, at the event's `t` verbatim.
  No quantization, no grid snapping, no per-bar caps: the syllables already
  encode the density. Every voiced syllable is a closed hat
  (`FLOW_SYLLABLE_CLASS`, velocity 25–50), and a stressed one (top 15% of a 2 s
  window) a louder one (`FLOW_ACCENT_CLASS`, 65–90). Velocities follow the
  syllable's track-level `strength`, not its local `stress`, which put every
  accent at ~100. Sibilants and plosives get quieter closed hats, the sibilant
  closing a phrase gets an open hat, and a phrase start with high stress adds a
  `crash` alongside its own hit. The only culling is a per-class
  `FLOW_MIN_GAP_MS` gap, keeping the louder hit, so samples can't stack on
  themselves. Every note carries `event_index`, a back-reference into the detect
  stage's array.
  - The earlier defaults put a `snare` ghost on every syllable and a `ride` on
    accents. Production never played them until syllable detection was fixed
    (#66), and then they sounded worse than the accidental all-hats layer they
    replaced. The kit's velocity layers are all peak-normalized, so a "ghost" is
    within ~2–3 dB of a backbeat snare. About 2.5 of them a second plus a ride on
    every accent put 76% of the flow layer's energy in 150 Hz–1 kHz, the band of
    the backbone snare and the instrumental's body. `FLOW_SYLLABLE_CLASS=snare`
    and `FLOW_ACCENT_CLASS=ride` restore that mapping for comparison.
- **Groove bed** (`backbone.py`) — the kick and snare are **transcribed from the
  song's own `drums.wav`**, a stem `separate_audio` has always written and
  `workflow.py` has always cached but nothing consumed. Band envelopes (low
  30–120 Hz, body+noise 150–450 Hz plus 1–6 kHz, high ≥ 6 kHz) are onset-detected
  and classified by band-energy ratio; a kick and a snare on the same instant are
  both kept. A grid sanity pass then **soft-snaps** onto the nearest 16th only
  within `BACKBONE_SNAP_MS` (so the record's own push and pull survives but
  detector jitter doesn't), fills a missing backbeat snare / beat-1 kick
  (`BACKBONE_FILL`, tagged `source: "filled"`), and clamps obvious band bleed.
  Too-sparse transcription or a degenerate beat grid falls back to a grid
  template (kick on 1 and the "and" of 3, snare on 2 and 4) with a warning.
- **Layer split.** The bed owns kick and snare; the flow owns hats, ghosts and
  accents. The old kick/snare-per-bar caps that demoted overflow to hats are gone
  — the flow layer has classes of its own now.
- **Merge** (`groove.py`) — a flow ghost `snare` within `MERGE_SNARE_GUARD_MS` of
  a bed `snare` is dropped (the backbone wins, because it is the beat), choke
  groups stay in the sampler and now operate across both layers, and the result
  is sorted by time. The layer balance is deliberately **not** applied here: it
  is a render bus gain, which is what makes the UI's fast re-render possible.
- **Output.** `drum_score.json` carries `tempo`, `beat_times`,
  `downbeat_offset`, the note list (each with `layer`, `source` and, for flow
  notes, `event_index`), `model_used: false`, `warning` and `stats`.

### 3. `render` — drum score → audio (real-sample sampler + bus chain)

- **Velocity-layered, round-robin sampler.** `sampler.py` plays a kit of real
  one-shots laid out as `kits/<kit>/<drum_class>/v<layer>_rr<variant>.wav` (see
  `kits/README.md`). The MIDI velocity picks the nearest velocity layer and is
  fine-scaled with gain; a single-layer class additionally darkens soft hits with
  a gentle lowpass. Round-robin variants are cycled and never repeated twice in a
  row; a single-variant class gets a ±3% random varispeed instead, so no two
  consecutive hits are bit-identical. Samples play at **native pitch** — there is
  no f0-tuning or pitch-shifting anywhere in the drum path.
- **Each one-shot starts early by its own lead-in.** The bundled Salamander
  samples start 0–17 ms before the hit, by a different amount per layer and
  round-robin. Placed from their first frame, every hit landed late and cycling
  the variants jittered it by up to ~13 ms. The sampler measures the lead-in
  (the first frame above −40 dB re peak) and starts the sample that much early,
  so the attack lands on the note time. The samples are deliberately *not*
  trimmed: the per-class transient shaping in `bus.py` was tuned by ear on the
  untrimmed samples, and trimming them (the first version of this fix) moved
  its attack boost and hat decay onto the real attack, up to +5 dB on closed hats.
- **Choke groups.** A `hat_closed` or `kick` event chokes any still-ringing
  `hat_open` with a fast 10 ms tail fade.
- **Bundled kit.** `kits/default/` ships CC0 synthesized placeholder one-shots so
  the pipeline works out of the box (2 velocity layers × 2 round-robins for
  kick/snare/hat_closed, one shot for the rest). Drop a real kit into any
  conforming folder and select it with `KIT_DIR` / `--kit` — no code changes
  needed. Regenerate the placeholders with `python kits/generate_default_kit.py`.
- **Two sub-buses and a bus chain** (`bus.py`). The flow and bed layers render
  into separate sub-buses so the balance can be applied before summing. Each
  one-shot gets **per-class transient shaping** (attack emphasis over the first
  ~8 ms, decay shortening for hats) applied to the buffer, so it stays cheap and
  phase-safe. Each sub-bus is then soft-clip saturated
  (`tanh(x·drive)/tanh(drive)`, `DRUM_DRIVE`, with output gain matched to input
  RMS) and given a short synthetic early-reflection room send (`DRUM_ROOM`,
  ~180 ms RT60, 12 ms pre-delay). **The room IR is generated from a fixed seed**
  — the pipeline is content-addressed and cache-reused, so a random IR would make
  identical inputs produce different renders. The summed bus then gets glue
  compression (3:1 at −14 dBFS, 8 ms / 120 ms, makeup to unity RMS).
- **Layer balance.** `LAYER_BALANCE` ∈ [0, 1] mixes the two sub-buses with
  equal-power gains (`cos(b·π/2)`, `sin(b·π/2)`), so the *total* percussion level
  stays put across the sweep and the control changes only the balance. It lives in
  `RENDER_PARAM_KEYS`, so the result page's slider invalidates only the render.
- **Bed processing.** A static notch at the kit kick's own dominant frequency
  (measured from the sample, −3 dB, Q 1.4) makes room for the sampled kick
  instead of the blunt whole-low-band duck. The envelope duck is retained, split
  with a ~400 Hz Linkwitz-Riley crossover so only the low band moves — but it is
  now driven **only by `layer: "bed"` kick and snare hits**. That is a correctness
  requirement, not a preference: flow ghosts *are* snares, so the old
  class-membership test would make every ghost note pump the instrumental.
- **True-peak limiter.** 4× oversampled detection, 1.5 ms lookahead, 50 ms
  release, ceiling `LIMITER_CEILING_DBTP`. It runs before loudness normalization,
  then the mix is re-measured and normalized to −14 LUFS with the limiter re-run
  as a safety clamp (iterated to convergence, because the second pass is
  program-dependent). This replaces the old "if the mix peaks, multiply the whole
  mix down" behaviour, which quietened everything the moment one drum peaked.
  Every gain is applied identically to the stems, so `perc + inst` still
  reconstructs `mix` — the player sums the two at unity.
- **Layer stems.** `RENDER_LAYER_STEMS=1` additionally writes
  `mix_flow_only.wav` and `mix_bed_only.wav` for auditioning the two layers
  while tuning. Off by default and not uploaded.
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

### Syllable detection (`detect`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `SYL_DETECTOR` | `nucleus` | `nucleus` (vowel nuclei) or `flux` (the old spectral-flux detector, retained as a fallback and an escape hatch). |
| `SYL_MIN_GAP_MS` | `70` | Minimum spacing between nuclei. |
| `SYL_PROMINENCE_DB` | `3.0` | Minimum peak prominence of a nucleus, in dB. |
| `SYL_LEVEL_FLOOR_DB` | `30` | A nucleus must be within this many dB of the vowel-band envelope's P95, so bleed in a silent stretch of the stem can't become a syllable. |
| `SYL_VOICED_THRESHOLD` | `0.35` | Periodicity above which a frame counts as voiced. |
| `TRANSIENT_ENABLED` | `1` | Run the consonant/transient detector. |
| `TRANSIENT_MIN_GAP_MS` | `40` | Minimum spacing between transients. |
| `TRANSIENT_SUPPRESS_MS` | `150` | Drop a consonant this close before a nucleus attack (it is that syllable's onset). Lower keeps more word-final consonants; higher drops more flams. |

`SYL_PROMINENCE` (a fraction of the track's P90−P10 range) is **gone**, replaced by
`SYL_PROMINENCE_DB`. Remove it from the `rap-flow-secrets` secret if it is set.

### Drum score (`groove`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKBONE_SOURCE` | `drums` | `drums` (transcribe the song's own `drums.wav`) or `template` (a fixed pattern on the beat grid). |
| `BACKBONE_SNAP_MS` | `25` | Soft-snap window for backbone hits. Keep it tight: the sampled kick plays over the record's own, so a wide snap makes them flam. |
| `BACKBONE_FILL` | `1` | Insert a missing backbeat snare / beat-1 kick. |
| `BACKBONE_HATS` | `0` | Let the backbone also play hats (the flow layer owns them by default). |
| `FLOW_SYLLABLE_CLASS` | `hat_closed` | Drum class for ordinary syllables (`snare` restores the old ghost-note mapping). Falls back to `hat_closed` if absent from the kit. |
| `FLOW_ACCENT_CLASS` | `hat_closed` | Drum class for stressed syllables (`ride` restores the old mapping). Falls back to `hat_closed` if absent from the kit. |
| `FLOW_MIN_GAP_MS` | `45` | Per-class minimum gap in the flow layer (the only culling it does). |
| `MERGE_SNARE_GUARD_MS` | `60` | Flow ghosts suppressed this close to a bed snare. |

### Render (`render`)

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAYER_BALANCE` | `0.5` | Default flow/backbone balance, 0–1. Overridden per render by the result page's slider. |
| `DRUM_DRIVE` | `1.6` | Saturation drive on each percussion sub-bus. |
| `DRUM_ROOM` | `0.14` | Room send wet level. |
| `DRUM_GLUE` | `1` | Bus glue compressor. |
| `LIMITER_CEILING_DBTP` | `-1.0` | True-peak ceiling. |
| `RENDER_LAYER_STEMS` | `0` | Also write `mix_flow_only.wav` / `mix_bed_only.wav` locally (not uploaded). |
| `KIT_DIR` | bundled `kits/default` | Path to the drum kit directory used by the sampler. See `kits/README.md`. |
| `DUCK_FLOOR` | `0.7` | Ducking floor gain applied under **bed** kick/snare hits (~ -3 dB). |
| `DUCK_RELEASE_MS` | `80` | Ducking release time in milliseconds. |
| `DUCK_BAND_LIMITED` | `1` | Duck only the low band via a ~400 Hz crossover; set `0`/`false` for full-band ducking. |

`GROOVE_ENABLED` and `GROOVE_TEMPERATURE` are **gone** — there is no model to
enable or to sample from. Remove them from the `rap-flow-secrets` secret.

## Running locally

```bash
pip install -r requirements.txt
python cli.py "https://youtu.be/VIDEO_ID" --outdir output
```

Local runs are the same code as production — there is no model to be missing.
`--backbone template` swaps the transcribed backbone for a fixed grid pattern,
`--layer-balance` sets the flow/backbone mix, `--layer-stems` also writes
flow-only and bed-only stems for auditioning, and `--kit /path/to/kit` selects a
different drum kit:

```bash
python cli.py "https://youtu.be/VIDEO_ID" --outdir output --layer-balance 0.35 --layer-stems
```

Intermediate artifacts are cached under `<outdir>/artifacts` (override with
`--artifacts`), so re-running the same source reuses the download and stems. To
force recomputation from a given stage while reusing everything before it, pass
`--from-stage` (one of `ingest`, `separate`, `detect`, `groove`, `render`), e.g.
re-render without re-downloading or re-separating:

```bash
python cli.py "https://youtu.be/VIDEO_ID" --outdir output --from-stage render
```

Each of the three rewritten stages is independently listenable this way, which is
how they are tuned:

```bash
python cli.py <src> --outdir out --from-stage detect   # new event list onward
python cli.py <src> --outdir out --from-stage groove   # new drum score onward
python cli.py <src> --outdir out --from-stage render   # bus chain only, seconds
```

## Tests

```bash
make test-backend        # from the repo root; installs requirements-dev.txt first
# or, by hand:
pip install -r requirements-dev.txt
python -m pytest         # parallel across cores (pytest.ini); -n 0 for serial
```

`requirements-dev.txt` is the test/lint set without the ML stack, so it
installs in seconds. `tests/test_contracts.py` also checks the seams with the
frontend and the Modal image (see the root `CLAUDE.md`).

`tests/` covers the alignment guarantee (float-equality between every flow note
and its source syllable), the merge guard, backbone transcription and gap fill,
the balance law, the limiter ceiling, bed-only ducking, the cache-key rules, and
an end-to-end score→mix integration check on synthesised stems. It also covers
the detection fixes: the torchcrepe decoder choice, the dB prominence gate on
connected syllables, onset-consonant suppression, the no-voicing warning, and
kit samples sounding at their note time. The two stages that need the ML stack
(`separate` → demucs, `detect` → torchcrepe) are covered by stubbing
`pipeline._crepe_pitch` (or faking `torchcrepe` to check the call); a full
end-to-end run is a `cli.py` listening pass.

What the tests can't cover, and what to listen for:

- does the flow read as the rapper's rhythm;
- do the backbone and the record's own drums flam (if so, deepen
  `drums_duck_db` further and keep `BACKBONE_SNAP_MS` tight — see
  `separate_audio`'s docstring);
- do the ghosts crowd the backbeat;
- does the room make the kit sound in-place or washed.
