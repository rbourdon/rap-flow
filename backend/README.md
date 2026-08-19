# Backend Worker

This directory contains the Modal worker (`worker.py`) and audio ingestion/processing
pipeline (`pipeline.py`) used to turn a YouTube/SoundCloud link or uploaded file into a
percussion-augmented mix.

## YouTube ingestion & the "Sign in to confirm you're not a bot" error

YouTube aggressively rate-limits and bot-checks anonymous downloads. The worker already
ships with the `bgutil-ytdlp-pot-provider` (a PO token provider) configured in
`ingest_audio`, which resolves most cases automatically. However, for some videos
YouTube will still require an authenticated session, resulting in a job failing with:

```
AUTH_REQUIRED: ERROR: [youtube] <id>: Sign in to confirm you're not a bot. ...
```

To fix this, supply your own YouTube cookies so `yt-dlp` can authenticate as a signed-in
user:

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
