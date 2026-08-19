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
3. Route the request through a residential/mobile proxy (see below) so it isn't coming
   from a datacenter IP; only fall back to account cookies (from a throwaway account,
   per yt-dlp's caution) if the video actually requires sign-in.

### Using a proxy to avoid datacenter-IP blocks

`ingest_audio` accepts an optional `yt_proxy` argument, which is wired straight into
yt-dlp's `proxy` option (used for both the metadata/API lookups and the actual media
download). `worker.py` and `cli.py` read this from a `YT_PROXY` environment variable, so
no code changes are needed to enable it.

Since a self-hosted server will always live in a datacenter, this needs to be a paid
proxy service that provides residential or mobile IPs (the server's own IP is never
used directly). Providers commonly used for this include Bright Data, Oxylabs,
Smartproxy/Decodo, IPRoyal, and Webshare — any of these that offer a
"residential"/"mobile" proxy product with a standard `http://` or `socks5://` endpoint
will work. Evaluate based on cost-per-GB, session/rotation behavior (rotating per-request
IPs are more resilient to rate limiting than a single sticky IP), and legitimate/ethical
sourcing of their IP pool, since offerings vary widely on that last point.

To enable it:

1. Sign up for a residential/mobile proxy plan and get its connection string, e.g.
   `******gate.proxyprovider.example:8000`.
2. Add it as a `YT_PROXY` value in the `rap-flow-secrets` Modal secret (`modal secret
   create rap-flow-secrets YT_PROXY=******host:port ...` or via the Modal
   dashboard, alongside any existing secret values).
3. Redeploy the worker. If `YT_PROXY` is set, all YouTube/SoundCloud requests in
   `ingest_audio` are routed through it automatically; if unset, requests go out on the
   worker's normal (datacenter) IP as before.

Proxy traffic adds latency and costs money per GB, so it's worth reserving for videos
that actually hit the bot-check rather than always routing every request through it,
unless failures become frequent enough that it's worth the cost.

### If cookies genuinely are needed (account-gated content only)

For content that actually requires an account — private/unlisted-to-account,
age-restricted, or members-only videos — cookies are the correct and documented
solution:

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
