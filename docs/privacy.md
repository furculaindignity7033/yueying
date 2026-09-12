# yueying privacy policy

_Last updated: 2026-09-09. Applies to the `yueying` Python package (CLI, MCP server and agent skill), version 0.2.0 and later._

## Summary

yueying is a local tool. It does not collect, store or transmit any personal data to the author
or to any third party. There is no telemetry, no analytics, no crash reporting, no update check and
no account.

## What runs where

All processing (reading the media file, subtitle parsing, speech recognition with faster-whisper,
scene detection and keyframe extraction with ffmpeg, contact-sheet rendering) happens on your
computer. Results are plain files in a folder you control.

## Network connections

yueying opens network connections in exactly two situations:

1. **The video site you name.** When you pass a URL, yt-dlp fetches that video (at most 720p) and
   its subtitles from the site hosting it. Only the URL you provide is contacted. yueying is meant
   for videos you are entitled to process; downloaded files are deleted after processing unless you
   opt in with `YUEYING_KEEP_SOURCE=1` or `--keep`.
2. **Hugging Face (model download).** The first time speech recognition runs, a Whisper model is
   downloaded once from huggingface.co (or the mirror you set in `HF_ENDPOINT`) into the standard
   Hugging Face cache. After that, recognition is fully offline.

Local files never leave your machine.

## Browser cookies

If you use `cookies_from_browser` (MCP) or `--cookies-from-browser` (CLI), yt-dlp reads the cookie
store of the browser you name so the video site can serve the quality your account is entitled to.
Cookies are used for that download only; they are not copied into the output folder and never sent
anywhere except to the video site itself.

## What is stored, and for how long

Outputs (`report.md`, `transcript.txt`, `transcript.srt`, `manifest.json`, `grid_*.jpg`,
`frames/*.jpg`) are written under `~/yueying_out` by default (`YUEYING_OUT_DIR` or `output_dir`
to change). They stay there until you delete the folder; yueying never deletes results on its own
except the single entry you ask to redo with `refresh=true`. `manifest.json` records the source
path or canonical URL, the title and uploader reported by the site, timestamps and the options used.

## Where the transcript and frames go

When you use yueying through an MCP client (Claude Desktop, Claude Code, Cursor, Cline, Windsurf,
VS Code, …), the transcript text and the images you request with `get_frames` / `get_frame_at` are
returned to that client, which sends them to whatever model provider the client is configured to
use. That transfer is governed by your client's and your model provider's privacy terms, not by
yueying. yueying itself has no server side and no model provider.

## Children

yueying is a developer tool and is not directed at children.

## Changes

Changes to this policy are recorded in the project's [CHANGELOG](../CHANGELOG.md) and in the git
history of this file.

## Contact

Questions or concerns: open an issue at <https://github.com/vsh5dvsch7-png/yueying/issues>.
