# rap-flow backend

Audio ingestion + processing pipeline. Audio is downloaded with
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp), separated into stems with Demucs,
analysed for syllable onsets, and rendered into a percussion mix. The heavy
lifting runs on [Modal](https://modal.com) (`worker.py`); `cli.py` runs the same
pipeline locally.

## Percussion synthesis

Detected syllable onsets are turned into a drum pattern that locks to the
track's own groove rather than following the vocal melody:

- **Beat-tracked grid + soft quantization.** The instrumental stem is
  beat-tracked (`librosa.beat.beat_track`) and a 16th-note grid is built by
  interpolating between the actual beat times, so it tolerates tempo drift.
  Each onset is soft-quantized `QUANTIZE_STRENGTH` of the way (default `0.65`)
  toward the nearest 16th gridline (snapping fully within 15 ms), removing the
  jitter that fought the on-grid instrumental. If beat tracking is degenerate
  (< 8 beats) the render falls back to unquantized times.
- **Metrical role assignment.** Roles come from the quantized metrical position,
  not vocal pitch. After estimating the 4/4 downbeat phase, on-beat hits on
  beats 1 & 3 become kicks (MIDI 36), on beats 2 & 4 snares (MIDI 38), and 8th
  /16th subdivisions become closed hats (MIDI 42). Unvoiced events always map to
  hats. Density is gated per bar (at most 4 kicks / 2 snares; weakest overflow
  demoted to hats), and empty grid positions stay as rests.
- **Fixed-pitch kit sampled from the track.** When the separated drum stem
  yields enough transients, one-shots are sliced from it, scored by isolation
  (a clean >250 ms decay) and loudness, and bucketed by spectral centroid; the
  top few candidates per bucket are cycled at render time to avoid machine-gun
  repetition. Otherwise the kit is synthesized. Either way the kit is tuned
  **once** to the track's median voiced vocal f0 (octave-folded into each drum's
  register). Any repitching uses resampling (varispeed), never a phase vocoder,
  so drum transients stay crisp.
- **Band-limited ducking.** Kick and snare hits (not hats) duck the
  instrumental. The bed is split with a ~400 Hz Linkwitz-Riley crossover and
  only the low band is ducked, so the mix doesn't pump. Each dip has a 5 ms
  attack ramp and a linear release (default 80 ms) to a floor of `0.7`
  (~ -3 dB), avoiding the zipper clicks and constant pumping of the old
  full-band, instantaneous sidechain. Set `DUCK_BAND_LIMITED=0` for full-band
  ducking with the same gentle envelope.
- **MIDI export.** The `.mid` file carries a real tempo meta message from the
  tracked BPM and uses proper `ticks_per_beat` math, so it imports on-grid into
  a DAW with the 36/38/42 drum notes.


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
| `QUANTIZE_STRENGTH` | How far each onset is pulled toward the nearest 16th gridline, 0-1 (default `0.65`). |
| `DUCK_FLOOR` | Ducking floor gain applied under kick/snare hits (default `0.7`, ~ -3 dB). |
| `DUCK_RELEASE_MS` | Ducking release time in milliseconds (default `80`). |
| `DUCK_BAND_LIMITED` | Duck only the low band via a ~400 Hz crossover when truthy (default on); set `0`/`false` for full-band ducking. |

## Running locally

```bash
pip install -r requirements.txt
python cli.py "https://youtu.be/VIDEO_ID" --outdir output
```
