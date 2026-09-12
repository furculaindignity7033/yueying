# yueying 0.2.0 — MCP server implementation spec (FINAL)

Base: Design 1 (child-process CLI wrapper, source-keyed idempotent long-poll, separate paged image tool). Grafted: Design 2's read-side tools (range transcript, search, list) and canonical keys; Design 3's `--setup`, `model=auto`, familiar tool names, `mode` enum, model-cache check, `.job` marker, timeouts, process-tree kill, listing artefacts. Every unresolved question is decided below (section 14).

Repo: `C:/Users/Administrator/Desktop/yueying` (src layout, setuptools). Verified facts used by this spec: current version is **0.1.2** (`pyproject.toml`, `__init__.py`); `cli.py` already has `process(inp, out_dir, base, a)` extracted, plus `--all`, `--no-dedupe`, `--keep`, `--json`; `download.py` has `list_entries()`; `frames.extract(video, times, out_dir, width=1280, dedupe=True, log=print)`; `report.write_all(out_dir, meta, segs, frames, grids, text_source)` writes manifest.json LAST with keys title/source/duration/width/height/text_source/report/transcript_srt/transcript_txt/grids/frames/segments/chapters (absolute paths); mcp **2.2.0** is already installed in `.venv` (`from mcp.server.mcpserver import MCPServer, Context, Image`); pywin32 is a transitive dep of mcp on win32; uv is NOT installed on the dev box; test media `test-media/test.mp4` (24 s, English TTS) and `test-media/test_zh.mp4` (24 s, Chinese TTS) exist with reference outputs in `test-media/out_demo` and `test-media/out_zh`.

Hard rules for the implementer:
1. CLI behaviour stays backward compatible: same flags, same defaults (`--model large-v3-turbo`, `./yueying_out/<slug>`, Chinese logs, Chinese report.md by default), same `--json` line (new manifest keys are filtered out of the stdout line).
2. The server process NEVER imports yt_dlp, faster_whisper, ctranslate2 or torch, and NEVER calls `print()`. All pipeline work happens in a child `python -m yueying.cli ...` process.
3. Nothing in the library writes to `sys.stdout` except `cli.log()` and the `--json` line, which only ever run in the child.
4. All paths in tool results are absolute. All user-facing tool text is English.

---

## 1. Naming

| Thing | Value |
|---|---|
| PyPI package | `yueying` (one package; no alias package in 0.2.0) |
| Version | `0.2.0` |
| Console scripts | `yueying` (CLI, unchanged) and `yueying-mcp` (= `yueying.mcp_server:main`) |
| Canonical launch command | `uvx yueying mcp` — `mcp` is a subcommand of the `yueying` script, so uvx works without `--from`. pip users: `yueying-mcp` or `python -m yueying mcp` |
| Server name (MCPServer) | `"yueying"`, `title="Yueying – Let AI watch videos"` |
| Client config key | `"yueying"` |
| Official registry id | `io.github.vsh5dvsch7-png/yueying` (README line 1: `<!-- mcp-name: io.github.vsh5dvsch7-png/yueying -->`) |
| Tool names | `watch_video`, `get_transcript`, `search_transcript`, `get_frames`, `get_frame_at`, `list_videos` (unprefixed; hosts namespace them as `mcp__yueying__*`) |
| PyPI summary | `Let AI watch videos: local files or YouTube/Bilibili URLs -> timestamped transcript + keyframes + contact sheets. Offline, no API key. MCP server + CLI + agent skill.` |
| GitHub description | same sentence |
| Keywords/topics | mcp, mcp-server, model-context-protocol, video, video-analysis, watch-video, transcript, keyframes, whisper, faster-whisper, youtube, bilibili, offline, claude-desktop, claude-code, cursor, cline, windsurf, agent-skills |

Never put "claude" in a package/server/tool name. README explains the name once: "Yueying (阅影) literally means 'read video'."

## 2. pyproject.toml (complete diff)

```toml
[project]
name = "yueying"
version = "0.2.0"
description = "Let AI watch videos: local files or YouTube/Bilibili URLs -> timestamped transcript + keyframes + contact sheets. Offline, no API key. MCP server + CLI + agent skill."
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [{ name = "vsh5dvsch7-png" }]
keywords = ["mcp", "mcp-server", "model-context-protocol", "video", "video-analysis", "watch-video", "transcript", "keyframes", "whisper", "faster-whisper", "youtube", "bilibili", "offline", "claude-desktop", "claude-code", "cursor", "cline", "windsurf", "agent-skills"]
classifiers = [
  "Development Status :: 4 - Beta",
  "Environment :: Console",
  "Intended Audience :: Developers",
  "License :: OSI Approved :: MIT License",
  "Operating System :: OS Independent",
  "Programming Language :: Python :: 3",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Topic :: Multimedia :: Video",
  "Topic :: Scientific/Engineering :: Artificial Intelligence",
]
dependencies = [
  "faster-whisper>=1.1",
  "yt-dlp>=2025.1.1",
  "imageio-ffmpeg>=0.5",
  "pillow>=10.1",
  "mcp>=2.2,<3",
]

[project.optional-dependencies]
cuda = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]
dev = ["pytest>=8", "anyio>=4.9", "mcp[cli]>=2.2,<3", "build", "twine"]

[project.urls]
Homepage = "https://github.com/vsh5dvsch7-png/yueying"
Repository = "https://github.com/vsh5dvsch7-png/yueying"
Issues = "https://github.com/vsh5dvsch7-png/yueying/issues"
Documentation = "https://github.com/vsh5dvsch7-png/yueying#readme"
Changelog = "https://github.com/vsh5dvsch7-png/yueying/blob/main/CHANGELOG.md"

[project.scripts]
yueying = "yueying.cli:main"
yueying-mcp = "yueying.mcp_server:main"

[tool.setuptools.package-data]
yueying = ["skill/SKILL.md"]

[tool.pytest.ini_options]
markers = ["asr: runs real speech recognition (needs model + minutes)", "net: needs network"]
```

`mcp` is a CORE dependency (a dozen small pure-Python wheels next to a 100 MB ctranslate2); `[cuda]` stays the only runtime extra. `src/yueying/__init__.py`: `__version__ = "0.2.0"`. Re-run `pip install -e ".[dev]"` in the venv (its egg-info is stale).

## 3. Library changes (backward compatible, additive)

### 3.1 `cli.py`
1. Subcommand dispatch as the FIRST statement of `main(argv=None)`:
   ```python
   args = list(sys.argv[1:] if argv is None else argv)
   if args[:1] == ["mcp"]:
       from .mcp_server import main as _mcp_main
       return _mcp_main(args[1:])
   ```
2. New flags: `--ui-lang {zh,en}` (default `zh`; forwarded to `report.write_all(..., ui_lang=...)` and used for `text_source["desc"]`), `--model auto` accepted (default stays `large-v3-turbo`).
3. `process()`: wrap the body after `os.makedirs(out_dir)` in `try/finally` that removes `audio.wav` and, when `not a.keep`, the `_download` dir. Pass `options=_options_dict(a)` to `write_all` (keys: model, device, lang, interval, frames, scene, no_dedupe, no_frames, no_asr, force_asr, keep, ui_lang).
4. `--json` line: filter out `segments` AND the new keys `schema`, `yueying_version`, `created_at`, `options`, `ui_lang`, `out_dir` so the stdout line is byte-identical to 0.1.x.
5. Add a comment block above `process()`:
   `# LOAD-BEARING LOG LINES: mcp_server.py parses "[n/4]", "  下载 N%", "  模型 ", "  检测语言", "  识别进度 N%", "  抽帧 n/N", "完成，用时", "《title》". Changing them requires updating mcp_server._parse_line and tests/fixtures/cli_log_sample.txt.`
   Log lines themselves stay Chinese and unchanged (they are an internal contract, not UI).
6. `FileNotFoundError` path unchanged (message `找不到文件：<abs>`); exit code 2 for single input is preserved by catching it in `main` (currently it raises — keep current behaviour: it propagates; the server classifies it by the `找不到文件` / `FileNotFoundError` text). No change.

### 3.2 `asr.py`
- `resolve_model(model_name: str, device: str) -> str`: `"auto"` → `"large-v3-turbo"` if `device == "cuda"` else `"small"`; otherwise identity.
- `transcribe(wav, model_name, device, language, duration, log=print)`: call `pick_device`, then `resolve_model`. In the existing GPU→CPU fallback branch: if the requested name was `"auto"`, reload `"small"` on CPU (int8) instead of the large model. `info["model"]` reports the resolved name; add `info["requested_model"]`.
- `prepare_model(model_name="auto", device="auto", log=print) -> dict`: runs `_add_cuda_dlls`, `pick_device`, `resolve_model`, `_load`; returns `{model, device, compute_type, cache_dir}` where `cache_dir` is the HF snapshot dir (`faster_whisper.utils.download_model(model, local_files_only=True)` or the WhisperModel's `model_path`). Used by `yueying mcp --setup`.
- `model_repo(model_name) -> str`: `"mobiuslabsgmbh/faster-whisper-large-v3-turbo"` for large-v3-turbo, else `f"Systran/faster-whisper-{name}"`. Pure; imported by the server (no heavy import — put it in a tiny module `yueying/models.py` so the server does not import asr.py).
- Move `MODEL_SIZES = {"tiny": "75 MB", "base": "140 MB", "small": "480 MB", "medium": "1.5 GB", "large-v3": "3 GB", "large-v3-turbo": "1.6 GB"}` and `model_repo()` into `src/yueying/models.py`.

### 3.3 `ffm.py`
- `ffmpeg_exe()` cached with `functools.lru_cache`.
- `run(args, check=True, timeout=None)`: add `stdin=subprocess.DEVNULL`, `creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0`, pass `timeout`; on `subprocess.TimeoutExpired` raise `MediaError(f"ffmpeg timed out after {timeout}s")`.
- `class MediaError(RuntimeError)` (keeps `RuntimeError` compatibility); `run` raises it with the stderr tail.
- Callers: `frames.scene_times` and `extract_audio` pass `timeout=1200`; per-frame extraction passes `timeout=60`.

### 3.4 `frames.py`
- Factor `extract_one(video: str, t: float, out_path: str, width: int = 1280, label: str | None = None, timeout: float = 60) -> bool` out of `extract()` (one `ffmpeg -ss t -i video -frames:v 1 -vf scale=min(width,iw):-2`, then `_burn(label)` if label, save q=88). `extract()` uses it; `get_frame_at` uses it with `label=f"@{fmt_time(t)}"`.

### 3.5 `download.py`
- Add to `ydl_opts`: `"logger": _YtDlpLogger(log)` (class with `debug()` no-op unless message starts with `[download]`, `warning()`/`error()` → `log("  yt-dlp: " + msg)`), `"socket_timeout": 30`. Behaviour otherwise unchanged.

### 3.6 `report.py`
- Label table `_T = {"zh": {...current strings...}, "en": {"source": "Source", "author": "Author", "duration": "Duration", "text_source": "Text source", "overview": "Visual overview", "frames": "Keyframes", "transcript": "Transcript", "no_text": "(no transcript: no subtitles and speech recognition skipped)", "hint": "Read the contact sheets first, then open individual frames for details; match pictures to the transcript by timestamp.", ...}}`. `write_all(out_dir, meta, segs, frames, grids, text_source, *, ui_lang="zh", options=None) -> dict`. English `text_source.desc` variants: `"subtitle file {name} ({n} cues)"`, `"local speech recognition, faster-whisper {model} on {device}, detected {lang} ({p:.0%}), {n} segments"`, `"none"`. The CLI computes `desc` in cli.py today — move both zh/en desc strings into `report.describe_text_source(text_source, ui_lang)` and have cli.py call it.
- Additive manifest keys: `"schema": 2`, `"yueying_version"`, `"created_at"` (ISO 8601 local), `"ui_lang"`, `"options"`, `"out_dir"` (absolute). Existing keys untouched.
- `load_manifest(folder) -> dict`: reads `manifest.json`; for every absolute path key (`report`, `transcript_srt`, `transcript_txt`, `grids[]`, `frames[].file`) that does not exist, rebase it to `folder / relpath(path, manifest["out_dir"] or dirname(manifest["report"]))`. Works for CLI-produced 0.1.x folders too.

### 3.7 New `store.py` (~180 lines, stdlib only)
```python
def out_root() -> Path            # $YUEYING_OUT_DIR (abs, expanduser) else Path.home()/"yueying_out"
def canonical_url(url: str) -> str
def source_key(source: str) -> str  # 8 hex
def entry_name(source: str) -> str  # "<slug>-<key8>"
def entry_dir(source: str, root: Path | None = None) -> Path
def resolve(video: str, root: Path | None = None) -> Path | None  # video_id | folder | path | URL -> entry dir with manifest.json, else None
def list_entries(root: Path | None = None) -> list[dict]  # [{dir, key, title, duration, text_source, created_at, size_bytes, source}] newest first
def dir_size(p: Path) -> int
def read_job_marker(entry: Path) -> dict | None   # {pid, started, server_pid} or None; None if older than 3 h or pid not alive
def write_job_marker(entry: Path, pid: int) -> None
def clear_job_marker(entry: Path) -> None
def parse_time(s: str | float | int) -> float     # "185", "185.5", "3:05", "1:02:03"
```
- `canonical_url`: lowercase scheme+host, strip leading `www.`/`m.`, drop fragment. YouTube (`youtube.com/watch?v=ID`, `youtu.be/ID`, `youtube.com/shorts/ID`, `youtube.com/live/ID`) → `https://www.youtube.com/watch?v=ID`. Bilibili (`bilibili.com/video/BV...` or `/video/avNNN`, optional `p`) → `https://www.bilibili.com/video/<id>` plus `?p=N` only when N>1. Everything else: drop query params whose name matches `utm_*`, `spm*`, `vd_source`, `share_*`, `from`, `t`, `feature`, `si`, `fbclid`, `gclid`; sort the rest. `b23.tv`/`v.douyin.com` short links are NOT resolved (no network in the server) — documented.
- `source_key`: URL → `sha1(canonical_url)[:8]`; local file → `sha1(os.path.normcase(abspath) + "\0" + str(size) + "\0" + str(int(mtime)))[:8]`.
- `entry_name` slug: YouTube `yt-<id>`; Bilibili `bili-<BVid>[-p<n>]`; other URLs `<first host label>-<last path segment without query>`; local files `cli.slug(basename-without-ext)`; slug truncated to 40 chars, then `-<key8>`. The entry folder is NEVER renamed after processing (title lives in manifest.json).
- `resolve(video)`: (a) 8-hex string → scan `root` for a dir ending in `-<key>`; (b) existing directory containing `manifest.json` → itself; (c) `is_url` or existing file → `entry_dir(video)` if it has manifest.json; else None.

### 3.8 New `query.py` (~160 lines, pure functions over a loaded manifest)
```python
def transcript_text(manifest, start=0.0, end=None, fmt="paragraphs", max_chars=8000) -> tuple[str, float | None]  # (text, next_start)
def search(manifest, query, context_s=15.0, limit=10) -> list[dict]   # [{time, end, snippet, frame_index, grid_no}]
def nearest_frame(manifest, t) -> dict | None
def grid_no_for_frame(manifest, index) -> int | None   # grids pack frames 9 per sheet in order: (position_in_list // 9) + 1
def grid_ranges(manifest) -> list[dict]                 # [{no, file, first_index, last_index, start, end}]
def paragraphs_around(manifest, t, n=3) -> list[dict]
def overview(manifest, max_chars) -> str                # the DONE body (section 6.1)
def shrink(path, max_width, quality) -> bytes           # Pillow: RGB, thumbnail((w, w)), JPEG optimize
```
- Paragraphs always come from `report.paragraphs(manifest["segments"])` with default `max_len=45, gap=2` so timestamps match transcript.txt exactly. `fmt="segments"` → `[mm:ss-mm:ss] text` per segment; `fmt="srt"` → SRT blocks. Truncation is on a paragraph/segment boundary; `next_start` = start of the first unreturned item.
- `search`: terms = query.split(); case-insensitive substring match per paragraph (CJK-safe since substring); rank by number of distinct terms matched desc, then time asc; snippet = paragraphs within ±context_s merged.

## 4. MCP server (`src/yueying/mcp_server.py`, ~500 lines)

### 4.1 Module top
```python
import os, sys
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("CT2_VERBOSE", "-3")
os.environ.setdefault("PYTHONUTF8", "1")
import contextlib, json, logging, re, subprocess, threading, time
from pathlib import Path
from typing import Annotated, Literal
import anyio
from pydantic import Field
from mcp.server.mcpserver import Context, Image, MCPServer
from mcp.types import ToolAnnotations
from yueying import __version__, store, query, models
```
Lazy inside tools only: `PIL`, `yueying.ffm`, `yueying.frames`, `yueying.report.load_manifest`. Import must complete in <0.5 s (Cline's 5 s tools/list limit). Module import has no side effects (no server run, no dir creation).

### 4.2 Lifespan
```python
@contextlib.asynccontextmanager
async def lifespan(server):
    sys.stdout.flush(); sys.stdout = sys.stderr            # AFTER the SDK claimed fd 1 (verified-safe order)
    with contextlib.suppress(Exception):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    store.out_root().mkdir(parents=True, exist_ok=True)
    try: yield
    finally:
        for job in list(JOBS.values()): job.kill("server shutdown")
```
Logging: `logger = logging.getLogger("yueying.mcp")`; `MCPServer()` already configures stderr logging; never `print()` after the module top.

### 4.3 Server object
```python
mcp = MCPServer("yueying", title="Yueying – Let AI watch videos", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan)
```
`INSTRUCTIONS` (exact text):
> yueying lets you watch videos offline. Workflow: call watch_video with an absolute file path or a video URL. If the reply starts with RUNNING, call watch_video again with the same video to keep waiting (it resumes the same job; never start a different video meanwhile). Answer from the transcript in the reply; use get_transcript for time ranges, search_transcript to find where something is said, get_frames to see the visual storyline (3x3 contact sheets, read these before single frames), and get_frame_at for one exact moment (code, slides, UI). Speech recognition mis-hears names, numbers and code — trust on-screen text from frames over the transcript. Quote the video with timestamps like (03:15). Images cost 1–2K tokens each; keep them few. Hosts that can read local files may open report.md, grid_NN.jpg and frames/*.jpg at the paths given; hosts that cannot (Claude Desktop) use get_frames / get_frame_at. In Claude Desktop and Cursor keep wait_seconds at the default (45); in Claude Code or Cline you may pass wait_seconds up to 1500 to get the result in one call.

### 4.4 Job runner
```python
JOBS: dict[str, Job] = {}            # key8 -> Job (process-local)
_SLOT = threading.Semaphore(int(os.environ.get("YUEYING_MAX_JOBS", "1")))
_FF   = threading.Semaphore(2)       # server-side ffmpeg (get_frame_at)
JOB_TIMEOUT = float(os.environ.get("YUEYING_JOB_TIMEOUT", "7200"))

class Job:
    key, source, entry: Path, cmd: list[str]
    state: Literal["queued","running","done","error"]; stage: int (0-4); pct: float (0-1); message: str
    lines: deque(maxlen=200); manifest: dict | None; started: float; proc: Popen | None; model_cached: bool
    def __init__(...): threading.Thread(target=self._run, daemon=True).start()
    def _run(self): with _SLOT: state="running"; write_job_marker; Popen(...); read lines -> _parse_line; wait; classify; clear_job_marker
    def kill(self, reason): _kill_tree(self.proc); state="error"; message=reason
```
Popen: `[sys.executable, "-m", "yueying.cli", src, "--out", str(entry), "--json", "--ui-lang", os.environ.get("YUEYING_LANG","en"), "--model", model, "--device", os.environ.get("YUEYING_DEVICE","auto")] + optional ["--lang", L] ["--no-frames"] ["--no-asr"] ["--interval", str(x)] ["--cookies-from-browser", b] ["--keep" if YUEYING_KEEP_SOURCE=="1"]`; `stdin=DEVNULL, stdout=PIPE, stderr=STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW (win32), start_new_session=True (POSIX), env=os.environ | {"PYTHONUTF8":"1","PYTHONIOENCODING":"utf-8","HF_HUB_DISABLE_PROGRESS_BARS":"1","TQDM_DISABLE":"1","CT2_VERBOSE":"-3"}`. On Windows, best-effort assign the child to a Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` via `win32job` (pywin32, wrapped in try/except) so the ffmpeg tree dies with the server. A watchdog thread kills the job after `JOB_TIMEOUT`.

`_kill_tree(proc)`: Windows `subprocess.run(["taskkill","/F","/T","/PID",str(pid)], creationflags=CREATE_NO_WINDOW)`; POSIX `os.killpg(pid, SIGKILL)`; then `proc.wait(10)`.

`_parse_line(line)` (regexes, stage → (pct range, English message)):
- `^\[1/4\]` → stage 1, pct 0.00, "downloading / reading file"; `^\s+下载 (\d+)%` → pct = 0.00 + 0.25·n/100, "downloading video n%"
- `^\s+《(.+?)》` → job.title
- `^\[2/4\]` → stage 2, 0.25, "looking for subtitles"; `^\s+模型 ` → 0.30, "loading speech model" + (" (first run downloads ~SIZE, this can take several minutes)" if not model_cached); `^\s+检测语言` → 0.35, "speech recognition"; `^\s+识别进度 (\d+)%` → 0.35 + 0.30·n/100, "speech recognition n%"; `^\s+用字幕` → 0.60, "using platform subtitles"
- `^\[3/4\]` → stage 3, 0.65, "detecting scene changes"; `^\s+抽帧 (\d+)/(\d+)` → 0.70 + 0.25·a/b, "extracting keyframes a/b"
- `^\[4/4\]` → stage 4, 0.97, "writing report"
- `^\{` → `json.loads` → manifest (from `--json`); `^完成，用时` → 1.0
- else: appended to lines only.
Success = rc == 0 and manifest is not None and `entry/manifest.json` exists. Fixture test: `tests/fixtures/cli_log_sample.txt` (captured from a real run) must parse to the expected stage sequence.

Model-cache check (before spawning): `model_cached = any((hf_hub_cache / f"models--{repo.replace('/','--')}" / "snapshots").glob("*/model.bin"))` for the repo(s) the request can resolve to (`auto` checks both large-v3-turbo and small; message then says "~480 MB (CPU) / ~1.6 GB (GPU)"). `hf_hub_cache = $HF_HUB_CACHE or $HF_HOME/hub or ~/.cache/huggingface/hub`.

ETA (only when model_cached and pct > 0.05): `eta = elapsed/pct - elapsed`, printed as "est. ~N min remaining".

### 4.5 watch_video algorithm
1. `src = video.strip()`. If not URL: `p = Path(src).expanduser()`; if not `p.is_file()` → ERROR (no spawn); `src = str(p.resolve())`.
2. `key = store.source_key(src)`; `entry = Path(output_dir).resolve() if output_dir else store.entry_dir(src)`.
3. `refresh=True` and a Job for key is running → `job.kill("cancelled by refresh=true")`, wait, then `shutil.rmtree(entry)` with 3 retries × 1 s (WinError 32), fall through. `refresh=True` with no job → rmtree same way.
4. Cache hit check (skip if refresh): `entry/manifest.json` exists AND no live `.job` marker AND `_covers(manifest, mode)` where `_covers` = (mode == "transcript" or manifest["frames"] or manifest["width"] == 0 or options.no_frames is False-but-video-has-no-video-stream) AND (mode == "frames" or manifest["text_source"]["kind"] != "none" or not manifest.get("options",{}).get("no_asr")) → render DONE (`cached: yes`).
5. Live `.job` marker from ANOTHER process (pid != ours, fresh) → poll for manifest.json until wait_seconds; RUNNING text says "another yueying server process is processing this video".
6. Job for key in JOBS and state queued/running → attach (options ignored; RUNNING text notes it).
7. Else create Job. 8. Long-poll: `while job.state in (queued, running) and monotonic < deadline: await ctx.report_progress(job.pct, 1.0, job.message); await anyio.sleep(1.5)`. 9. Render DONE / RUNNING / ERROR.

### 4.6 Tools (annotations, exact signatures, exact description text)

All text tools are `async def` (watch) or plain `def` (file reads, run in worker threads) with `structured_output=False`. `Lang = Literal["auto","zh","en","ja","ko","fr","de","es","ru","pt","it","yue"]`, `Model = Literal["auto","tiny","base","small","medium","large-v3","large-v3-turbo"]`, `Browser = Literal["chrome","edge","firefox","brave","chromium","safari"]`.

**watch_video** — `ToolAnnotations(title="Watch a video", read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)`
```python
async def watch_video(
    video: Annotated[str, Field(description="Absolute path to a local video/audio file, or a video page URL (YouTube, Bilibili, Douyin, Xiaohongshu, TikTok, Vimeo, X and other yt-dlp sites)")],
    ctx: Context,
    mode: Annotated[Literal["full","transcript","frames"], Field(description="full = transcript + keyframes (default); transcript = no keyframes; frames = no speech recognition (platform subtitles still used)")] = "full",
    language: Annotated[Lang, Field(description="Spoken language; auto detects it. Set it when you know it for better accuracy")] = "auto",
    model: Annotated[Model, Field(description="Whisper model used only when the video has no subtitles. auto = large-v3-turbo on an NVIDIA GPU, small on CPU")] = "auto",
    frame_interval_seconds: Annotated[float | None, Field(ge=0.5, le=120, description="Approximate seconds between keyframes; None = automatic by duration (2–20 s). Use 2 for code/slide-heavy screencasts")] = None,
    cookies_from_browser: Annotated[Browser | None, Field(description="Reuse a browser login for HD or member-only Bilibili / sign-in-gated YouTube. Close Chrome first on Windows")] = None,
    output_dir: Annotated[str | None, Field(description="Absolute folder for the results; default $YUEYING_OUT_DIR/<name>-<id>")] = None,
    refresh: Annotated[bool, Field(description="Discard cached results (and any running job) for this video and process it again")] = False,
    wait_seconds: Annotated[int, Field(ge=0, le=1500, description="How long this call may block before answering RUNNING. Keep 45 in Claude Desktop/Cursor (60 s client timeout); Claude Code/Cline may use up to 1500")] = 45,
    max_chars: Annotated[int, Field(ge=2000, le=100000, description="Maximum characters of transcript included in the reply")] = 12000,
) -> str
```
Description: "Turn a video into a timestamped transcript plus a keyframe overview so you can summarize it, answer questions about it, extract steps, commands or code, or write notes. `video` is an absolute path to a local video/audio file or a YouTube / Bilibili / Douyin / Xiaohongshu / TikTok / Vimeo URL. Fully offline: platform subtitles are used when they exist, otherwise local Whisper speech recognition (GPU when available, CPU otherwise); no API key. Results are cached per video, so calling again with the same video is instant. Typical time: 5–60 s for short subtitled videos, a few minutes for long videos without subtitles; the very first speech recognition downloads a model once (~480 MB on CPU, ~1.6 GB on GPU). If the reply starts with RUNNING, call watch_video again with the same `video` — it resumes waiting for the same job; do not change options and do not start other videos meanwhile. The reply is an English overview: metadata, chapters, the list of contact sheets (see them with get_frames), file paths, and the transcript with [mm:ss] timestamps (truncated at max_chars with a start time for get_transcript). Cite timestamps like (03:15). Not for live streams or images."

**get_transcript** — `ToolAnnotations(title="Read transcript by time range", read_only_hint=True, idempotent_hint=True)`
```python
def get_transcript(
    video: Annotated[str, Field(description="video_id from watch_video/list_videos, the results folder, or the same path/URL given to watch_video")],
    start: Annotated[str, Field(description="Start time: seconds, mm:ss or h:mm:ss")] = "0",
    end: Annotated[str | None, Field(description="End time (same formats); None = to the end")] = None,
    format: Annotated[Literal["paragraphs","segments","srt"], Field(description="paragraphs = compact [mm:ss] text (fewest tokens); segments = one line per subtitle cue with start-end; srt = subtitle blocks")] = "paragraphs",
    max_chars: Annotated[int, Field(ge=1000, le=100000)] = 8000,
) -> str
```
Description: "Read part of an already-watched video's transcript with [mm:ss] timestamps. Use when the watch_video overview was truncated, when the user asks about a specific time range, or to export subtitles (format='srt'). Returns at most max_chars; when truncated the last line gives next_start so you can continue from there. Paragraph format is the cheapest."

**search_transcript** — `ToolAnnotations(title="Search transcript", read_only_hint=True, idempotent_hint=True)`
```python
def search_transcript(
    video: Annotated[str, Field(description="video_id, results folder, or the path/URL given to watch_video")],
    query: Annotated[str, Field(description="Words to look for; separate alternatives with spaces (any term matches, more terms rank higher). Case-insensitive")],
    context_seconds: Annotated[float, Field(ge=0, le=120, description="Seconds of surrounding text to include around each hit")] = 15,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str
```
Description: "Find where something is said in an already-watched video. Each hit shows the time, the surrounding sentences, and the nearest keyframe number and contact-sheet number so you can follow up with get_frame_at or get_transcript. Use this instead of paging the whole transcript when the user asks 'when does he mention X' or 'find the part about Y'."

**get_frames** — `ToolAnnotations(title="Show contact sheets / keyframes", read_only_hint=True, idempotent_hint=True)`
```python
def get_frames(
    video: Annotated[str, Field(description="video_id, results folder, or the path/URL given to watch_video")],
    kind: Annotated[Literal["grids","frames"], Field(description="grids = 3x3 contact sheets, 9 keyframes per image (start here); frames = individual full-size keyframes")] = "grids",
    start: Annotated[int, Field(ge=1, description="1-based number of the first image: contact-sheet number for grids, keyframe number (as printed on the tiles) for frames")] = 1,
    count: Annotated[int, Field(ge=1, le=3, description="Images per call; keep 2 or fewer in Claude Desktop")] = 2,
    max_width: Annotated[int, Field(ge=320, le=1920, description="Downscale width in pixels; 1280 is about 150 KB per contact sheet")] = 1280,
) -> list[str | Image]
```
Description: "See what is on screen in an already-watched video. Returns contact-sheet images (3x3 keyframes in time order, every tile labelled '#number mm:ss' bottom-left) or individual keyframes. Read the contact sheets first to get the visual storyline, then request single frames only when you need to read code, slides or UI text. At most 3 images per call (default 2), downscaled to max_width; page with start/count. Every image is preceded by its absolute file path so hosts that can read files may open the full-size original instead."

**get_frame_at** — `ToolAnnotations(title="Look at one moment", read_only_hint=True, idempotent_hint=True)`
```python
def get_frame_at(
    video: Annotated[str, Field(description="video_id, results folder, or the path/URL given to watch_video")],
    time: Annotated[str, Field(description="Timestamp: seconds, mm:ss or h:mm:ss, e.g. '03:15'")],
    max_width: Annotated[int, Field(ge=320, le=1920)] = 960,
) -> list[str | Image]
```
Description: "Look closely at one moment of an already-watched video, e.g. to read code, a slide, a chart or a UI. For local files that still exist the exact frame at that time is extracted from the video; otherwise the nearest cached keyframe is returned and the caption says so. Also returns the transcript paragraphs spoken around that time. Returns one image (~100 KB at 960 px)."

**list_videos** — `ToolAnnotations(title="List processed videos", read_only_hint=True, idempotent_hint=True)`
```python
def list_videos(limit: Annotated[int, Field(ge=1, le=200)] = 20) -> str
```
Description: "List videos already processed by yueying on this machine (newest first) and jobs currently running, with video_id, title, duration, text source, date, folder and size. Use when the user refers to a video watched earlier, to get a video_id for the other tools, or to see how much disk space results use. Instant and read-only."

`forget_video`, `cancel`, `prepare_model` tools: NOT in 0.2.0 (refresh handles reprocessing; deletion is a folder the user can see; `--setup` pre-downloads the model).

### 4.7 `main(argv=None)`
- `--check`: write `yueying-mcp 0.2.0 ok (python X, ffmpeg <path>)` to stderr, exit 0.
- `--version`: same, stdout is fine here (no server).
- `--setup [--model auto|tiny|...]`: (1) print version, Python path, ffmpeg path (`imageio_ffmpeg.get_ffmpeg_exe()`), output root; (2) GPU probe in a CHILD: `python -c "from yueying import asr; ..."` printing device/compute type; (3) download the model in a child: `python -c "from yueying.asr import prepare_model; ..."` with the child's stdout passed through; (4) smoke test: generate a 2 s clip with `ffmpeg -f lavfi -i testsrc=duration=2:size=640x360:rate=10 -f lavfi -i sine=frequency=440:duration=2 <tmp>/setup.mp4` (if lavfi is unavailable in the static build, print "smoke test skipped: lavfi not available" and continue) and run `python -m yueying.cli <tmp>/setup.mp4 --no-asr --out <tmp>/out`, delete tmp; (5) print ready-to-paste JSON for Claude Desktop, Cursor/Windsurf/Cline and the `claude mcp add` line, using `shutil.which("uvx")` if found else the absolute path of the current `yueying-mcp` executable next to `sys.executable`. All output of `--setup` goes to stdout (no server is started). No `--install <client>` config merging in 0.2.0.
- otherwise `mcp.run()` (stdio).

## 5. Long-running strategy — concrete numbers
- `wait_seconds` default 45 (Claude Desktop hard ~60 s, Cursor 60–120 s), max 1500 (Claude Code/Cline with `timeout` configured). Poll interval 1.5 s; `ctx.report_progress(pct, 1.0, message)` each iteration (no-op if no progressToken; keeps Claude Code's idle timer alive; never relied on to extend timeouts).
- One pipeline at a time per server process (`YUEYING_MAX_JOBS=1`); extra sources are `queued` ("waiting for another video to finish").
- Cross-process dedupe via `<entry>/.job` (JSON `{pid, started, server_pid}`; stale after 3 h or when pid is dead).
- Job hard timeout `YUEYING_JOB_TIMEOUT=7200` s → kill tree + ERROR. ffmpeg: 1200 s scene/audio, 60 s per frame. yt-dlp `socket_timeout=30`.
- Cancel = `refresh=true` (kills, deletes, restarts) or server shutdown. No cancel tool.
- Recommended host settings shipped in README/llms-install.md/.mcp.json: Claude Code `"timeout": 1800000` (ms), Cline `"timeout": 1800` (s) + `autoApprove` for the five read-only tools.
- First-run: README step 1 is `uvx yueying mcp --setup` (installs, downloads model, smoke-tests, prints config); `--check` for CI/pre-warm. Server also detects an uncached model and says so in RUNNING.

## 6. Reply formats (exact)

### 6.1 DONE (watch_video)
```
DONE video_id=3f9a2c1e (cached: no, took 87 s)
Title: <title>
Source: <canonical url or absolute path>
Uploader: <uploader>            (URLs only, if known)
Duration: 06:21 · 1280x720
Text: platform subtitles (zh, 95 cues) | local speech recognition (faster-whisper large-v3-turbo on cuda), detected zh 100%, 95 segments | none (no audio) | none (skipped: mode=frames)
Folder: C:\Users\me\yueying_out\bili-BV1xx-3f9a2c1e
Files: report.md, transcript.txt, transcript.srt, manifest.json, 7 contact sheets (grid_01.jpg…grid_07.jpg), 63 keyframes in frames\
Chapters: 00:00 Intro · 01:12 Setup · 03:40 Demo      (if any)
Contact sheets (see with get_frames):
  grid 1: frames #1–#9, 00:02–00:55
  grid 2: frames #10–#18, 00:58–01:52
  ...
Transcript (paragraphs, [mm:ss]):
[00:00] ...
[00:47] ...
TRUNCATED at 05:10 — continue with get_transcript(video="3f9a2c1e", start="05:10")   (only when cut)
Next: cite timestamps like (03:15); call get_frames(video="3f9a2c1e") to see the visuals.
```
### 6.2 RUNNING
```
RUNNING video_id=3f9a2c1e · stage 2/4 speech recognition 40% · elapsed 46 s · est. ~1 min remaining
Title: <title if known yet>
Call watch_video again with the same video to keep waiting (each call waits up to wait_seconds). Do not start another video. (Options of the running job apply; pass refresh=true to restart with different options.)
```
When the model is not cached the stage line reads `stage 2/4 loading speech model (first run downloads ~1.6 GB, this can take several minutes)` and no ETA is shown.
### 6.3 ERROR (classifier over the last 40 log lines, first match wins; then `log:` + last 10 raw lines except for case 1)
1. `找不到文件|FileNotFoundError|No such file` → `ERROR: file not found: <path>. Pass an absolute path to an existing video file.`
2. `412|-352|Sign in to confirm|login|cookies|members-only|premium|需要登录|-404` → `ERROR: the site refused the download (login or cookies required). Retry with cookies_from_browser="edge" or "chrome" (on Windows close Chrome first — it locks its cookie database).`
3. `Unsupported URL|HTTP Error 40\d|Video unavailable|is not a valid URL|DownloadError` → `ERROR: could not fetch this URL: <matching line>. Check the link, or update yt-dlp (pip install -U yt-dlp / uv cache clean yueying).`
4. `huggingface|hf-mirror|ConnectionError|Max retries|SSLError|timed out|Read timed out` → `ERROR: could not download the Whisper speech model. Run "yueying mcp --setup" once with network access, or set HF_ENDPOINT=https://hf-mirror.com in the server env, or pass model="small".`
5. `CUDA|cudnn|cublas|out of memory|ctranslate2` → `ERROR: GPU speech recognition failed. Retry with model="small", or set YUEYING_DEVICE=cpu in the server env.`
6. `Invalid data found|moov atom|could not find codec|ffmpeg .* failed|MediaError` → `ERROR: ffmpeg could not read this file — it does not look like a playable video/audio file.`
7. job killed by timeout → `ERROR: the job exceeded YUEYING_JOB_TIMEOUT (7200 s) and was stopped. Try mode="transcript", a smaller model, or a shorter video.`
8. killed by refresh/shutdown → `ERROR: this job was cancelled (<reason>). Call watch_video again.`
9. default → `ERROR: processing failed (exit code N).`
Read tools: unknown video → `ERROR: no processed video matches "<x>". Call watch_video first, or use list_videos to find the video_id or folder.`; no frames → `ERROR: <title> has no keyframes (processed with mode=transcript, or audio-only). Run watch_video(video=..., refresh=true, mode="full") to extract them.`; no text → `No transcript: <reason>. Use get_frames to see the frames.`; out-of-range → `ERROR: start=<n> is beyond the last <kind> (<max>).`

### 6.4 get_transcript
Header `Transcript of <title> 05:10–09:42 (video is 12:30) — paragraphs, source: platform subtitles zh` then lines; trailing `TRUNCATED — next_start="09:42"` when cut.
### 6.5 search_transcript
`3 hits for "docker compose" in <title>:` then `- [03:15] …snippet… (keyframe #24, grid 3)`; zero → `No hits for "…" in <title>. Try a synonym, or read a range with get_transcript.`
### 6.6 get_frames / get_frame_at (content blocks, in order)
`grids 1–2 of 7 · <folder>` → per image: `grid_01.jpg · frames #1–#9 · 00:02–00:55 · <abs path>` then `Image(data, format="jpeg")`; trailing `Next: get_frames(video=..., start=3)` when more remain. get_frame_at: `frame at 03:15 (extracted exactly from the source) · <abs path>` or `keyframe #24 at 03:12 (nearest cached keyframe; requested 03:15) · <abs path>`, then `Spoken around then:` + 3 paragraphs, then the image.
### 6.7 list_videos
```
Processed videos in C:\Users\me\yueying_out (total 312 MB):
video_id  | title                 | duration | text                 | processed        | size   | folder
3f9a2c1e  | ...                   | 06:21    | subtitles zh         | 2026-09-08 21:10 | 11 MB  | C:\...\bili-BV1xx-3f9a2c1e
Running jobs: 7b12aa90 stage 3/4 extracting keyframes 21/63, elapsed 40 s
Delete a folder to free space; watch_video(refresh=true) reprocesses.
```

## 7. Image delivery rules
- Only `get_frames` and `get_frame_at` return images; `watch_video` never does (no preview image).
- Re-encode with Pillow: RGB, `thumbnail((max_width, max_width))`, JPEG quality 72 for grids (≈150 KB at 1280) and 80 for frames (≈100–120 KB at 960), `optimize=True`; originals on disk untouched.
- Caps: ≤3 images per call (default 2 grids / 1 frame) → worst case ≈ 3 × 165 KB = 495 KB raw ≈ 660 KB base64, under Claude Desktop's ~1 MB per-result cap. Test asserts every ImageContent < 220 KB.
- `list[str | Image]` return with the SDK emitting ordered TextContent/ImageContent; no structured content. Text block with the absolute path precedes every image (file-capable hosts read originals; hosts without vision still get useful text).
- Exact-moment frames: local file still present with matching size+mtime, or `<entry>/_download/source.*` when `YUEYING_KEEP_SOURCE=1` → `frames.extract_one(src, t, entry/"frames"/"extra"/f"at_{slug_time(t)}.jpg", label=f"@{fmt_time(t)}")` under `_FF` semaphore (cached on disk for repeats). Else nearest keyframe (±1.5 s counts as exact).

## 8. Cache / output directory policy
- Root: `$YUEYING_OUT_DIR` (absolute, `~` expanded) else `~/yueying_out` — visible, short, ASCII. CLI default `./yueying_out/<slug>` unchanged. `output_dir` per call overrides for one video.
- Entry: `<root>/<slug40>-<key8>/` with exactly the CLI's files + `.job` while running + `frames/extra/` for on-demand frames + `_download/` only with `YUEYING_KEEP_SOURCE=1`.
- Hit rule: section 4.5 step 4. Options other than mode do not invalidate (the DONE header shows what produced the entry; `refresh=true` is the escape hatch). Local-file edits change size/mtime → new key → new entry.
- Cleanup: none automatic in 0.2.0. `list_videos` shows sizes; README budget "≈25 MB per hour of video; 300–600 MB/h more with YUEYING_KEEP_SOURCE=1; delete folders to free space". Whisper weights stay in the HF cache (`HF_HOME` passed through).
- Env vars (one README table, mirrored later in .mcpb user_config): `YUEYING_OUT_DIR`, `YUEYING_MODEL` (default for `model`, default auto), `YUEYING_DEVICE` (auto|cuda|cpu), `YUEYING_LANG` (report.md language, default en for MCP), `YUEYING_COOKIES_FROM_BROWSER`, `YUEYING_KEEP_SOURCE`, `YUEYING_MAX_JOBS`, `YUEYING_JOB_TIMEOUT`, `HF_HOME`, `HF_ENDPOINT`, `PYTHONUTF8=1`.

## 9. Files

CREATE: `src/yueying/mcp_server.py`, `src/yueying/store.py`, `src/yueying/query.py`, `src/yueying/models.py`, `src/yueying/__main__.py` (`from .cli import main; sys.exit(main())`), `tests/conftest.py`, `tests/test_store.py`, `tests/test_query.py`, `tests/test_progress_parse.py`, `tests/fixtures/cli_log_sample.txt`, `tests/test_mcp.py`, `tests/test_cli_compat.py`, `tests/test_version_sync.py`, `server.json`, `glama.json`, `llms-install.md`, `.mcp.json`, `plugin.json`, `docs/privacy.md`, `docs/icon-512.png`, `docs/logo-400.png`, `CHANGELOG.md`, `SECURITY.md`, `.github/workflows/ci.yml`.

MODIFY: `pyproject.toml`, `src/yueying/__init__.py`, `src/yueying/cli.py`, `src/yueying/asr.py`, `src/yueying/ffm.py`, `src/yueying/frames.py`, `src/yueying/download.py`, `src/yueying/report.py`, `src/yueying/skill/SKILL.md`, `README.md`, `install.cmd`, `.github/workflows/publish.yml`, `.gitignore` (add `yueying_out/`, `*.mcpb`).

Deferred to 0.2.1/0.3 (explicitly NOT now): alias package `yueying-mcp`, `--install <client>`, `.mcpb` bundle + Claude Desktop directory form + Smithery, Dockerfile/Glama build, LobeHub `lhm.plugin.json`, prune/forget_video, lock files beyond `.job`, ResourceLinks/prompts, English CLI default, `--all` over MCP.

## 10. README.md structure (English first, Chinese section last)
Line 1: `<!-- mcp-name: io.github.vsh5dvsch7-png/yueying -->`
1. `# yueying — let AI watch videos` · one-liner · badges: PyPI version, PyPI downloads, MIT, Python 3.10+, "Add to Cursor" (`cursor://anysphere.cursor-deeplink/mcp/install?name=yueying&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJ5dWV5aW5nIiwibWNwIl0sImVudiI6eyJQWVRIT05VVEY4IjoiMSJ9fQ==`), "Install in VS Code" (`vscode:mcp/install?%7B%22name%22%3A%22yueying%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22yueying%22%2C%22mcp%22%5D%2C%22env%22%3A%7B%22PYTHONUTF8%22%3A%221%22%7D%7D`) · link `中文说明 ↓`. "Yueying (阅影) means 'read video'."
2. What you get: `docs/demo-grid.jpg` with caption, 4-line transcript sample, the file list.
3. Why yueying (5 bullets): captions first / local Whisper only when needed (GPU auto, CPU fallback, `small` on CPU); ffmpeg bundled — works on Windows 11 out of the box; token-efficient (scene keyframes → 3x3 sheets with burned-in timestamps, one transcript); Bilibili/Douyin/Xiaohongshu AND YouTube/TikTok; zero API keys, zero telemetry. Benchmark line: "6-minute Bilibili video → report in ~90 s on an RTX 5060 laptop; on CPU with model=small expect ~1–2 min per 10 min of speech."
4. Quick start (3 steps): install uv (`winget install astral-sh.uv` / `brew install uv` / curl one-liner) → `uvx yueying mcp --setup` → add to your client. Then per-client blocks:
   - Claude Desktop (`%APPDATA%\Claude\claude_desktop_config.json` / `~/Library/Application Support/Claude/claude_desktop_config.json`; fully quit and reopen; logs at `%APPDATA%\Claude\logs\mcp-server-yueying.log`; Windows note: use the absolute `uvx.exe` path, e.g. `C:\\Users\\<you>\\.local\\bin\\uvx.exe`):
     ```json
     { "mcpServers": { "yueying": { "command": "uvx", "args": ["yueying", "mcp"], "env": { "PYTHONUTF8": "1" } } } }
     ```
   - Claude Code: `claude mcp add --transport stdio --scope user --env PYTHONUTF8=1 yueying -- uvx yueying mcp`; project `.mcp.json` (shipped in repo) with `"timeout": 1800000`.
   - Cursor: `~/.cursor/mcp.json` same JSON + the badge.
   - Cline: `cline_mcp_settings.json` with `"type": "stdio"`, `"timeout": 1800`, `"autoApprove": ["get_transcript","search_transcript","get_frames","get_frame_at","list_videos"]`.
   - Windsurf: `~/.codeium/windsurf/mcp_config.json` same JSON.
   - VS Code Copilot: `.vscode/mcp.json` with root key `servers` and `"type": "stdio"` + badge.
   - Without uv (pip/pipx): `pip install yueying` (+ `"yueying[cuda]"`), command = absolute path printed by `yueying mcp --setup` (`yueying-mcp.exe` on Windows); Windows one-click `install.cmd`.
   - GPU: `uvx --from "yueying[cuda]" yueying mcp` / `pip install "yueying[cuda]"`; automatic CPU fallback.
5. Tools table (6 rows: name, when the agent uses it, what it returns, limits) + RUNNING/re-call rule + wait_seconds per host + first-run model download.
6. Where files go: root, entry layout, env-var table, disk budget, how to delete, `refresh=true`.
7. Supported sources: local formats; yt-dlp sites; Bilibili cookies (`cookies_from_browser`, Chrome must be closed on Windows, 480p without login); not for live streams; short links (b23.tv) are processed but not deduplicated.
8. Also a CLI and an agent skill: `yueying video.mp4`, flag table (translated), `yueying --install-skill`.
9. Comparison table (dated): claude-video (skill only, cloud Whisper fallback), claude-real-video (manual ffmpeg, ASR-first), mcp-video-analyzer (Node, whisper separate) vs yueying — rows: MCP server, offline ASR, GPU auto, ffmpeg bundled, Windows tested, contact sheets, burned timestamps, Bilibili.
10. Privacy Policy: no data collection, no telemetry; processing local; network only to the video platform (yt-dlp, for the URL you give) and Hugging Face (model download once); outputs stored in your folder until you delete them; transcript/frames are sent only to the model your MCP client uses; contact via GitHub issues. Link `docs/privacy.md`.
11. Troubleshooting/FAQ: "No result received" in Claude Desktop → keep wait_seconds 45, run `--setup`; "spawn uvx ENOENT" → absolute path; console flashes; mojibake → PYTHONUTF8; Bilibili 412; YouTube "Sign in to confirm"; slow CPU → model small; mainland-China HF mirror (`HF_ENDPOINT`).
12. How it works (pipeline diagram + module table incl. mcp_server/store/query).
13. Roadmap (0.2.1: .mcpb one-click for Claude Desktop, Smithery; 0.3: forget/prune, English CLI default, `--all` over MCP) · Credits (faster-whisper, yt-dlp, imageio-ffmpeg, Pillow, video-vision-mcp, video-analyzer-skill) · License MIT.
14. `## 中文说明` — condensed: 一句话介绍、安装（uv 一行 / install.cmd / pip）、Claude Desktop / Claude Code / Cursor 配置块、六个工具一览、输出目录与环境变量、B站 cookie、国内镜像、命令行用法；注明完整文档见上方英文部分.

Listing copy rule (registry/Desktop review): lead with local files; describe URLs as "videos you are entitled to process, fetched via yt-dlp at ≤720p, deleted after processing by default"; never headline "download YouTube videos".

## 11. SKILL.md relationship
`src/yueying/skill/SKILL.md` becomes English-first; frontmatter `description` keeps BOTH English and the current Chinese trigger words. Step 0: "If MCP tools named watch_video / get_transcript / get_frames from a server called yueying are available, use them and skip the CLI." Otherwise the existing CLI flow (run `yueying "<path or url>" --out "<absolute dir>"`, Read report.md, grids, frames), now recommending an absolute `--out` and `--ui-lang en` when the user writes in English. `install_skill()` unchanged (still rewrites the command to the absolute exe path). The MCP `instructions` and SKILL.md share the same guidance sentences (timestamps, trust on-screen text, grids before frames).

## 12. Test plan (see structured field for the list)
Fast suite (no ASR, no network) runs in CI on ubuntu/windows/macos × Python 3.10/3.13 (`.github/workflows/ci.yml`, `pytest -q -m "not asr and not net"`). ASR test opt-in with `-m asr` (uses `test_zh.mp4`; large-v3-turbo is cached on the dev box). Inspector: `npx @modelcontextprotocol/inspector -e PYTHONUTF8=1 C:/Users/Administrator/Desktop/yueying/.venv/Scripts/python.exe -m yueying.mcp_server`.

## 13. Release mechanics
`publish.yml`: keep the PyPI trusted-publishing job; add job `registry` (needs: pypi; permissions id-token: write) that sleeps 90 s, downloads `mcp-publisher` (linux amd64 tarball), runs `./mcp-publisher login github-oidc` and `./mcp-publisher publish` with the repo's `server.json`. `tests/test_version_sync.py` asserts `server.json` version == `__version__` == pyproject version so a tag cannot ship mismatched. Versions are immutable on the registry: a botched 0.2.0 becomes 0.2.1. Manual fallback: `mcp-publisher login github` + `mcp-publisher publish` on the owner's machine.

`server.json` (repo root):
```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.vsh5dvsch7-png/yueying",
  "title": "Yueying – Let AI watch videos",
  "description": "Local videos or YouTube/Bilibili URLs -> timestamped transcript, keyframes and contact sheets. Fully offline, no API key.",
  "repository": { "url": "https://github.com/vsh5dvsch7-png/yueying", "source": "github" },
  "websiteUrl": "https://github.com/vsh5dvsch7-png/yueying",
  "version": "0.2.0",
  "packages": [{
    "registryType": "pypi", "registryBaseUrl": "https://pypi.org", "identifier": "yueying", "version": "0.2.0",
    "runtimeHint": "uvx", "transport": { "type": "stdio" },
    "packageArguments": [{ "type": "positional", "value": "mcp", "description": "Run the MCP server" }],
    "environmentVariables": [
      { "name": "PYTHONUTF8", "description": "Set to 1 (UTF-8 I/O on Windows)", "default": "1", "isRequired": false },
      { "name": "YUEYING_OUT_DIR", "description": "Folder for transcripts/keyframes (default ~/yueying_out)", "isRequired": false },
      { "name": "YUEYING_MODEL", "description": "Default Whisper model: auto|tiny|base|small|medium|large-v3|large-v3-turbo", "default": "auto", "isRequired": false },
      { "name": "YUEYING_DEVICE", "description": "auto | cuda | cpu", "default": "auto", "isRequired": false }
    ]
  }]
}
```

## 14. Decisions on every unresolved question
1. **Launch command**: `uvx yueying mcp` (subcommand). No alias package in 0.2.0; reconsider after 3 months of download data.
2. **Preview image in DONE**: no. Images only from get_frames/get_frame_at.
3. **Keep source video**: not by default; `YUEYING_KEEP_SOURCE=1` opt-in. Exact-moment frames work for local files always, for URLs only with opt-in.
4. **Cache root**: `~/yueying_out`, override `YUEYING_OUT_DIR`, per-call `output_dir`. Visible, short, no clash with a repo clone named `yueying`.
5. **model=auto**: resolved in the child (asr.py) after `pick_device`: cuda → large-v3-turbo, cpu → small; if GPU trial fails and request was auto → small on CPU int8. Apple Silicon/CPU-only Windows → small. Server never probes the GPU; the model-cache check covers both candidates.
6. **Title before job**: no pre-flight `extract_info` in the server. Folder named from the URL id (`yt-<id>`, `bili-<BV>`), title captured from the child's `《title》` log line for RUNNING and from manifest.json afterwards. No rename.
7. **Job contract**: re-call `watch_video` with the same `video`; no job_id/status tool. Different options while in flight are ignored and stated; `refresh=true` restarts.
8. **Cross-process**: `.job` marker per entry (pid, started, stale 3 h/dead pid); second process polls for manifest.json and reports "another yueying process is processing this video". Semaphore is per process.
9. **Desktop Windows spawn timeout**: README step 1 `uvx yueying mcp --setup` (warms the uvx cache); Windows section leads with absolute `uvx.exe` path or `install.cmd`'s `yueying-mcp.exe`. Verified manually on this box; fresh-profile test is in the manual checklist.
10. **Progress coupling**: regex parsing of the existing Chinese log lines, marked load-bearing, with a fixture test. No `--progress-json` in 0.2.0.
11. **Report language**: child runs with `--ui-lang en` (YUEYING_LANG); SKILL.md tells the CLI flow to pass `--ui-lang en` for English users; CLI default flips to en in 0.3 with a changelog note.
12. **Timeouts shipped**: ffmpeg per-call, yt-dlp socket, job hard timeout, Windows Job Object (best effort) + taskkill tree + lifespan kill. All in 0.2.0.
13. **CLI knobs over MCP**: exposed: mode, language, model, frame_interval_seconds, cookies_from_browser, output_dir, refresh. Not exposed: `--force-asr`, `--scene`, `--no-dedupe`, `--frames`, `--all`, `--keep` (env only).
14. **Cache validity**: manifest present + no live marker + mode coverage (section 4.5). Model/language/interval changes do not invalidate; `refresh=true` does. File edits invalidate via size+mtime key.
15. **ERROR text**: English hint first (9 classes), then `log:` with the last 10 raw lines.
16. **Timeout guidance**: both — tool descriptions/instructions state per-host wait_seconds, and `.mcp.json` (repo) + snippets in README/llms-install.md carry `timeout` values.
17. **Release**: OIDC registry job added to publish.yml now, manual `mcp-publisher login github` as fallback.
18. **Hung job**: acceptable until refresh/timeout; no cancel tool.
19. **Cline/Windsurf images**: test once manually; defaults stay (count=2) — text with paths is always present.
20. **Paragraph alignment**: get_transcript/search/overview all use `report.paragraphs()` defaults → identical timestamps to transcript.txt.
21. **Server-side ffmpeg**: `_FF = Semaphore(2)`, CREATE_NO_WINDOW via `ffm.run`.
22. **Legal copy & .mcpb**: wording fixed in section 10; .mcpb + Desktop form + Smithery in 0.2.1 (needs macOS test the dev box cannot do — use a GitHub Actions macos runner for the automated part, a borrowed Mac for the double-click test).
23. **macOS/Linux verification**: CI matrix (ubuntu/windows/macos × 3.10/3.13) running the fast suite incl. the stdio round-trip on test.mp4 with mode=frames.
24. **Housekeeping**: bump 0.1.2 → 0.2.0, requires-python ≥3.10, refresh egg-info, English summary, marker on README line 1 — all before the first 0.2.0 upload.

## 15. Implementation order
See structured `implementation_steps`. Total ≈ 3.5 days: library + store/query + tests (day 1), server + stdio tests + Inspector + Claude Code/Desktop on this box (day 2), README/SKILL/llms-install/install.cmd/server.json/CI/CHANGELOG + TestPyPI/uvx smoke (day 3), release + listings (half day).


---

## Structured summary (auto-extracted)

- package_name: yueying
- server_name: yueying (registry id: io.github.vsh5dvsch7-png/yueying; launch: uvx yueying mcp)
- entry_point: yueying.mcp_server:main — reached via `yueying mcp` (subcommand of the `yueying` console script, so `uvx yueying mcp` works), the `yueying-mcp` console script, and `python -m yueying.mcp_server`
- new_version: 0.2.0
- python_requires: >=3.10

### Tools

#### watch_video
- signature: `async def watch_video(video: str, ctx: Context, mode: Literal['full','transcript','frames'] = 'full', language: Literal['auto','zh','en','ja','ko','fr','de','es','ru','pt','it','yue'] = 'auto', model: Literal['auto','tiny','base','small','medium','large-v3','large-v3-turbo'] = 'auto', frame_interval_seconds: float | None = None (0.5..120), cookies_from_browser: Literal['chrome','edge','firefox','brave','chromium','safari'] | None = None, output_dir: str | None = None, refresh: bool = False, wait_seconds: int = 45 (0..1500), max_chars: int = 12000 (2000..100000)) -> str  # annotations: title='Watch a video', read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True`
- returns: Plain text. DONE block (video_id, cached yes/no + seconds, Title, Source, Uploader, Duration·WxH, Text source in English, Folder, Files, Chapters, per-contact-sheet frame/time ranges, transcript as [mm:ss] paragraphs truncated on a paragraph boundary with 'TRUNCATED at mm:ss — continue with get_transcript(...)', Next hint); or RUNNING line (video_id, stage n/4 + English message + percent, elapsed, ETA when model cached, re-call instruction); or ERROR with one of 9 English hints followed by 'log:' + last 10 raw pipeline lines.
- description_for_llm: Turn a video into a timestamped transcript plus a keyframe overview so you can summarize it, answer questions about it, extract steps, commands or code, or write notes. `video` is an absolute path to a local video/audio file or a YouTube / Bilibili / Douyin / Xiaohongshu / TikTok / Vimeo URL. Fully offline: platform subtitles are used when they exist, otherwise local Whisper speech recognition (GPU when available, CPU otherwise); no API key. Results are cached per video, so calling again with the same video is instant. Typical time: 5–60 s for short subtitled videos, a few minutes for long videos without subtitles; the very first speech recognition downloads a model once (~480 MB on CPU, ~1.6 GB on GPU). If the reply starts with RUNNING, call watch_video again with the same `video` — it resumes waiting for the same job; do not change options and do not start other videos meanwhile. The reply is an English overview: metadata, chapters, the list of contact sheets (see them with get_frames), file paths, and the transcript with [mm:ss] timestamps (truncated at max_chars with a start time for get_transcript). Cite timestamps like (03:15). Not for live streams or images.

#### get_transcript
- signature: `def get_transcript(video: str, start: str = '0', end: str | None = None, format: Literal['paragraphs','segments','srt'] = 'paragraphs', max_chars: int = 8000 (1000..100000)) -> str  # annotations: title='Read transcript by time range', read_only_hint=True, idempotent_hint=True`
- returns: Plain text: header 'Transcript of <title> mm:ss–mm:ss (video is h:mm:ss) — <format>, source: <text source>' then '[mm:ss] text' paragraphs / '[mm:ss-mm:ss] text' segments / SRT blocks; last line 'TRUNCATED — next_start="mm:ss"' when cut; 'No transcript: <reason>. Use get_frames to see the frames.' when the video has no text; ERROR line for unknown video.
- description_for_llm: Read part of an already-watched video's transcript with [mm:ss] timestamps. Use when the watch_video overview was truncated, when the user asks about a specific time range, or to export subtitles (format='srt'). Returns at most max_chars; when truncated the last line gives next_start so you can continue from there. Paragraph format is the cheapest.

#### search_transcript
- signature: `def search_transcript(video: str, query: str, context_seconds: float = 15 (0..120), limit: int = 10 (1..50)) -> str  # annotations: title='Search transcript', read_only_hint=True, idempotent_hint=True`
- returns: Plain text: 'N hits for "<query>" in <title>:' then one line per hit '- [mm:ss] …context paragraphs… (keyframe #n, grid g)', ranked by number of terms matched then time; 'No hits for … Try a synonym, or read a range with get_transcript.' when empty.
- description_for_llm: Find where something is said in an already-watched video. Separate alternative words with spaces (any term matches; hits mentioning more terms rank first); case-insensitive. Each hit shows the time, the surrounding sentences, and the nearest keyframe number and contact-sheet number so you can follow up with get_frame_at or get_transcript. Use this instead of paging the whole transcript when the user asks 'when does he mention X' or 'find the part about Y'.

#### get_frames
- signature: `def get_frames(video: str, kind: Literal['grids','frames'] = 'grids', start: int = 1 (>=1), count: int = 2 (1..3), max_width: int = 1280 (320..1920)) -> list[str | Image]  # annotations: title='Show contact sheets / keyframes', read_only_hint=True, idempotent_hint=True`
- returns: Ordered content blocks: text 'grids a–b of N · <folder>', then per image a text line ('grid_03.jpg · frames #19–#27 · 02:10–03:05 · <absolute path>' or 'frame #23 · 02:41 · <absolute path>') followed by an ImageContent JPEG re-encoded at max_width, quality 72 (~150 KB per sheet); trailing 'Next: get_frames(video=..., start=k)' when more remain. Single ERROR text block when the video is unknown, has no keyframes, or start is out of range.
- description_for_llm: See what is on screen in an already-watched video. Returns contact-sheet images (3x3 keyframes in time order, every tile labelled '#number mm:ss' bottom-left) or individual keyframes. Read the contact sheets first to get the visual storyline, then request single frames only when you need to read code, slides or UI text. At most 3 images per call (default 2; keep 2 or fewer in Claude Desktop), downscaled to max_width; page with start/count. Every image is preceded by its absolute file path so hosts that can read files may open the full-size original instead.

#### get_frame_at
- signature: `def get_frame_at(video: str, time: str, max_width: int = 960 (320..1920)) -> list[str | Image]  # annotations: title='Look at one moment', read_only_hint=True, idempotent_hint=True`
- returns: Content blocks: caption 'frame at mm:ss (extracted exactly from the source) · <abs path>' or 'keyframe #n at mm:ss (nearest cached keyframe; requested mm:ss) · <abs path>', then 'Spoken around then:' with up to 3 transcript paragraphs, then one ImageContent JPEG (quality 80, ~100 KB at 960 px). ERROR text for unknown video, no frames, or time beyond duration.
- description_for_llm: Look closely at one moment of an already-watched video, e.g. to read code, a slide, a chart or a UI. Give the time as seconds, mm:ss or h:mm:ss. For local files that still exist the exact frame at that time is extracted from the video; otherwise the nearest cached keyframe is returned and the caption says so. Also returns the transcript paragraphs spoken around that time. Returns one image (~100 KB at 960 px).

#### list_videos
- signature: `def list_videos(limit: int = 20 (1..200)) -> str  # annotations: title='List processed videos', read_only_hint=True, idempotent_hint=True`
- returns: Plain-text table (newest first): video_id | title | duration | text source | processed date | size | absolute folder; then 'Running jobs: <video_id> stage n/4 <message>, elapsed Ns'; footer with the output root, total size and how to free space. 'No videos processed yet. Output root: <path>' when empty.
- description_for_llm: List videos already processed by yueying on this machine (newest first) and jobs currently running, with video_id, title, duration, text source, date, folder and size. Use when the user refers to a video watched earlier, to get a video_id for the other tools, or to see how much disk space results use. Instant and read-only.

### files_to_create
- C:/Users/Administrator/Desktop/yueying/src/yueying/mcp_server.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/store.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/query.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/models.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/__main__.py
- C:/Users/Administrator/Desktop/yueying/tests/conftest.py
- C:/Users/Administrator/Desktop/yueying/tests/test_store.py
- C:/Users/Administrator/Desktop/yueying/tests/test_query.py
- C:/Users/Administrator/Desktop/yueying/tests/test_progress_parse.py
- C:/Users/Administrator/Desktop/yueying/tests/fixtures/cli_log_sample.txt
- C:/Users/Administrator/Desktop/yueying/tests/test_mcp.py
- C:/Users/Administrator/Desktop/yueying/tests/test_cli_compat.py
- C:/Users/Administrator/Desktop/yueying/tests/test_version_sync.py
- C:/Users/Administrator/Desktop/yueying/server.json
- C:/Users/Administrator/Desktop/yueying/glama.json
- C:/Users/Administrator/Desktop/yueying/llms-install.md
- C:/Users/Administrator/Desktop/yueying/.mcp.json
- C:/Users/Administrator/Desktop/yueying/plugin.json
- C:/Users/Administrator/Desktop/yueying/docs/privacy.md
- C:/Users/Administrator/Desktop/yueying/docs/icon-512.png
- C:/Users/Administrator/Desktop/yueying/docs/logo-400.png
- C:/Users/Administrator/Desktop/yueying/CHANGELOG.md
- C:/Users/Administrator/Desktop/yueying/SECURITY.md
- C:/Users/Administrator/Desktop/yueying/.github/workflows/ci.yml

### files_to_modify
- C:/Users/Administrator/Desktop/yueying/pyproject.toml
- C:/Users/Administrator/Desktop/yueying/src/yueying/__init__.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/cli.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/asr.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/ffm.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/frames.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/download.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/report.py
- C:/Users/Administrator/Desktop/yueying/src/yueying/skill/SKILL.md
- C:/Users/Administrator/Desktop/yueying/README.md
- C:/Users/Administrator/Desktop/yueying/install.cmd
- C:/Users/Administrator/Desktop/yueying/.github/workflows/publish.yml
- C:/Users/Administrator/Desktop/yueying/.gitignore

### implementation_steps
- 1. Metadata: pyproject.toml per spec section 2 (version 0.2.0, requires-python >=3.10, mcp>=2.2,<3 core dep, dev extra, yueying-mcp script, English description/keywords/classifiers/urls); __init__.py version; `pip install -e ".[dev]"` in .venv to refresh stale egg-info; add tests/test_version_sync.py.
- 2. Library hardening (no behaviour change): ffm.py (lru_cache ffmpeg_exe, run(timeout=), stdin=DEVNULL, CREATE_NO_WINDOW, MediaError); frames.py extract_one() factored out and used by extract(); download.py yt-dlp logger + socket_timeout=30; new models.py (MODEL_SIZES, model_repo); asr.py resolve_model('auto'), auto->small fallback on GPU failure, prepare_model(), info['requested_model'].
- 3. report.py: _T zh/en label table, describe_text_source(text_source, ui_lang), write_all(..., ui_lang='zh', options=None) with additive manifest keys (schema 2, yueying_version, created_at, ui_lang, options, out_dir), load_manifest(folder) with path rebasing. cli.py: `mcp` subcommand dispatch first line of main(), --ui-lang, --model auto accepted, try/finally cleanup of audio.wav/_download, options passed to write_all, --json line filters new keys, LOAD-BEARING comment. Run tests/test_cli_compat.py: `yueying test-media/test.mp4 --no-asr --out <tmp>` output (report.md, transcript.txt/srt, --json line) byte-identical to a pre-change run captured into tests/fixtures.
- 4. store.py (out_root, canonical_url, source_key, entry_name/entry_dir, resolve, list_entries, .job marker helpers, parse_time) + tests/test_store.py; query.py (transcript_text, search, nearest_frame, grid_no_for_frame, grid_ranges, paragraphs_around, overview, shrink) + tests/test_query.py against test-media/out_zh/manifest.json and test-media/out_demo/manifest.json.
- 5. mcp_server.py: env guards, lifespan stdout guard, MCPServer with INSTRUCTIONS, Job class (Popen child, reader thread, _parse_line regexes, model-cache check, ETA, kill tree via taskkill/killpg + best-effort win32job Job Object, watchdog timeout), watch_video algorithm (validate -> key/entry -> refresh -> cache hit -> foreign .job marker -> attach -> spawn -> long-poll with report_progress), renderers for DONE/RUNNING/ERROR + 9-class error classifier, the five read tools, main() with --check/--version/--setup/mcp.run(). Capture a real child log into tests/fixtures/cli_log_sample.txt and write tests/test_progress_parse.py.
- 6. tests/test_mcp.py: in-memory Client schema/annotation test, ERROR-on-missing-file test, stdio round-trip on test.mp4 (mode=frames) covering watch -> cache hit -> get_transcript -> search -> get_frames (image < 220 KB) -> get_frame_at exact extraction -> list_videos, and the 'no Failed to parse JSONRPC / no Traceback' assertions; asr-marked test on test_zh.mp4 with model=small (opt-in). Run `pytest -q`.
- 7. Manual host verification on this Windows box: MCP Inspector (tools/list, watch_video on test_zh.mp4, image rendering, progress notifications); `claude mcp add --scope user --env PYTHONUTF8=1 yueying -- C:/Users/Administrator/Desktop/yueying/.venv/Scripts/yueying-mcp.exe` then a real Bilibili URL through RUNNING -> DONE -> get_frames; Claude Desktop with the venv exe path: confirm no console flashes, wait_seconds=45 round trip, images render, stderr log at %APPDATA%\Claude\logs\mcp-server-yueying.log clean; cp936 console mojibake check; move HF cache aside once to see the 'first run downloads' RUNNING message.
- 8. Docs and packaging files: README.md (section 10 structure, marker on line 1, Cursor/VS Code badges with the exact base64/URL-encoded links), SKILL.md (English-first, MCP-first step 0, Chinese trigger words kept), llms-install.md, .mcp.json + plugin.json, server.json, glama.json, docs/privacy.md, CHANGELOG.md, SECURITY.md, docs/icon-512.png + docs/logo-400.png (derive from demo-grid or a simple 阅影 mark), install.cmd (write yueying-mcp.cmd, run `yueying mcp --setup`, print JSON with the absolute venv exe path), .gitignore, .github/workflows/ci.yml (ubuntu/windows/macos × 3.10/3.13, `pytest -q -m "not asr and not net"`), publish.yml registry OIDC job.
- 9. Pre-release: install uv on the dev box (`winget install astral-sh.uv`), `python -m build`, upload to TestPyPI, run `uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ yueying mcp --check` and `--setup` cold; fix anything the cold uvx path reveals; commit.
- 10. Release 0.2.0 (owner): GitHub Release v0.2.0 -> PyPI via trusted publishing -> registry via OIDC job (fallback manual mcp-publisher); verify `curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.vsh5dvsch7-png/yueying"`; then run the listing checklist.

### test_plan
- tests/conftest.py: fixtures for TEST_MP4=C:/Users/Administrator/Desktop/yueying/test-media/test.mp4, TEST_ZH=C:/Users/Administrator/Desktop/yueying/test-media/test_zh.mp4, REF_ZH=test-media/out_zh/manifest.json, REF_DEMO=test-media/out_demo/manifest.json; tmp output root via monkeypatch YUEYING_OUT_DIR; anyio_backend='asyncio'.
- tests/test_store.py: canonical_url cases (youtu.be/ID, youtube shorts/live/watch with t=&feature=&si= -> same canonical; bilibili BV with spm_id_from/vd_source stripped, p=1 dropped, p=2 kept, av id; generic URL utm_* stripped and params sorted; b23.tv left as-is); source_key changes when a copied file's mtime changes and is stable across calls; entry_name slug rules and 40-char truncation with CJK; resolve() by 8-hex id, by folder, by path, by URL; parse_time('185', '185.5', '3:05', '1:02:03'); .job marker fresh/stale/dead-pid.
- tests/test_query.py (pure, uses REF_ZH/REF_DEMO): transcript_text paging returns paragraph-aligned text identical to transcript.txt lines, next_start correctness, segments and srt formats; search ranks multi-term hits first and reports keyframe/grid numbers; nearest_frame/grid_no_for_frame; overview() truncation on a paragraph boundary and the 'TRUNCATED at' line; shrink() output < 220 KB at 1280 for grid_01.jpg.
- tests/test_progress_parse.py: feed tests/fixtures/cli_log_sample.txt (captured from `python -m yueying.cli test_zh.mp4 --json`) line by line into mcp_server._parse_line and assert the stage/pct/message sequence, title capture, and manifest parsed from the trailing JSON line; also assert the error classifier maps sample tails (412 line, huggingface ConnectionError, 找不到文件, Invalid data found) to the expected English hints.
- tests/test_cli_compat.py: run `python -m yueying.cli test.mp4 --no-asr --out <tmp> --json` and compare report.md, transcript.txt, transcript.srt and the --json stdout line (paths normalised) to fixtures captured before the refactor; assert manifest.json on disk has schema==2 and the additive keys; run with `--ui-lang en` and assert English headings.
- tests/test_mcp.py::test_in_memory_schema: `async with Client(mcp, raise_exceptions=True)`: exactly 6 tools; watch_video input_schema required == ['video']; annotations (read_only_hint True on the five read tools, open_world_hint True on watch_video); watch_video('C:/nope.mp4') returns text starting with 'ERROR: file not found'; get_transcript('zzz') returns 'ERROR: no processed video matches'.
- tests/test_mcp.py::test_stdio_roundtrip (fast, no ASR): spawn `sys.executable -m yueying.mcp_server` via StdioServerParameters with env PYTHONUTF8=1, YUEYING_OUT_DIR=<tmp>, errlog to a file, read_timeout_seconds=600; watch_video(video=TEST_MP4, mode='frames', wait_seconds=300, progress_callback) -> text startswith 'DONE' and contains 'cached: no'; second identical call -> 'cached: yes' in < 2 s; get_transcript/search on the same id return the English 'No transcript' message (mode=frames on a TTS clip without subtitles); get_frames(count=1) -> exactly one ImageContent, mime image/jpeg, len(base64) < 300000; get_frame_at(time='00:05') caption contains 'extracted exactly'; list_videos lists the entry; progress callbacks were received; caplog has no 'Failed to parse JSONRPC message'; server stderr file has no 'Traceback'.
- tests/test_mcp.py::test_stdio_asr (marker asr, opt-in `pytest -m asr`): watch_video(video=TEST_ZH, model='small', language='zh', wait_seconds=900) -> DONE with 'local speech recognition' in the Text line and at least one '[00:' paragraph; get_transcript(start='0', max_chars=1000) returns text; search_transcript(query='桌面') returns >= 1 hit (matches the reference transcript in out_zh).
- tests/test_mcp.py::test_refresh_kills_running_job (fast): start watch_video on TEST_MP4 with wait_seconds=0 (returns RUNNING), immediately call with refresh=true and wait_seconds=120 -> DONE and only one entry folder exists; no orphan ffmpeg (check via psutil-free approach: job.proc.poll() is not None).
- Stdout-purity: run `python -m yueying.mcp_server < NUL > wire.txt 2> err.txt` for 3 s (or feed an initialize request) and assert every non-empty line of wire.txt parses as JSON (covered implicitly by the stdio round-trip; keep as a manual step in CHANGELOG checklist).
- MCP Inspector (manual, dev box): `npx @modelcontextprotocol/inspector -e PYTHONUTF8=1 C:/Users/Administrator/Desktop/yueying/.venv/Scripts/python.exe -m yueying.mcp_server` — verify tool descriptions/schemas render, progress notifications stream during watch_video on test_zh.mp4, images render inline for get_frames and get_frame_at; CLI mode: `npx @modelcontextprotocol/inspector --cli <python> -m yueying.mcp_server --method tools/list`.
- Real hosts (manual): Claude Code (`claude mcp add`, /mcp, a Bilibili URL end-to-end, `claude --debug` stderr clean); Cursor (Output > MCP); Claude Desktop on Windows via the venv exe path (no console flashes, wait_seconds=45 RUNNING -> re-call -> DONE, images visible, log file clean); once with the HF cache renamed to see the 'first run downloads' message; once on a fresh Windows user profile after publishing (`uvx yueying mcp --setup` then Desktop).
- CI: .github/workflows/ci.yml matrix ubuntu-latest/windows-latest/macos-latest × Python 3.10/3.13 running `pip install -e .[dev]` and `pytest -q -m "not asr and not net"` (this is the macOS/Linux verification the dev box cannot do).
- Publishing smoke: after TestPyPI upload, `uvx --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ yueying mcp --check` and `--setup`; after PyPI release, Inspector against exactly `uvx yueying mcp` as the README tells users.

### listing_checklist
- [prep, no account] Repo artefacts committed before tagging: README line-1 marker `<!-- mcp-name: io.github.vsh5dvsch7-png/yueying -->`, server.json, glama.json, llms-install.md, .mcp.json + plugin.json, docs/privacy.md, docs/icon-512.png, docs/logo-400.png, CHANGELOG.md, SECURITY.md, publish.yml with the OIDC registry job.
- [OWNER account: GitHub + PyPI] Publish yueying 0.2.0: create GitHub Release v0.2.0 -> publish.yml uploads to PyPI via the existing trusted publisher. Verify `pip index versions yueying` shows 0.2.0 and the PyPI page renders the English README.
- [OWNER account: GitHub] Official MCP Registry: automatic via the OIDC job in publish.yml (needs id-token: write; runs in the owner's repo). Fallback on the owner's machine: download mcp-publisher, `mcp-publisher login github` (device code), `mcp-publisher publish`. Verify: `curl "https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.vsh5dvsch7-png/yueying"`. This seeds PulseMCP (auto when their submission pause ends — nothing else to do) and Glama.
- [OWNER account: GitHub] Repo hygiene: set the English repo description, add topics (mcp, mcp-server, model-context-protocol, video, video-analysis, transcript, whisper, faster-whisper, keyframes, youtube, bilibili, offline, claude-desktop, claude-code, cursor, cline, windsurf, agent-skills), enable GitHub Pages for docs/ so https://vsh5dvsch7-png.github.io/yueying/privacy resolves.
- [OWNER account: GitHub] punkpeye/awesome-mcp-servers PR under '🎥 Multimedia Process', alphabetical, one line: `- [vsh5dvsch7-png/yueying](https://github.com/vsh5dvsch7-png/yueying) 🐍 🏠 🍎 🪟 🐧 - Let AI watch videos: timestamped transcripts (platform subtitles or local faster-whisper) and scene-change keyframe contact sheets from local files or YouTube/Bilibili URLs. Fully offline, no API keys.` (append 🤖🤖🤖 to the PR title if agent-authored). Optional same PR to appcypher/awesome-mcp-servers.
- [OWNER account: GitHub OAuth on glama.ai] Glama: listing appears automatically from the registry; owner claims it via GitHub OAuth, clicks Sync Server, checks the /score page; rewrite any tool description scoring under B. (Dockerfile-based sandbox build deferred to 0.2.1.)
- [OWNER account: GitHub] Cline MCP Marketplace: owner first lets Cline install the server from llms-install.md (cline_mcp_settings.json with timeout 1800 and autoApprove), then opens the '[Server Submission]' issue at github.com/cline/mcp-marketplace with the repo URL, docs/logo-400.png and both testing checkboxes ticked.
- [OWNER account: GitHub or Google on cursor.directory] cursor.directory: sign in, paste the repo URL (plugin.json/.mcp.json/skill auto-detected), submit; verify the 'Add to Cursor' badge in the current Cursor build before merging it into README.
- [OWNER account: mcp.so login] mcp.so: sign in, /submit with the repo URL, complete the draft (name, description, category Multimedia, install snippet), save. Fallback: comment the repo link on github.com/chatmcp/mcpso/issues/1.
- [OWNER, no login but personal email] mcpservers.org/submit: name 'yueying', category Productivity, short description = PyPI summary, repo URL, contact email = owner's own (prefilled text handed over; the agent never enters the owner's email).
- [OWNER, likely no login] mcpmarket.com/submit: paste the repo URL.
- [no account, nothing to do] PulseMCP: submissions paused; ingests from the official registry automatically. Re-check https://www.pulsemcp.com/submit in a few weeks.
- [0.2.1, OWNER accounts: Google Form + GitHub Release] Claude Desktop extension (.mcpb): build mcpb/ (manifest_version 0.4, server.type uv, thin pyproject depending on yueying==0.2.1, uv.lock, icon.png, privacy_policies URL, six tools with titles/annotations), `mcpb validate` + `mcpb pack`, double-click test on Windows and on a Mac, attach to Release v0.2.1, add as second `mcpb` package with fileSha256 in server.json and republish; then the owner submits the Google Form at https://clau.de/desktop-extention-submission with the .mcpb URL, README URL, privacy URL, test instructions (test-media sample + one public URL, no credentials).
- [0.2.1, OWNER account: Smithery OAuth] Smithery: `npm i -g smithery@latest`, `smithery auth login`, `smithery mcp publish ./yueying.mcpb -n <namespace>/yueying` (strip `tools` from a manifest copy if the 400 bug persists).
- [0.2.1, OWNER accounts] LobeHub (`lhm login`, `lhm github connect`, `lhm plugin publish` with a generated lhm.plugin.json) and an email to partnerships@github.com for github.com/mcp inclusion (low odds, zero cost). Skip Docker MCP Catalog (containers cannot use local files or the GPU).
- [OWNER] Launch posts with the grid screenshot and the benchmark line, pre-empting the known objections (token cost vs Gemini native video, privacy boundary 'frames go to your model vendor', keyframes miss motion): dev.to article, Show HN, r/ClaudeAI, r/cursor, X; Chinese side: Zhihu / V2EX / 小众软件 thread (existing posts.md drafts). Metrics to watch after 4 weeks: PyPI downloads, GitHub stars/issues by locale, Glama score, Cline marketplace installs.

### open_decisions_for_owner
- English product name / tagline. Recommended default: keep the package and server name `yueying`, H1 'yueying — let AI watch videos', explain 阅影 once; do NOT register `video-eyes-mcp` or `yueying-mcp` alias packages until 3 months of download data exist.
- GitHub identity: the handle `vsh5dvsch7-png` appears in the registry id and every directory. Recommended default: keep it for 0.2.0 (renaming changes the registry namespace); consider moving to an org like `yueying-ai` before 0.3 if overseas traction appears (GitHub redirects old URLs).
- Author display name in pyproject/`authors`, SECURITY.md and the future .mcpb manifest (currently the GitHub handle). Recommended default: keep the handle now; add a real name/pseudonym only if the owner wants one publicly.
- Privacy-policy contact: docs/privacy.md needs a contact line. Recommended default: 'GitHub issues at https://github.com/vsh5dvsch7-png/yueying/issues' (no personal email published).
- Default output root name: `~/yueying_out` (recommended; matches the CLI's `./yueying_out`, cannot clash with a repo clone named `yueying`) vs `~/yueying`.
- Keep downloaded ≤720p sources by default for exact-moment frames on URLs? Recommended default: no (YUEYING_KEEP_SOURCE=1 opt-in) — keeps disk at ~25 MB/h and avoids 'stores downloaded YouTube videos' optics in directory reviews.
- Flip the CLI's default report language from zh to en in 0.3 (MCP already uses en)? Recommended default: yes, announced in CHANGELOG 0.2.0 as a planned change; Chinese users keep `--ui-lang zh`.
- Whether to ship the .mcpb / Claude Desktop directory submission in 0.2.1 (needs a Mac for the double-click test and the owner's Google account for the form). Recommended default: yes, ~1.5 days, right after the 0.2.0 listings; borrow a Mac or accept the CI macos runner for the automated part.
- Which launch venues to post on and whether to use the existing posts.md drafts. Recommended default: dev.to + Show HN + r/ClaudeAI in English first (the goal is overseas signal), Chinese threads a week later.
- Whether `install.cmd` (Windows one-click without uv) stays supported now that `uvx yueying mcp` is canonical. Recommended default: keep it, updated to write yueying-mcp.cmd and print the JSON snippet; drop it in 0.3 if uv adoption makes it redundant.
