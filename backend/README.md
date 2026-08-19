# Backend Worker

This directory contains the Modal worker (`worker.py`) and audio ingestion/processing
pipeline (`pipeline.py`) used to turn a YouTube/SoundCloud link or uploaded file into a
percussion-augmented mix.

## YouTube ingestion & the "Sign in to confirm you're not a bot" error

YouTube aggressively rate-limits and bot-checks anonymous downloads. The worker already
ships with the `bgutil-ytdlp-pot-provider` (a PO token provider) configured in
`ingest_audio`. **This is a separate mechanism from cookies and does not replace them.**

- **PO tokens** attest that a request is coming from a genuine client. They are mainly
  used to avoid `HTTP 403` errors on Google Video Server (GVS) / format-URL requests,
  and are not tied to a specific YouTube account.
- The **"Sign in to confirm you're not a bot"** error is a separate bot/CAPTCHA check
  based on the *reputation of the calling IP address* (see the [yt-dlp
  FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)).
  Cloud/datacenter IPs — like the ones Modal's workers run on — are frequently flagged,
  regardless of whether a valid PO token is supplied. The
  [bgutil-ytdlp-pot-provider README](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
  itself notes: *"Providing a PO token does not guarantee bypassing 403 errors or bot
  checks, but it may help your traffic seem more legitimate."*

So the PO token provider and cookies solve different problems, and a video can still hit
the bot-check even with the PO token provider working correctly:

```
AUTH_REQUIRED: ERROR: [youtube] <id>: Sign in to confirm you're not a bot. ...
```

When that happens, supply your own YouTube cookies so `yt-dlp` can authenticate as a
signed-in user (a logged-in session with a good IP reputation is much less likely to be
challenged):

1. Export cookies from a browser where you're logged into YouTube, in Netscape
   `cookies.txt` format (e.g. using a browser extension like "Get cookies.txt LOCALLY",
   or `yt-dlp --cookies-from-browser <browser> --cookies cookies.txt https://youtube.com`
   run locally).
2. Add the **contents** of that `cookies.txt` file as a `YT_COOKIES` value in the
   `rap-flow-secrets` Modal secret used by `process_job` (`modal secret create
   rap-flow-secrets YT_COOKIES=@cookies.txt ...` or via the Modal dashboard).
3. Redeploy the worker. `worker.py` reads `YT_COOKIES` from the environment and passes it
   to `pipeline.ingest_audio`, which writes it to a temporary `cookiefile` for `yt-dlp`.

Cookies do expire, so if the error reappears after a while, re-export and update the
secret.
