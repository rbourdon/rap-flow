# rap-flow backend

Audio ingestion + processing pipeline. Audio is downloaded with
[`yt-dlp`](https://github.com/yt-dlp/yt-dlp), separated into stems with Demucs,
analysed for syllable onsets, and rendered into a percussion mix. The heavy
lifting runs on [Modal](https://modal.com) (`worker.py`); `cli.py` runs the same
pipeline locally.

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

## Running locally

```bash
pip install -r requirements.txt
python cli.py "https://youtu.be/VIDEO_ID" --outdir output
```
