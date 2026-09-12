# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 0.2.x | yes |
| < 0.2 | no — please upgrade |

## Reporting a vulnerability

Please report security issues privately through GitHub's private vulnerability reporting on
<https://github.com/vsh5dvsch7-png/yueying/security/advisories/new>. If that form is unavailable,
open an issue at <https://github.com/vsh5dvsch7-png/yueying/issues> with the title
"security" and no exploit details; we will move the conversation to a private channel.

You can expect an acknowledgement within 7 days and a fix or mitigation plan within 30 days for
confirmed issues. Fixed versions are announced in [CHANGELOG.md](CHANGELOG.md).

## Scope and threat model

yueying runs entirely on your machine. Things worth knowing when you assess it:

- **Local subprocesses.** The MCP server never processes media itself; it spawns
  `python -m yueying.cli` as a child, which in turn runs the bundled ffmpeg (imageio-ffmpeg) and
  yt-dlp. Untrusted media files and URLs are parsed by those tools; keep them updated
  (`pip install -U yueying yt-dlp` or `uv cache clean yueying`).
- **Network.** Only two destinations: the video site of the URL you pass (via yt-dlp) and
  Hugging Face (or `HF_ENDPOINT`) to download a Whisper model once. No telemetry, no update
  checks, no crash reporting.
- **Browser cookies.** `cookies_from_browser` / `--cookies-from-browser` reads your browser's
  cookie store through yt-dlp so sites like Bilibili serve HD or member-only videos. Cookies are
  used for that download only and are never written to the output folder.
- **File system.** Results are written under `~/yueying_out` (or `YUEYING_OUT_DIR`, or an
  `output_dir` you pass). `watch_video(refresh=true)` deletes and recreates that one entry folder;
  nothing else is ever deleted. Any absolute path readable by your user can be passed as `video`,
  so run the server with the same privileges you would give the MCP client itself.
- **Model weights.** Whisper models are fetched from the Hugging Face repositories
  `Systran/faster-whisper-*` and `mobiuslabsgmbh/faster-whisper-large-v3-turbo` into the standard
  HF cache (`HF_HOME`). They are loaded by CTranslate2 (no pickle execution).

See [docs/privacy.md](docs/privacy.md) for the privacy policy.
