# Changelog

All notable changes to yueying. Versions follow [Semantic Versioning](https://semver.org/); the
format follows [Keep a Changelog](https://keepachangelog.com/).

## Unreleased

### Added

- CI job that really transcribes on Linux and macOS (`tiny` model on CPU, CLI and MCP paths),
  so the pipeline is no longer only proven on the maintainer's Windows box; the two short test
  clips it uses are now part of the checkout.
- `Dockerfile` (CPU-only, ffmpeg from the distro) so registries that start the server and introspect it
  — Glama, Docker's MCP catalog — can run it, plus a CI job that builds the image and does an
  `initialize` + `tools/list` round trip over `docker run -i`.

## 0.2.0 — 2026-09-09

First release with an MCP server. Install with `uvx yueying mcp --setup` or `pip install yueying`.

### Added

- **MCP server** (`yueying mcp`, `yueying-mcp`, `python -m yueying mcp`; stdio) with six tools:
  `watch_video`, `get_transcript`, `search_transcript`, `get_frames`, `get_frame_at`, `list_videos`.
  Results are cached per video under `~/yueying_out` (`YUEYING_OUT_DIR`); `watch_video` long-polls
  up to `wait_seconds` and answers `RUNNING` so hosts with short tool timeouts (Claude Desktop,
  Cursor) can simply call again; progress notifications every 1.5 s; one pipeline at a time with
  queueing; `.job` markers de-duplicate work across server processes; hard job timeout
  (`YUEYING_JOB_TIMEOUT`, 7200 s) with process-tree kill.
- `yueying mcp --setup` (version, ffmpeg path, GPU probe, Whisper model pre-download, 2-second
  smoke test, ready-to-paste client config), `--check`, `--version`.
- `--ui-lang {zh,en}`: language of report.md headings and labels (the server writes `en`).
- `--model auto`: large-v3-turbo on an NVIDIA GPU, small on CPU; if the GPU trial fails the
  request falls back to small on CPU.
- `manifest.json` schema 2: additive keys `schema`, `yueying_version`, `created_at`, `ui_lang`,
  `options`, `out_dir`; `report.load_manifest()` rebases paths of moved folders and reads 0.1.x
  folders.
- New modules `mcp_server.py`, `store.py`, `query.py`, `models.py`, `__main__.py`.
- Listing artefacts: `server.json` (official MCP Registry, published by CI on release),
  `.mcp.json` + `.claude-plugin/plugin.json` (Claude Code project config / plugin),
  `llms-install.md` (Cline), `glama.json`, `docs/privacy.md`, `SECURITY.md`, icons in `docs/`.
- CI on Linux, Windows and macOS × Python 3.10 / 3.13 (`.github/workflows/ci.yml`).
- English-first `SKILL.md` that prefers the MCP tools when they are available and recommends an
  absolute `--out` (and `--ui-lang en` for English users) for the CLI flow.
- English README with per-client configuration, tool reference and a condensed Chinese section.

### Changed

- Requires Python 3.10+ (was 3.9). `mcp` is now a core dependency; `[cuda]` stays the only runtime extra.
- ffmpeg calls get timeouts (1200 s scene/audio, 60 s per frame), `stdin=DEVNULL` and no console
  window on Windows; yt-dlp gets a 30 s socket timeout and its warnings go through the log.
- `audio.wav` and the `_download/` folder are removed even when a run fails (unless `--keep`).
- When scene detection exceeds its 20-minute limit the run does not fail: keyframes are spaced evenly
  instead, the log says `场景检测超时，按固定间隔抽帧` and `manifest.json` records it in `notes`
  (shown as `Note:` in the MCP `DONE` reply).
- English PyPI summary, keywords and classifiers; `Development Status :: 4 - Beta`.
- `install.cmd` (Windows one-click) now also writes `yueying-mcp.cmd`, runs `yueying mcp --setup`
  and prints the Claude Desktop JSON with the absolute executable path (written with forward
  slashes, which JSON and Windows both accept — cmd.exe cannot reliably double backslashes).
- `yueying mcp --setup` run through `uvx` prints the absolute path of `uvx` as the `command`,
  so hosts that start without the user's PATH (Claude Desktop) do not hit `spawn uvx ENOENT`.

### Compatibility

- CLI flags, defaults (`--model large-v3-turbo`, `./yueying_out/<name>`, Chinese log lines and
  report.md) and the `--json` output line are unchanged from 0.1.x.
- Planned for 0.3: the CLI's default report language switches to English (`--ui-lang zh` keeps
  Chinese); `forget_video` / prune tools; `--all` (playlists) over MCP.

### Release checklist (maintainers)

1. `pytest -q -m "not asr and not net"` green locally and in CI; `pytest -m asr` once on a machine with the model cached.
2. Stdout purity: `python -m yueying.mcp_server < NUL > wire.txt 2> err.txt` for a few seconds — every non-empty line of `wire.txt` must parse as JSON, `err.txt` has no `Traceback`.
3. `npx @modelcontextprotocol/inspector -e PYTHONUTF8=1 <venv>/Scripts/yueying-mcp.exe` (or `uvx yueying mcp` after release; the Inspector swallows a bare `-m`, so do not pass `<python> -m yueying.mcp_server`): tools/list, `watch_video` on `test-media/test_zh.mp4`, images render.
4. `tests/test_version_sync.py` passes (server.json = pyproject = `__version__` = plugin.json); README line 1 carries the `mcp-name` marker.
5. TestPyPI upload, then `uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ yueying mcp --check` and `--setup` cold.
6. GitHub Release → PyPI (trusted publishing) → MCP Registry (OIDC job). Registry versions are immutable: a bad 0.2.0 becomes 0.2.1.

## 0.1.3 — 2026-09-09

- Several inputs in one run (`yueying a.mp4 b.mp4 <url>`), one folder each under `--out` plus an
  `index.md`; exit code 1 when any input failed.
- `--all`: process every entry of a Bilibili multi-part video / collection / playlist
  (`download.list_entries`).

## 0.1.2 — 2026-09-09

- Keyframes that are almost identical to the previous one (< 2 % of thumbnail pixels changed) are
  dropped; `--no-dedupe` keeps them.

## 0.1.1 — 2026-09-09

- Default keyframe density more than doubled (duration-based intervals: 2 s under 1 min … 20 s for
  very long videos); new `--interval` to set the seconds between keyframes directly.

## 0.1.0 — 2026-09-08

- First release: a local file or a URL (yt-dlp, ≤720p) becomes a timestamped transcript (platform
  subtitles first, local faster-whisper otherwise, GPU with CPU fallback), scene-change keyframes
  with burned-in `#number mm:ss` labels, 3x3 contact sheets and a `report.md` index; Claude Code
  skill (`--install-skill`); Windows `install.cmd`; README with a demo contact sheet.
