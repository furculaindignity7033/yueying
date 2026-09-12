# Installing the yueying MCP server (guide for AI agents such as Cline)

yueying lets an AI assistant watch videos: a local file or a video URL becomes a timestamped
transcript plus keyframe contact sheets, fully offline (platform subtitles first, local
faster-whisper otherwise; no API key). Server name: `yueying`. Transport: stdio.
Tools: `watch_video`, `get_transcript`, `search_transcript`, `get_frames`, `get_frame_at`, `list_videos`.

Follow the steps in order. Do not skip step 1: the first launch through `uvx` installs ~300 MB of
wheels and the setup command pre-downloads the speech model, so the first tool call does not time out.

## 1. Install the runtime and warm it up

Preferred: [uv](https://docs.astral.sh/uv/) (Python is not required to be pre-installed).

```bash
# Windows (PowerShell)         winget install astral-sh.uv
# macOS                        brew install uv
# Linux / macOS (curl)         curl -LsSf https://astral.sh/uv/install.sh | sh
uvx yueying mcp --setup
```

`--setup` prints the version, the Python and ffmpeg paths, probes the GPU, downloads the Whisper
model (about 480 MB on CPU, 1.6 GB with an NVIDIA GPU), runs a 2-second smoke test and prints
ready-to-paste client configuration. It needs network access once. It exits 0 on success; a non-zero
exit (or a line containing `FAILED` in steps [2/4] or [3/4]) means the model download or the smoke test
failed — fix the network (or set `HF_ENDPOINT=https://hf-mirror.com`) and run it again. The config JSON
in step [4/4] is printed either way.

Fallback without uv (Python 3.10+ required):

```bash
pip install yueying            # add "yueying[cuda]" on a machine with an NVIDIA GPU
yueying mcp --setup            # prints a config with the absolute path of the yueying-mcp executable
```

On Windows without uv you can instead double-click `install.cmd` from a checkout of the repository;
it creates `%LOCALAPPDATA%\yueying\venv`, runs `yueying mcp --setup` and prints the JSON to paste.

## 2. Add the server to Cline

Open Cline → MCP Servers → Configure (this edits `cline_mcp_settings.json`) and add:

```json
{
  "mcpServers": {
    "yueying": {
      "type": "stdio",
      "command": "uvx",
      "args": ["yueying", "mcp"],
      "env": { "PYTHONUTF8": "1" },
      "timeout": 1800,
      "autoApprove": ["get_transcript", "search_transcript", "get_frames", "get_frame_at", "list_videos"],
      "disabled": false
    }
  }
}
```

Notes:

- `timeout` is in seconds for Cline. 1800 lets a long video finish inside one `watch_video` call
  (pass `wait_seconds` up to 1500). With a shorter timeout, keep the default `wait_seconds=45` and
  call `watch_video` again with the same `video` whenever the reply starts with `RUNNING`.
- The five tools in `autoApprove` are read-only (they only read files already produced);
  `watch_video` is deliberately left out so the user confirms each new video.
- If Cline reports `spawn uvx ENOENT`, replace `"command": "uvx"` with the absolute path of `uvx`
  (`where uvx` on Windows, `which uvx` elsewhere; the client-configuration block that
  `uvx yueying mcp --setup` prints already uses it). Windows example:
  `C:\\Users\\<you>\\.local\\bin\\uvx.exe` (JSON needs the doubled backslashes).
- Without uv, use `"command": "<absolute path to yueying-mcp>"` and drop `args` (the path is printed
  by `yueying mcp --setup`; on Windows it ends in `Scripts\\yueying-mcp.exe`).
- Optional environment variables (add to `env`): `YUEYING_OUT_DIR` (results folder, default
  `~/yueying_out`), `YUEYING_MODEL` (`auto`|`tiny`|`base`|`small`|`medium`|`large-v3`|`large-v3-turbo`),
  `YUEYING_DEVICE` (`auto`|`cuda`|`cpu`), `HF_ENDPOINT=https://hf-mirror.com` (Hugging Face mirror
  for mainland China).

## 3. Verify

1. In the MCP Servers panel the `yueying` server should show six tools.
2. Call `list_videos` — it answers instantly with `No processed videos in <output root> yet. Call
   watch_video first.`
3. Call `watch_video` with an absolute path to any short local video (or a public URL) and
   `wait_seconds: 600`. Expect a reply starting with `DONE video_id=...` followed by the transcript.
   If the reply starts with `RUNNING`, call `watch_video` again with the same `video`.
4. Call `get_frames` with that `video_id` to confirm images render.

## Troubleshooting

- "No result received" / timeout on the first call: run `uvx yueying mcp --setup` once from a
  terminal and try again; the model download was still in progress.
- Garbled non-ASCII text on Windows: make sure `"PYTHONUTF8": "1"` is in `env`.
- Bilibili error 412 or YouTube "Sign in to confirm": call `watch_video` again with
  `cookies_from_browser: "edge"` (or `"chrome"`; close Chrome first on Windows).
- Slow on a CPU-only machine: pass `model: "small"` (this is already the automatic choice when no
  NVIDIA GPU is found) or `mode: "frames"` when only the pictures matter.
- Full documentation: https://github.com/vsh5dvsch7-png/yueying#readme
