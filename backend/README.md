# Backend Worker

This directory contains the Modal worker (`worker.py`) and audio ingestion/processing
pipeline (`pipeline.py`) used to turn a YouTube/SoundCloud link or uploaded file into a
percussion-augmented mix.

## YouTube ingestion & the "Sign in to confirm you're not a bot" error

The worker ships with the `bgutil-ytdlp-pot-provider` (a PO token provider) configured
in `ingest_audio`, which supplies PO tokens so requests aren't rejected by YouTube's
format/GVS checks. This is working as intended and is unrelated to account cookies.

Separately, `yt-dlp` may still surface an `AUTH_REQUIRED` error whose message contains
"Sign in...":

```
AUTH_REQUIRED: ERROR: [youtube] <id>: Sign in to confirm you're not a bot. ...
```

**Important:** this is generic boilerplate text that `yt-dlp` appends to *any* error
reason containing the phrase "sign in" (see
[`_video.py`](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/youtube/_video.py) -
search for `if 'sign in' in reason.lower()`), regardless of whether the true cause is:

- The video genuinely requiring an account (private/unlisted-to-account, age-restricted,
  members-only), **or**
- YouTube flagging the *IP address* the request came from as suspicious (common for
  cloud/datacenter IPs, which is what Modal workers use), independent of the PO token.

Per yt-dlp's own docs, cookies are **only recommended/necessary for the first case**:

> This is only necessary for content that requires an account to access, such as private
> playlists, age-restricted videos and members-only content.
> — [yt-dlp wiki: Exporting YouTube cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)

For the second case (IP-reputation bot-check on an otherwise public video), passing
cookies is **not guaranteed to help** and comes with a real downside: yt-dlp's own
caution notice warns that using an account this way risks that account being
temporarily or permanently banned. Do not add cookies purely in reaction to this error
unless the video is actually account-gated — check whether the video plays fine
anonymously in a normal browser first.

If a specific public video keeps failing with this error even though it plays fine
without logging in, treat it as YouTube blocking Modal's IP range rather than an
auth problem. Options in that case (roughly in order of preference):
1. Make sure `yt-dlp` and `bgutil-ytdlp-pot-provider` are pinned to recent versions —
   YouTube/extractor compatibility changes frequently.
2. Try alternate `player_client` values in `extractor_args` (already using `ios,web`).
3. As a last resort, route the request through a residential/mobile proxy so it isn't
   coming from a datacenter IP; only fall back to account cookies (from a throwaway
   account, per yt-dlp's caution) if the video actually requires sign-in.

If cookies genuinely are needed (age-restricted/members-only/private content):

1. Export cookies from a browser where you're logged into YouTube, in Netscape
   `cookies.txt` format, following the
   [official export steps](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies)
   (use a private/incognito session so the cookies aren't rotated, and consider a
   throwaway account since the account is at risk of being banned).
2. Add the **contents** of that `cookies.txt` file as a `YT_COOKIES` value in the
   `rap-flow-secrets` Modal secret used by `process_job` (`modal secret create
   rap-flow-secrets YT_COOKIES=@cookies.txt ...` or via the Modal dashboard).
3. Redeploy the worker. `worker.py` reads `YT_COOKIES` from the environment and passes it
   to `pipeline.ingest_audio`, which writes it to a temporary `cookiefile` for `yt-dlp`.

Cookies do expire, so if the error reappears after a while, re-export and update the
secret.
