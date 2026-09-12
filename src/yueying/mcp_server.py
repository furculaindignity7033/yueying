"""MCP server (stdio): six tools that let an AI host watch videos through the yueying pipeline.

Design (docs/dev/SPEC-mcp-0.2.0.md, sections 4-8):

* All pipeline work runs in a CHILD process ``python -m yueying.cli <src> --out <entry> --json ...``;
  this process never imports yt_dlp / faster_whisper / ctranslate2 / torch and never print()s
  (stdout is the JSON-RPC wire).
* Results are cached per source under ``$YUEYING_OUT_DIR`` (default ``~/yueying_out``) in
  ``<slug40>-<key8>/``; ``watch_video`` is idempotent and long-polls a running job.
* Progress is parsed from the CLI's (load-bearing, Chinese) log lines - see ``_parse_line``.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("CT2_VERBOSE", "-3")
os.environ.setdefault("PYTHONUTF8", "1")

import collections  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Annotated, Literal  # noqa: E402

import anyio  # noqa: E402
from pydantic import Field  # noqa: E402
from mcp.server.mcpserver import Context, Image, MCPServer  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from yueying import __version__, models, query, store  # noqa: E402

logger = logging.getLogger("yueying.mcp")
_query = query      # tool parameters named `query` / `time` shadow these modules inside the tools
_time = time

_WIN = sys.platform == "win32"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if _WIN else 0

INSTRUCTIONS = (
    "yueying lets you watch videos offline. Workflow: call watch_video with an absolute file path or a "
    "video URL. If the reply starts with RUNNING, call watch_video again with the same video to keep "
    "waiting (it resumes the same job; never start a different video meanwhile). Answer from the "
    "transcript in the reply; use get_transcript for time ranges, search_transcript to find where "
    "something is said, get_frames to see the visual storyline (3x3 contact sheets, read these before "
    "single frames), and get_frame_at for one exact moment (code, slides, UI). Speech recognition "
    "mis-hears names, numbers and code — trust on-screen text from frames over the transcript. Quote "
    "the video with timestamps like (03:15). Images cost 1–2K tokens each; keep them few. Hosts that "
    "can read local files may open report.md, grid_NN.jpg and frames/*.jpg at the paths given; hosts "
    "that cannot (Claude Desktop) use get_frames / get_frame_at. In Claude Desktop and Cursor keep "
    "wait_seconds at the default (45); in Claude Code or Cline you may pass wait_seconds up to 1500 "
    "to get the result in one call."
)


# --------------------------------------------------------------------------- lifespan / server
@contextlib.asynccontextmanager
async def lifespan(server):
    # AFTER the SDK claimed fd 1 (verified-safe order): anything that still print()s goes to stderr.
    sys.stdout.flush()
    sys.stdout = sys.stderr
    with contextlib.suppress(Exception):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    with contextlib.suppress(OSError):
        store.out_root().mkdir(parents=True, exist_ok=True)
    logger.info("yueying %s MCP server ready; output root %s", __version__, store.out_root())
    try:
        yield
    finally:
        for job in list(JOBS.values()):
            if job.state in ("queued", "running"):
                job.kill("server shutdown")


mcp = MCPServer("yueying", title="Yueying – Let AI watch videos", version=__version__,
                instructions=INSTRUCTIONS, lifespan=lifespan)


# --------------------------------------------------------------------------- job runner
def _env_number(name: str, default, cast):
    """Numeric env var with a safe fallback: a malformed value must not crash the server at import."""
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        logger.warning("ignoring %s=%r (not a number); using %s", name, raw, default)
        return default


JOBS: dict[str, "Job"] = {}                 # key8 -> live Job (process-local; done jobs are dropped)
_DONE_ENTRIES: dict[str, Path] = {}          # key8 -> entry folder of jobs finished in this process
_JOBS_LOCK = threading.Lock()
_SLOT = threading.Semaphore(max(1, _env_number("YUEYING_MAX_JOBS", 1, int)))
_FF = threading.Semaphore(2)                 # server-side ffmpeg (get_frame_at)
JOB_TIMEOUT = max(60.0, _env_number("YUEYING_JOB_TIMEOUT", 7200.0, float))
POLL_INTERVAL = 1.5

_MESSAGES = {1: "downloading / reading file", 2: "looking for subtitles",
             3: "detecting scene changes", 4: "writing report"}
_RE_STAGE = re.compile(r"^\[([1-4])/4\]")
_RE_DOWNLOAD = re.compile(r"^\s+下载 (\d+)%")
_RE_TITLE = re.compile(r"^\s+《(.+?)》")
_RE_MODEL = re.compile(r"^\s+模型 ")
_RE_LANG = re.compile(r"^\s+检测语言")
_RE_ASR = re.compile(r"^\s+识别进度 (\d+)%")
_RE_SUBS = re.compile(r"^\s+用字幕")
_RE_FRAMES = re.compile(r"^\s+抽帧 (\d+)/(\d+)")
_RE_DONE = re.compile(r"^完成，用时")


def _parse_line(line: str) -> dict | None:
    """Progress information carried by one CLI log line, or None for a plain line.

    Keys (all optional): stage (1-4), pct (0.0-1.0), message (English), title, manifest (dict parsed
    from the trailing --json line), done (bool).  Pure: the Job applies the result.
    """
    line = line.rstrip("\r\n")
    if not line.strip():
        return None
    m = _RE_STAGE.match(line)
    if m:
        st = int(m.group(1))
        return {"stage": st, "pct": {1: 0.0, 2: 0.25, 3: 0.65, 4: 0.97}[st], "message": _MESSAGES[st]}
    m = _RE_DOWNLOAD.match(line)
    if m:
        n = min(100, int(m.group(1)))
        return {"pct": 0.25 * n / 100, "message": f"downloading video {n}%"}
    m = _RE_TITLE.match(line)
    if m:
        return {"title": m.group(1).strip()}
    if _RE_MODEL.match(line):
        return {"pct": 0.30, "message": "loading speech model"}
    if _RE_LANG.match(line):
        return {"pct": 0.35, "message": "speech recognition"}
    m = _RE_ASR.match(line)
    if m:
        n = min(100, int(m.group(1)))
        return {"pct": 0.35 + 0.30 * n / 100, "message": f"speech recognition {n}%"}
    if _RE_SUBS.match(line):
        return {"pct": 0.60, "message": "using platform subtitles"}
    m = _RE_FRAMES.match(line)
    if m:
        a, b = int(m.group(1)), max(1, int(m.group(2)))
        return {"pct": 0.70 + 0.25 * min(a, b) / b, "message": f"extracting keyframes {a}/{b}"}
    if _RE_DONE.match(line):
        return {"pct": 1.0, "done": True}
    if line.startswith("{"):
        try:
            data = json.loads(line)
        except ValueError:
            return None
        if isinstance(data, dict):
            return {"manifest": data}
        return None
    return None


def _hf_hub_cache() -> Path:
    env = os.environ.get("HF_HUB_CACHE")
    if env:
        return Path(os.path.expanduser(env))
    home = os.environ.get("HF_HOME")
    if home:
        return Path(os.path.expanduser(home)) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _repo_cached(repo: str) -> bool:
    snaps = _hf_hub_cache() / f"models--{repo.replace('/', '--')}" / "snapshots"
    try:
        return any(snaps.glob("*/model.bin"))
    except OSError:
        return False


def _model_candidates(model: str, device: str) -> list[str]:
    if model != "auto":
        return [model]
    if device == "cuda":
        return ["large-v3-turbo"]
    if device == "cpu":
        return ["small"]
    return ["large-v3-turbo", "small"]


def _model_cache_status(model: str, device: str) -> tuple[bool, str]:
    """(cached, size hint) for the model(s) a request can resolve to."""
    cands = _model_candidates(model, device)
    cached = any(_repo_cached(models.model_repo(c)) for c in cands)
    if model == "auto" and len(cands) > 1:
        hint = "~480 MB (CPU) / ~1.6 GB (GPU)"
    else:
        hint = "~" + models.MODEL_SIZES.get(cands[0], "1 GB")
    return cached, hint


def _kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if _WIN:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                           timeout=30)
        else:
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
    except Exception as e:  # pragma: no cover - best effort
        logger.warning("kill tree failed for pid %s: %s", proc.pid, e)
        with contextlib.suppress(Exception):
            proc.kill()
    with contextlib.suppress(Exception):
        proc.wait(10)


def _assign_job_object(proc: subprocess.Popen):
    """Windows: put the child in a Job Object that dies with this process (best effort)."""
    if not _WIN:
        return None
    try:
        import win32job
        h = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(h, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(h, win32job.JobObjectExtendedLimitInformation, info)
        win32job.AssignProcessToJobObject(h, int(proc._handle))  # type: ignore[attr-defined]
        return h
    except Exception as e:
        logger.debug("job object not assigned: %s", e)
        return None


def _child_env() -> dict:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_PROGRESS_BARS": "1",
                "TQDM_DISABLE": "1", "CT2_VERBOSE": "-3"})
    return env


def build_command(src: str, entry: Path, *, mode: str = "full", language: str = "auto", model: str = "auto",
                  frame_interval_seconds: float | None = None, cookies_from_browser: str | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "yueying.cli", src, "--out", str(entry), "--json",
           "--ui-lang", os.environ.get("YUEYING_LANG") or "en",
           "--model", model, "--device", os.environ.get("YUEYING_DEVICE") or "auto"]
    if language and language != "auto":
        cmd += ["--lang", language]
    if mode == "transcript":
        cmd.append("--no-frames")
    elif mode == "frames":
        cmd.append("--no-asr")
    if frame_interval_seconds:
        cmd += ["--interval", str(frame_interval_seconds)]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    if os.environ.get("YUEYING_KEEP_SOURCE") == "1":
        cmd.append("--keep")
    return cmd


class Job:
    """One child pipeline run.  Created => a daemon thread waits for a slot, spawns and follows it."""

    def __init__(self, key: str, source: str, entry: Path, cmd: list[str], *, model: str = "auto",
                 device: str = "auto", options: dict | None = None):
        self.key, self.source, self.entry, self.cmd = key, source, Path(entry), list(cmd)
        self.options = dict(options or {})
        self.state: str = "queued"           # queued | running | done | error
        self.stage: int = 0
        self.pct: float = 0.0
        self.message: str = "waiting for another video to finish"
        self.title: str | None = None
        self.lines: collections.deque = collections.deque(maxlen=200)
        self.manifest: dict | None = None
        self.created = time.monotonic()
        self.started: float | None = None
        self.finished: float | None = None
        self.proc: subprocess.Popen | None = None
        self.rc: int | None = None
        self.kill_reason: str | None = None
        self.model_cached, self.model_hint = _model_cache_status(model, device)
        self._job_handle = None
        self._lock = threading.Lock()
        self.done_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name=f"yueying-job-{key}", daemon=True)
        self.thread.start()

    # ---- lifecycle
    @property
    def alive(self) -> bool:
        return self.state in ("queued", "running")

    @property
    def elapsed(self) -> float:
        end = self.finished or time.monotonic()
        return end - self.created

    @property
    def took(self) -> float | None:
        if self.started is None:
            return None
        return (self.finished or time.monotonic()) - self.started

    def wait(self, timeout: float | None = None) -> bool:
        return self.done_event.wait(timeout)

    def kill(self, reason: str) -> None:
        with self._lock:
            if not self.alive:
                return
            self.kill_reason = reason
            proc = self.proc
        logger.info("job %s killed: %s", self.key, reason)
        _kill_tree(proc)
        if proc is None:                     # still queued: the thread will notice and bail out
            self._finish("error", reason)

    def _finish(self, state: str, message: str) -> None:
        with self._lock:
            if self.state in ("done", "error"):
                return
            self.state, self.message = state, message
            self.finished = time.monotonic()
        store.clear_job_marker(self.entry)
        self.done_event.set()

    def _apply(self, ev: dict) -> None:
        if "title" in ev:
            self.title = ev["title"]
        if "stage" in ev:
            self.stage = ev["stage"]
        if "pct" in ev:
            self.pct = max(self.pct, ev["pct"]) if not ev.get("done") else 1.0
        if "message" in ev:
            msg = ev["message"]
            if msg == "loading speech model" and not self.model_cached:
                msg += f" (first run downloads {self.model_hint}, this can take several minutes)"
            self.message = msg
        if "manifest" in ev:
            self.manifest = ev["manifest"]

    def _run(self) -> None:
        with _SLOT:
            if self.kill_reason:
                self._finish("error", self.kill_reason)
                return
            with self._lock:
                self.state, self.started = "running", time.monotonic()
                self.stage, self.message = 1, _MESSAGES[1]
            watchdog = None
            try:
                self.entry.mkdir(parents=True, exist_ok=True)
                kwargs: dict = {}
                if _WIN:
                    kwargs["creationflags"] = _NO_WINDOW
                else:
                    kwargs["start_new_session"] = True
                logger.info("job %s: %s", self.key, subprocess.list2cmdline(self.cmd))
                proc = subprocess.Popen(self.cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                        errors="replace", env=_child_env(), **kwargs)
                with self._lock:
                    if self.kill_reason:                 # killed while spawning
                        _kill_tree(proc)
                    self.proc = proc
                self._job_handle = _assign_job_object(proc)
                store.write_job_marker(self.entry, proc.pid)
                watchdog = threading.Timer(JOB_TIMEOUT, self.kill, args=("timeout",))
                watchdog.daemon = True
                watchdog.start()
                assert proc.stdout is not None
                for raw in proc.stdout:
                    line = raw.rstrip("\r\n")
                    self.lines.append(line)
                    ev = _parse_line(line)
                    if ev:
                        self._apply(ev)
                self.rc = proc.wait()
            except Exception as e:
                logger.error("job %s failed to run: %s", self.key, e)
                self.lines.append(f"{type(e).__name__}: {e}")
                self.rc = -1
            finally:
                if watchdog:
                    watchdog.cancel()
            ok = (self.rc == 0 and self.manifest is not None and (self.entry / "manifest.json").is_file()
                  and not self.kill_reason)
            if ok:
                try:
                    from yueying.report import load_manifest
                    self.manifest = load_manifest(str(self.entry))
                except Exception as e:
                    logger.error("job %s: manifest unreadable: %s", self.key, e)
                    self.lines.append(f"manifest unreadable: {e}")
                    ok = False
            store.clear_job_marker(self.entry)        # also covers a kill that raced the spawn
            self._job_handle = None                     # child is gone: release the Job Object handle
            if ok:
                self.pct = 1.0
                self._finish("done", "done")
                # Callers that are waiting keep their own reference; the process-wide dict only needs
                # the folder (for id/path resolution of custom output_dir entries), not the manifest.
                with _JOBS_LOCK:
                    _DONE_ENTRIES[self.key] = self.entry
                    if JOBS.get(self.key) is self:
                        JOBS.pop(self.key, None)
            else:
                self._finish("error", self.kill_reason or f"exit code {self.rc}")

    # ---- rendering helpers
    def eta_text(self) -> str:
        if not self.model_cached or self.state != "running" or self.pct <= 0.05 or self.started is None:
            return ""
        elapsed = time.monotonic() - self.started
        eta = elapsed / self.pct - elapsed
        return f" · est. ~{max(1, int(round(eta / 60)))} min remaining"

    def stage_text(self) -> str:
        return f"stage {self.stage}/4 {self.message}"


# --------------------------------------------------------------------------- error classifier
# Spec 6.3 classes in order (first match wins).  The tokens are anchored so that file names such as
# "lecture-412/a.mp4" or "login.mp4" cannot trigger the download classes, and "timed out" alone is no
# longer a model-download symptom (ffmpeg timeouts say "ffmpeg timed out" and belong to class 6).
_ERR_CLASSES = [
    (re.compile(r"找不到文件|FileNotFoundError|No such file"), 1),
    (re.compile(r"HTTP Error 412|(?<![\w/.\\-])-(?:352|404)\b|Sign in to confirm|(?i:\blog ?in\b)|"
                r"(?i:\bcookies?\b)|members-only|(?i:\bpremium\b)|需要登录"), 2),
    (re.compile(r"Unsupported URL|HTTP Error 40\d|Video unavailable|is not a valid URL|DownloadError"), 3),
    (re.compile(r"huggingface|hf-mirror|HTTPSConnectionPool|ConnectionError|Max retries|SSLError|Read timed out"), 4),
    (re.compile(r"CUDA|cudnn|cublas|out of memory|ctranslate2"), 5),
    (re.compile(r"Invalid data found|moov atom|could not find codec|ffmpeg .* failed|ffmpeg timed out|MediaError|"
                r"ffmpeg 失败|ffmpeg 读不了"), 6),
]
_DOWNLOAD_CLASSES = {2, 3}                     # only meaningful when the source is a URL
# Lines that must never drive the classification: the CLI's recoverable GPU->CPU fallback notice
# (asr.py: "  GPU 不可用（RuntimeError: CUDA ...），退回 CPU"), the echoed video title ("  《...》"), and
# download progress echoes (a title containing "login" or "CUDA" is not an error).
_RE_NOISE = re.compile(r"GPU 不可用|退回 CPU|^\s+《.*》\s*$|^\s+下载 \d+%|^\s+yt-dlp: \[download\]")
_RE_FFMPEG_TIMEOUT = re.compile(r"ffmpeg timed out after ([\d.]+\s?s)")
_RE_URL_IN_LINE = re.compile(r"https?://\S+")   # URLs themselves must not feed the token matching
_RE_NOTFOUND_PATH = re.compile(r"找不到文件：(.+)$")


def classify_error(lines, rc: int | None = None, kill_reason: str | None = None, source: str = "") -> str:
    """English ERROR text for a failed job: 9 classes (spec 6.3), then `log:` + the last 10 raw lines."""
    lines = [str(x).rstrip("\r\n") for x in list(lines)]
    tail40 = lines[-40:]
    text = "\n".join(tail40)
    if kill_reason == "timeout":
        head = (f"ERROR: the job exceeded YUEYING_JOB_TIMEOUT ({int(JOB_TIMEOUT)} s) and was stopped. "
                f'Try mode="transcript", a smaller model, or a shorter video.')
    elif kill_reason:
        head = f"ERROR: this job was cancelled ({kill_reason}). Call watch_video again."
    else:
        cls, match_line = 9, ""
        local_source = bool(source) and not store.is_url(source)
        scan = [ln for ln in tail40 if not _RE_NOISE.search(ln)]
        for rx, n in _ERR_CLASSES:
            if local_source and n in _DOWNLOAD_CLASSES:
                continue
            for ln in scan:
                if rx.search(_RE_URL_IN_LINE.sub("", ln) if n in _DOWNLOAD_CLASSES else ln):
                    cls, match_line = n, ln.strip()
                    break
            if cls != 9:
                break
        if cls == 1:
            path = source
            for ln in reversed(tail40):
                m = _RE_NOTFOUND_PATH.search(ln)
                if m:
                    path = m.group(1).strip()
                    break
            return f"ERROR: file not found: {path}. Pass an absolute path to an existing video file."
        if cls == 2:
            head = ('ERROR: the site refused the download (login or cookies required). Retry with '
                    'cookies_from_browser="edge" or "chrome" (on Windows close Chrome first — it locks its cookie database).')
        elif cls == 3:
            head = (f"ERROR: could not fetch this URL: {match_line}. Check the link, or update yt-dlp "
                    f"(pip install -U yt-dlp / uv cache clean yueying).")
        elif cls == 4:
            head = ('ERROR: could not download the Whisper speech model. Run "yueying mcp --setup" once with '
                    'network access, or set HF_ENDPOINT=https://hf-mirror.com in the server env, or pass model="small".')
        elif cls == 5:
            head = 'ERROR: GPU speech recognition failed. Retry with model="small", or set YUEYING_DEVICE=cpu in the server env.'
        elif cls == 6:
            to = next((m.group(1) for ln in reversed(tail40) for m in [_RE_FFMPEG_TIMEOUT.search(ln)] if m), None)
            if to:
                head = (f"ERROR: ffmpeg timed out after {to} while reading this file (it may be huge or damaged). "
                        f'Try mode="transcript", frame_interval_seconds=20, or a shorter video.')
            else:
                head = "ERROR: ffmpeg could not read this file — it does not look like a playable video/audio file."
        else:
            head = f"ERROR: processing failed (exit code {rc if rc is not None else '?'})."
    tail = [ln for ln in lines if ln.strip()][-10:]
    return head + ("\nlog:\n" + "\n".join(tail) if tail else "")


# --------------------------------------------------------------------------- helpers
def _covers(manifest: dict, mode: str) -> bool:
    """Does a cached entry satisfy the requested mode?  (spec 4.5 step 4)"""
    opts = manifest.get("options") or {}
    has_frames = bool(manifest.get("frames"))
    frames_ok = (mode == "transcript" or has_frames or not manifest.get("width")
                 or (bool(opts) and not opts.get("no_frames")))
    kind = (manifest.get("text_source") or {}).get("kind") or "none"
    text_ok = mode == "frames" or kind != "none" or not opts.get("no_asr")
    return bool(frames_ok and text_ok)


# A refresh may delete a folder whole only when it is one of OUR entries (under the output root).  A custom
# output_dir can be any user folder (Documents, a project checkout ...): there we remove only the files the
# manifest itself lists plus the pipeline's own scratch names, never the folder and never anything else
# (spec 4.6: watch_video is not destructive).
_ENTRY_SCRATCH = ("manifest.json", ".job", ".job.tmp", "embedded.srt", "audio.wav")
_ENTRY_SCRATCH_DIRS = ("_download", os.path.join("frames", "extra"))


def _under_root(entry: Path) -> bool:
    """True when `entry` lies strictly inside the output root (never the root itself)."""
    try:
        rel = entry.resolve().relative_to(store.out_root().resolve())
        return bool(rel.parts)
    except (ValueError, OSError):
        return False


_ENTRY_NAME_RE = re.compile(r"-[0-9a-f]{8}$")


def _is_entry_dir(entry: Path) -> bool:
    """True when `entry` is safe to rmtree as a whole: an auto-named `<slug>-<key8>` folder that is a direct
    child of the output root.  Anything else (a custom output_dir, even one placed under the root) only ever
    loses the files yueying itself wrote there."""
    try:
        rel = entry.resolve().relative_to(store.out_root().resolve())
    except (ValueError, OSError):
        return False
    return len(rel.parts) == 1 and bool(_ENTRY_NAME_RE.search(rel.parts[0]))


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


def _manifest_files(entry: Path) -> list[Path]:
    """Files an entry's manifest.json points at (raw JSON, rebased onto `entry`), restricted to `entry`."""
    try:
        with open(entry / "manifest.json", encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(m, dict):
        return []
    names: list[str] = []
    for k in ("report", "transcript_srt", "transcript_txt"):
        if m.get(k):
            names.append(str(m[k]))
    names += [str(g) for g in (m.get("grids") or []) if g]
    for fr in m.get("frames") or []:
        if isinstance(fr, dict) and fr.get("file"):
            names.append(str(fr["file"]))
    out: list[Path] = []
    for n in names:
        # recorded paths may come from another machine/folder: keep only the basename (and frames/ prefix)
        parts = re.split(r"[\\/]", n)
        rel = Path(*parts[-2:]) if len(parts) >= 2 and parts[-2] == "frames" else Path(parts[-1])
        p = entry / rel
        if p.is_file() and _inside(p, entry):
            out.append(p)
    return out


def _clear_entry(entry: Path) -> None:
    """Delete cached results for a refresh without ever wiping an arbitrary user folder.

    Entries under the output root are removed whole.  Any other folder (custom output_dir) loses only the
    files its manifest lists, the pipeline's scratch files/folders and then-empty frames/ directories."""
    if not entry.exists():
        return
    if _is_entry_dir(entry):
        _rmtree_retry(entry)
        return
    logger.info("refresh: %s is outside the output root; removing only the files yueying wrote there", entry)
    victims: list[Path] = _manifest_files(entry)
    victims += [entry / n for n in _ENTRY_SCRATCH]
    for v in victims:
        if v.is_file() or v.is_symlink():
            with contextlib.suppress(FileNotFoundError):
                v.unlink()
    for d in _ENTRY_SCRATCH_DIRS:
        p = entry / d
        if p.is_dir() and not p.is_symlink():
            _rmtree_retry(p)
    frames = entry / "frames"
    if frames.is_dir() and not frames.is_symlink() and not any(frames.iterdir()):
        frames.rmdir()


def _rmtree_retry(path: Path, tries: int = 3) -> None:
    for i in range(tries):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except OSError as e:                          # WinError 32: a handle still open
            if i == tries - 1:
                raise
            logger.info("rmtree %s retry (%s)", path, e)
            time.sleep(1)


def _load_entry(video: str) -> tuple[Path, dict] | None:
    entry = store.resolve(video)
    if entry is None:
        # a job of this process may have written to a custom output_dir outside the root
        ref = (video or "").strip().strip('"')
        key = ref.lower() if re.fullmatch(r"[0-9a-fA-F]{8}", ref) else None
        if key is None:
            try:
                if store.is_url(ref) or os.path.isfile(os.path.expanduser(ref)):
                    key = store.source_key(ref)
            except OSError:
                key = None
        with _JOBS_LOCK:
            done_entry = _DONE_ENTRIES.get(key) if key else None
        if done_entry is not None and (done_entry / "manifest.json").is_file():
            entry = done_entry
        else:
            return None
    from yueying.report import load_manifest
    return entry, load_manifest(str(entry))


def _entry_key(entry: Path) -> str:
    m = re.search(r"-([0-9a-f]{8})$", entry.name.lower())
    return m.group(1) if m else ""


def _video_id(entry: Path, manifest: dict) -> str:
    return _entry_key(entry) or str(entry)


def _not_found(x: str) -> str:
    return (f'ERROR: no processed video matches "{x}". Call watch_video first, or use list_videos to '
            f"find the video_id or folder.")


def _no_frames(manifest: dict) -> str:
    return (f"ERROR: {manifest.get('title') or 'this video'} has no keyframes (processed with mode=transcript, "
            f'or audio-only). Run watch_video(video=..., refresh=true, mode="full") to extract them.')


def _fmt_size(n: int) -> str:
    n = float(n or 0)
    if n < 1024 * 1024:
        return f"{max(1, int(round(n / 1024)))} KB"
    if n < 1024 ** 3:
        return f"{int(round(n / 1024 ** 2))} MB"
    return f"{n / 1024 ** 3:.1f} GB"


def _short_text(ts: dict, count: int | None = None) -> str:
    kind = (ts or {}).get("kind") or "none"
    lang = (ts or {}).get("language") or ""
    n = count if count is not None else (ts or {}).get("count")
    if kind == "subtitle":
        return " ".join(x for x in ("subtitles", lang) if x) + (f" ({n} cues)" if n else "")
    if kind == "asr":
        return " ".join(x for x in ("speech recognition", lang) if x) + (f" ({n} seg)" if n else "")
    return "none"


def _render_running(key: str, job: Job, attached: bool) -> str:
    stage = job.stage_text() if job.state == "running" else "stage 0/4 waiting for another video to finish"
    lines = [f"RUNNING video_id={key} · {stage} · elapsed {int(job.elapsed)} s{job.eta_text()}"]
    if job.title:
        lines.append(f"Title: {job.title}")
    note = ("Call watch_video again with the same video to keep waiting (each call waits up to wait_seconds). "
            "Do not start another video. (Options of the running job apply; pass refresh=true to restart "
            "with different options.)")
    if attached:
        note = "Resumed the job already running for this video; options passed in this call were ignored. " + note
    lines.append(note)
    return "\n".join(lines)


def _render_foreign(key: str, entry: Path, marker: dict, since: float) -> str:
    return (f"RUNNING video_id={key} · another yueying server process is processing this video "
            f"(pid {marker.get('pid')}, started {int(max(0, time.time() - float(marker.get('started') or time.time())))} s ago)\n"
            f"Folder: {entry}\n"
            "Call watch_video again with the same video to keep waiting; it will return the finished result "
            "as soon as manifest.json appears. Pass refresh=true to stop that job and start over in this server.")


def _live_marker(entry: Path) -> dict | None:
    """The entry's .job marker when a job is really still running there.

    store.read_job_marker() already drops stale / dead-pid markers (and pid reuse when the child's
    start time was recorded).  On top of that, a manifest.json written AFTER the marker means the job
    that wrote the marker finished but its server died before clearing it: not running."""
    marker = store.read_job_marker(entry)
    if not marker:
        return None
    try:
        mp = entry / "manifest.json"
        if mp.is_file() and mp.stat().st_mtime > float(marker.get("started") or 0):
            return None
    except OSError:
        pass
    return marker


async def _report(ctx: Context, pct: float, message: str) -> None:
    try:
        await ctx.report_progress(float(min(max(pct, 0.0), 1.0)), 1.0, message)
    except Exception as e:  # never let a progress hiccup fail the call
        logger.debug("report_progress: %s", e)


Lang = Literal["auto", "zh", "en", "ja", "ko", "fr", "de", "es", "ru", "pt", "it", "yue"]
Model = Literal["auto", "tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
Browser = Literal["chrome", "edge", "firefox", "brave", "chromium", "safari"]
_VIDEO_DESC = "video_id, results folder, or the path/URL given to watch_video"


# --------------------------------------------------------------------------- tools
@mcp.tool(
    name="watch_video",
    description=(
        "Turn a video into a timestamped transcript plus a keyframe overview so you can summarize it, answer "
        "questions about it, extract steps, commands or code, or write notes. `video` is an absolute path to a "
        "local video/audio file or a YouTube / Bilibili / Douyin / Xiaohongshu / TikTok / Vimeo URL. Fully "
        "offline: platform subtitles are used when they exist, otherwise local Whisper speech recognition (GPU "
        "when available, CPU otherwise); no API key. Results are cached per video, so calling again with the "
        "same video is instant. Typical time: 5–60 s for short subtitled videos, a few minutes for long videos "
        "without subtitles; the very first speech recognition downloads a model once (~480 MB on CPU, ~1.6 GB "
        "on GPU). If the reply starts with RUNNING, call watch_video again with the same `video` — it resumes "
        "waiting for the same job; do not change options and do not start other videos meanwhile. The reply is "
        "an English overview: metadata, chapters, the list of contact sheets (see them with get_frames), file "
        "paths, and the transcript with [mm:ss] timestamps (truncated at max_chars with a start time for "
        "get_transcript). Cite timestamps like (03:15). Not for live streams or images."
    ),
    annotations=ToolAnnotations(title="Watch a video", read_only_hint=False, destructive_hint=False,
                                idempotent_hint=True, open_world_hint=True),
    structured_output=False,
)
async def watch_video(
    video: Annotated[str, Field(description="Absolute path to a local video/audio file, or a video page URL (YouTube, Bilibili, Douyin, Xiaohongshu, TikTok, Vimeo, X and other yt-dlp sites)")],
    ctx: Context,
    mode: Annotated[Literal["full", "transcript", "frames"], Field(description="full = transcript + keyframes (default); transcript = no keyframes; frames = no speech recognition (platform subtitles still used)")] = "full",
    language: Annotated[Lang, Field(description="Spoken language; auto detects it. Set it when you know it for better accuracy")] = "auto",
    model: Annotated[Model, Field(description="Whisper model used only when the video has no subtitles. auto = large-v3-turbo on an NVIDIA GPU, small on CPU")] = "auto",
    frame_interval_seconds: Annotated[float | None, Field(ge=0.5, le=120, description="Approximate seconds between keyframes; None = automatic by duration (2–20 s). Use 2 for code/slide-heavy screencasts")] = None,
    cookies_from_browser: Annotated[Browser | None, Field(description="Reuse a browser login for HD or member-only Bilibili / sign-in-gated YouTube. Close Chrome first on Windows")] = None,
    output_dir: Annotated[str | None, Field(description="Absolute folder for the results; default $YUEYING_OUT_DIR/<name>-<id>")] = None,
    refresh: Annotated[bool, Field(description="Discard cached results (and any running job) for this video and process it again")] = False,
    wait_seconds: Annotated[int, Field(ge=0, le=1500, description="How long this call may block before answering RUNNING. Keep 45 in Claude Desktop/Cursor (60 s client timeout); Claude Code/Cline may use up to 1500")] = 45,
    max_chars: Annotated[int, Field(ge=2000, le=100000, description="Maximum characters of transcript included in the reply")] = 12000,
) -> str:
    try:
        return await _watch_video(video, ctx, mode, language, model, frame_interval_seconds, cookies_from_browser,
                                  output_dir, refresh, wait_seconds, max_chars)
    except Exception as e:
        logger.error("watch_video failed: %s: %s", type(e).__name__, e)
        logger.debug("watch_video traceback", exc_info=True)
        return f"ERROR: internal error in watch_video: {type(e).__name__}: {e}"


async def _watch_video(video, ctx, mode, language, model, frame_interval_seconds, cookies_from_browser,
                       output_dir, refresh, wait_seconds, max_chars) -> str:
    # 1. validate
    src = (video or "").strip().strip('"')
    if not src:
        return "ERROR: file not found: (empty). Pass an absolute path to an existing video file."
    if not store.is_url(src):
        p = Path(src).expanduser()
        if not p.is_file():
            return f"ERROR: file not found: {src}. Pass an absolute path to an existing video file."
        src = store.local_path(str(p))          # same normalisation as store.source_key / store.resolve
    if model == "auto" and os.environ.get("YUEYING_MODEL"):
        model = os.environ["YUEYING_MODEL"].strip() or "auto"
    if not cookies_from_browser and os.environ.get("YUEYING_COOKIES_FROM_BROWSER"):
        cookies_from_browser = os.environ["YUEYING_COOKIES_FROM_BROWSER"].strip() or None

    # 2. key / entry
    key = store.source_key(src)
    entry = Path(output_dir).expanduser().resolve() if output_dir else store.entry_dir(src)
    deadline = time.monotonic() + max(0, int(wait_seconds))
    manifest_path = entry / "manifest.json"

    def _done(manifest: dict, cached: bool, took: float | None, folder: Path = entry) -> str:
        return query.overview(manifest, max_chars, video_id=key, cached=cached, took=took, folder=str(folder))

    # 3. refresh
    if refresh:
        with _JOBS_LOCK:
            job = JOBS.get(key)
        if job and job.alive:
            job.kill("cancelled by refresh=true")
            await anyio.to_thread.run_sync(job.wait, 15)
        with _JOBS_LOCK:
            if JOBS.get(key) is job:
                JOBS.pop(key, None)
        marker = _live_marker(entry)
        if marker and marker.get("server_pid") != os.getpid():
            with contextlib.suppress(Exception):
                _kill_pid_tree(int(marker["pid"]))
        try:
            await anyio.to_thread.run_sync(_clear_entry, entry)
        except OSError as e:
            return f"ERROR: could not delete the cached results folder {entry} ({e}). Close programs using it and retry."
    # 4. cache hit
    elif manifest_path.is_file() and not _live_marker(entry):
        try:
            from yueying.report import load_manifest
            manifest = load_manifest(str(entry))
        except Exception as e:
            logger.warning("unreadable manifest in %s: %s", entry, e)
            manifest = None
        if manifest and _covers(manifest, mode):
            return _done(manifest, True, None)

    # 5. a live .job marker from another process
    with _JOBS_LOCK:
        job = JOBS.get(key)
    marker = _live_marker(entry)
    if marker and marker.get("server_pid") != os.getpid() and not (job and job.alive):
        started = time.monotonic()
        while True:
            marker = _live_marker(entry)
            if not marker or marker.get("server_pid") == os.getpid():
                break
            if time.monotonic() >= deadline:
                return _render_foreign(key, entry, marker, started)
            await _report(ctx, 0.0, "another yueying server process is processing this video")
            await anyio.sleep(POLL_INTERVAL)
        if manifest_path.is_file():
            from yueying.report import load_manifest
            manifest = load_manifest(str(entry))
            if _covers(manifest, mode):
                return _done(manifest, True, None)
        # marker gone but nothing usable: fall through and run our own job

    # 6./7. attach or spawn
    with _JOBS_LOCK:
        job = JOBS.get(key)
        attached = bool(job and job.alive)
        if not attached:
            cmd = build_command(src, entry, mode=mode, language=language, model=model,
                                frame_interval_seconds=frame_interval_seconds,
                                cookies_from_browser=cookies_from_browser)
            job = Job(key, src, entry, cmd, model=model, device=os.environ.get("YUEYING_DEVICE") or "auto",
                      options={"mode": mode, "language": language, "model": model,
                               "frame_interval_seconds": frame_interval_seconds})
            JOBS[key] = job

    # 8. long-poll
    while job.alive and time.monotonic() < deadline:
        await _report(ctx, job.pct, job.message)
        await anyio.sleep(POLL_INTERVAL)

    # 9. render
    if job.state == "done" and job.manifest:
        await _report(ctx, 1.0, "done")
        # an attached job may have been started with another output_dir: report where the files really are
        return _done(job.manifest, False, job.took, folder=job.entry)
    if job.state == "error":
        with _JOBS_LOCK:
            if JOBS.get(key) is job:
                JOBS.pop(key, None)              # the next call may retry
        return classify_error(job.lines, job.rc, job.kill_reason, job.source)
    return _render_running(key, job, attached)


def _kill_pid_tree(pid: int) -> None:
    if _WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW, timeout=30)
    else:
        import signal
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


@mcp.tool(
    name="get_transcript",
    description=(
        "Read part of an already-watched video's transcript with [mm:ss] timestamps. Use when the watch_video "
        "overview was truncated, when the user asks about a specific time range, or to export subtitles "
        "(format='srt'). Returns at most max_chars; when truncated the last line gives next_start so you can "
        "continue from there. Paragraph format is the cheapest."
    ),
    annotations=ToolAnnotations(title="Read transcript by time range", read_only_hint=True, idempotent_hint=True),
    structured_output=False,
)
def get_transcript(
    video: Annotated[str, Field(description="video_id from watch_video/list_videos, the results folder, or the same path/URL given to watch_video")],
    start: Annotated[str, Field(description="Start time: seconds, mm:ss or h:mm:ss")] = "0",
    end: Annotated[str | None, Field(description="End time (same formats); None = to the end")] = None,
    format: Annotated[Literal["paragraphs", "segments", "srt"], Field(description="paragraphs = compact [mm:ss] text (fewest tokens); segments = one line per subtitle cue with start-end; srt = subtitle blocks")] = "paragraphs",
    max_chars: Annotated[int, Field(ge=1000, le=100000)] = 8000,
) -> str:
    try:
        found = _load_entry(video)
        if not found:
            return _not_found(video)
        entry, m = found
        try:
            t0 = store.parse_time(start if start not in (None, "") else "0")
            t1 = store.parse_time(end) if end not in (None, "") else None
        except ValueError as e:
            return f"ERROR: bad time ({e}). Use seconds, mm:ss or h:mm:ss."
        if t1 is not None and t1 <= t0:
            return f"ERROR: end ({store.fmt_time(t1)}) must be after start ({store.fmt_time(t0)})."
        title = m.get("title") or entry.name
        reason = query.no_text_reason(m)
        if reason:
            return f"No transcript: {reason}." + (" Use get_frames to see the frames." if m.get("grids") else "")
        duration = float(m.get("duration") or 0)
        text, nxt = query.transcript_text(m, t0, t1, format, max_chars)
        if not text:
            last = max((float(s.get("end") or 0) for s in m.get("segments") or []), default=0.0)
            return f"ERROR: start={store.fmt_time(t0)} is beyond the last paragraph ({store.fmt_time(last)})."
        end_txt = store.fmt_time(t1) if t1 is not None else store.fmt_time(duration)
        head = (f"Transcript of {title} {store.fmt_time(t0)}–{end_txt} (video is {store.fmt_time(duration)}) — "
                f"{format}, source: {query.text_description(m)}")
        out = head + "\n" + text
        if nxt is not None:
            out += f'\nTRUNCATED — next_start="{store.fmt_time(nxt)}"'
        return out
    except Exception as e:
        logger.error("get_transcript failed: %s: %s", type(e).__name__, e)
        logger.debug("get_transcript traceback", exc_info=True)
        return f"ERROR: internal error in get_transcript: {type(e).__name__}: {e}"


@mcp.tool(
    name="search_transcript",
    description=(
        "Find where something is said in an already-watched video. Each hit shows the time, the surrounding "
        "sentences, and the nearest keyframe number and contact-sheet number so you can follow up with "
        "get_frame_at or get_transcript. Use this instead of paging the whole transcript when the user asks "
        "'when does he mention X' or 'find the part about Y'."
    ),
    annotations=ToolAnnotations(title="Search transcript", read_only_hint=True, idempotent_hint=True),
    structured_output=False,
)
def search_transcript(
    video: Annotated[str, Field(description=_VIDEO_DESC)],
    query: Annotated[str, Field(description="Words to look for; separate alternatives with spaces (any term matches, more terms rank higher). Case-insensitive")],
    context_seconds: Annotated[float, Field(ge=0, le=120, description="Seconds of surrounding text to include around each hit")] = 15,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str:
    try:
        found = _load_entry(video)
        if not found:
            return _not_found(video)
        entry, m = found
        title = m.get("title") or entry.name
        reason = _query.no_text_reason(m)
        if reason:
            return f"No transcript: {reason}." + (" Use get_frames to see the frames." if m.get("grids") else "")
        q = (query or "").strip()
        if not q:
            return "ERROR: query is empty."
        hits = _query.search(m, q, float(context_seconds), int(limit))
        if not hits:
            return f'No hits for "{q}" in {title}. Try a synonym, or read a range with get_transcript.'
        lines = [f'{len(hits)} hit{"s" if len(hits) != 1 else ""} for "{q}" in {title}:']
        for h in hits:
            where = ""
            if h.get("frame_index") is not None:
                where = f" (keyframe #{h['frame_index']}"
                where += f", grid {h['grid_no']})" if h.get("grid_no") else ")"
            lines.append(f"- [{store.fmt_time(h['time'])}] {h['snippet']}{where}")
        return "\n".join(lines)
    except Exception as e:
        logger.error("search_transcript failed: %s: %s", type(e).__name__, e)
        logger.debug("search_transcript traceback", exc_info=True)
        return f"ERROR: internal error in search_transcript: {type(e).__name__}: {e}"


def _jpeg(path: str, max_width: int, quality: int) -> Image:
    return Image(data=query.shrink(path, int(max_width), quality), format="jpeg")


@mcp.tool(
    name="get_frames",
    description=(
        "See what is on screen in an already-watched video. Returns contact-sheet images (3x3 keyframes in "
        "time order, every tile labelled '#number mm:ss' bottom-left) or individual keyframes. Read the contact "
        "sheets first to get the visual storyline, then request single frames only when you need to read code, "
        "slides or UI text. At most 3 images per call (default 2), downscaled to max_width; page with "
        "start/count. Every image is preceded by its absolute file path so hosts that can read files may open "
        "the full-size original instead."
    ),
    annotations=ToolAnnotations(title="Show contact sheets / keyframes", read_only_hint=True, idempotent_hint=True),
    structured_output=False,
)
def get_frames(
    video: Annotated[str, Field(description=_VIDEO_DESC)],
    kind: Annotated[Literal["grids", "frames"], Field(description="grids = 3x3 contact sheets, 9 keyframes per image (start here); frames = individual full-size keyframes")] = "grids",
    start: Annotated[int, Field(ge=1, description="1-based number of the first image: contact-sheet number for grids, keyframe number (as printed on the tiles) for frames")] = 1,
    count: Annotated[int, Field(ge=1, le=3, description="Images per call; keep 2 or fewer in Claude Desktop")] = 2,
    max_width: Annotated[int, Field(ge=320, le=1920, description="Downscale width in pixels; 1280 is about 150 KB per contact sheet")] = 1280,
) -> list[str | Image]:
    try:
        found = _load_entry(video)
        if not found:
            return [_not_found(video)]
        entry, m = found
        vid = _video_id(entry, m)
        frames = query.frames(m)
        if not frames:
            return [_no_frames(m)]
        out: list[str | Image] = []
        if kind == "grids":
            items = query.grid_ranges(m)
            if not items:
                return [_no_frames(m)]
            if start > len(items):
                return [f"ERROR: start={start} is beyond the last contact sheet ({len(items)})."]
            sel = items[start - 1:start - 1 + count]
            out.append(f"grids {start}–{start + len(sel) - 1} of {len(items)} · {entry}")
            for g in sel:
                path = g["file"]
                cap = os.path.basename(path)
                if g["first_index"] is not None:
                    cap += (f" · frames #{g['first_index']}–#{g['last_index']} · "
                            f"{store.fmt_time(g['start'])}–{store.fmt_time(g['end'])}")
                cap += f" · {path}"
                if not os.path.isfile(path):
                    out.append(cap + " (file missing on disk)")
                    continue
                out.append(cap)
                out.append(_jpeg(path, max_width, 72))
            nxt = start + len(sel)
            if nxt <= len(items):
                out.append(f'Next: get_frames(video="{vid}", start={nxt})')
        else:
            pos = next((i for i, f in enumerate(frames) if f.get("index") == start), None)
            if pos is None:
                if start > len(frames):
                    last = frames[-1].get("index", len(frames))
                    return [f"ERROR: start={start} is beyond the last keyframe (#{last})."]
                pos = start - 1
            sel = frames[pos:pos + count]
            first, last = sel[0].get("index", pos + 1), sel[-1].get("index", pos + len(sel))
            out.append(f"keyframes #{first}–#{last} of {len(frames)} · {entry}")
            for f in sel:
                path = str(f.get("file") or "")
                cap = f"keyframe #{f.get('index')} · {store.fmt_time(f.get('time') or 0)} · {path}"
                if not os.path.isfile(path):
                    out.append(cap + " (file missing on disk)")
                    continue
                out.append(cap)
                out.append(_jpeg(path, max_width, 80))
            if pos + len(sel) < len(frames):
                nxt = frames[pos + len(sel)].get("index", pos + len(sel) + 1)
                out.append(f'Next: get_frames(video="{vid}", kind="frames", start={nxt})')
        return out
    except Exception as e:
        logger.error("get_frames failed: %s: %s", type(e).__name__, e)
        logger.debug("get_frames traceback", exc_info=True)
        return [f"ERROR: internal error in get_frames: {type(e).__name__}: {e}"]


def _local_source(entry: Path, m: dict) -> str | None:
    """The source media file when it is still available for exact frame extraction, else None."""
    src = str(m.get("source") or "")
    if src and not store.is_url(src) and os.path.isfile(src):
        key = _entry_key(entry)
        try:
            if not key or store.source_key(src) == key:
                return src
        except OSError:
            pass
        if not key:
            return src
    kept = sorted((entry / "_download").glob("source.*")) if (entry / "_download").is_dir() else []
    return str(kept[0]) if kept else None


@mcp.tool(
    name="get_frame_at",
    description=(
        "Look closely at one moment of an already-watched video, e.g. to read code, a slide, a chart or a UI. "
        "For local files that still exist the exact frame at that time is extracted from the video; otherwise "
        "the nearest cached keyframe is returned and the caption says so. Also returns the transcript "
        "paragraphs spoken around that time. Returns one image (~100 KB at 960 px)."
    ),
    annotations=ToolAnnotations(title="Look at one moment", read_only_hint=True, idempotent_hint=True),
    structured_output=False,
)
def get_frame_at(
    video: Annotated[str, Field(description=_VIDEO_DESC)],
    time: Annotated[str, Field(description="Timestamp: seconds, mm:ss or h:mm:ss, e.g. '03:15'")],
    max_width: Annotated[int, Field(ge=320, le=1920)] = 960,
) -> list[str | Image]:
    try:
        found = _load_entry(video)
        if not found:
            return [_not_found(video)]
        entry, m = found
        try:
            t = store.parse_time(time)
        except ValueError as e:
            return [f"ERROR: bad time ({e}). Use seconds, mm:ss or h:mm:ss."]
        duration = float(m.get("duration") or 0)
        if duration and t > duration + 1.0:
            return [f"ERROR: time={store.fmt_time(t)} is beyond the end of the video ({store.fmt_time(duration)})."]
        if duration:
            t = min(t, max(0.0, duration - 0.3))

        path, caption = None, ""
        src = _local_source(entry, m)
        if src:
            from yueying import frames as fr
            out = entry / "frames" / "extra" / f"at_{fr.slug_time(t)}.jpg"
            if not out.is_file():
                with _FF:
                    try:
                        ok = fr.extract_one(src, t, str(out), width=1280, label=f"@{fr.fmt_time(t)}")
                    except Exception as e:
                        logger.warning("extract_one failed at %s: %s", t, e)
                        ok = False
                if not ok:
                    out = None
            if out is not None and out.is_file():
                path = str(out)
                caption = f"frame at {store.fmt_time(t)} (extracted exactly from the source) · {path}"
        if path is None:
            near = _query.nearest_frame(m, t)
            if not near or not os.path.isfile(str(near.get("file") or "")):
                return [_no_frames(m)]
            path = str(near["file"])
            ft = float(near.get("time") or 0)
            if abs(ft - t) <= 1.5:
                caption = f"keyframe #{near['index']} at {store.fmt_time(ft)} (cached keyframe at the requested time {store.fmt_time(t)}) · {path}"
            else:
                caption = f"keyframe #{near['index']} at {store.fmt_time(ft)} (nearest cached keyframe; requested {store.fmt_time(t)}) · {path}"
        lines = [caption]
        paras = _query.paragraphs_around(m, t, 3)
        if paras:
            lines.append("Spoken around then:")
            lines += [f"[{store.fmt_time(p['start'])}] {p['text']}" for p in paras]
        else:
            reason = _query.no_text_reason(m)
            lines.append(f"No transcript: {reason}." if reason else "No transcript around this time.")
        return ["\n".join(lines), _jpeg(path, max_width, 80)]
    except Exception as e:
        logger.error("get_frame_at failed: %s: %s", type(e).__name__, e)
        logger.debug("get_frame_at traceback", exc_info=True)
        return [f"ERROR: internal error in get_frame_at: {type(e).__name__}: {e}"]


@mcp.tool(
    name="list_videos",
    description=(
        "List videos already processed by yueying on this machine (newest first) and jobs currently running, "
        "with video_id, title, duration, text source, date, folder and size. Use when the user refers to a "
        "video watched earlier, to get a video_id for the other tools, or to see how much disk space results "
        "use. Instant and read-only."
    ),
    annotations=ToolAnnotations(title="List processed videos", read_only_hint=True, idempotent_hint=True),
    structured_output=False,
)
def list_videos(limit: Annotated[int, Field(ge=1, le=200)] = 20) -> str:
    try:
        root = store.out_root()
        entries = store.list_entries(root)
        total = sum(e.get("size_bytes", 0) for e in entries)
        lines: list[str] = []
        if entries:
            lines.append(f"Processed videos in {root} (total {_fmt_size(total)}):")
            rows = [("video_id", "title", "duration", "text", "processed", "size", "folder")]
            for e in entries[:limit]:
                title = str(e.get("title") or "")
                if len(title) > 40:
                    title = title[:39] + "…"
                created = str(e.get("created_at") or "")[:16].replace("T", " ")
                rows.append((e.get("key") or "-", title, store.fmt_time(e.get("duration") or 0),
                             _short_text(e.get("text_source") or {}), created, _fmt_size(e.get("size_bytes", 0)),
                             str(e.get("dir") or "")))
            widths = [max(len(r[i]) for r in rows) for i in range(6)]
            for r in rows:
                cells = [r[i].ljust(widths[i]) for i in range(6)] + [r[6]]
                lines.append(" | ".join(cells))
            if len(entries) > limit:
                lines.append(f"… {len(entries) - limit} more (raise limit to see them).")
        else:
            lines.append(f"No processed videos in {root} yet. Call watch_video first.")
        with _JOBS_LOCK:
            running = [j for j in JOBS.values() if j.alive]
        if running:
            lines.append("Running jobs: " + "; ".join(
                f"{j.key} {j.stage_text() if j.state == 'running' else 'queued'}, elapsed {int(j.elapsed)} s"
                for j in running))
        lines.append("Delete a folder to free space; watch_video(refresh=true) reprocesses.")
        return "\n".join(lines)
    except Exception as e:
        logger.error("list_videos failed: %s: %s", type(e).__name__, e)
        logger.debug("list_videos traceback", exc_info=True)
        return f"ERROR: internal error in list_videos: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- CLI entry (--check / --setup / run)
def _out(msg: str = "") -> None:
    """stdout writer for --check/--version/--setup (no server is running then)."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _ffmpeg_path() -> str:
    try:
        from yueying import ffm
        return ffm.ffmpeg_exe()
    except Exception as e:
        return f"NOT FOUND ({e})"


def _check_line() -> str:
    pyver = ".".join(str(x) for x in sys.version_info[:3])
    return f"yueying-mcp {__version__} ok (python {pyver}, ffmpeg {_ffmpeg_path()})"


def _ephemeral_env() -> bool:
    """Is this interpreter a throw-away `uvx` / `uv tool run` environment (uv cache), not an install?"""
    parts = [p.lower().rstrip("\\/") for p in Path(sys.executable).resolve().parts]
    return any(p.startswith("archive-v") for p in parts) or ("uv" in parts and "cache" in parts) \
        or bool(os.environ.get("UV_INTERNAL__PARENT_INTERPRETER"))


def _launch_command() -> list[str]:
    """How a host should start the server: the absolute path of the yueying-mcp executable that belongs to
    the interpreter running --setup (what the docs promise); the absolute path of `uvx` + `yueying mcp` only
    when --setup itself was run through uvx (that interpreter lives in uv's cache and can vanish)."""
    exe_dir = Path(sys.executable).parent
    cand = exe_dir / ("yueying-mcp.exe" if _WIN else "yueying-mcp")
    if cand.is_file() and not _ephemeral_env():
        return [str(cand)]
    uvx = shutil.which("uvx")
    if uvx:
        # absolute path: hosts such as Claude Desktop often start without the user's PATH ("spawn uvx ENOENT")
        return [os.path.abspath(uvx), "yueying", "mcp"]
    if cand.is_file():
        return [str(cand)]
    return [sys.executable, "-m", "yueying", "mcp"]


def _run_child(args: list[str], *, passthrough: bool = False, timeout: float | None = None) -> subprocess.CompletedProcess:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "env": _child_env(), "timeout": timeout}
    if _WIN:
        kwargs["creationflags"] = _NO_WINDOW
    if passthrough:
        return subprocess.run(args, **kwargs)
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


def _setup(model: str) -> int:
    import tempfile

    root = store.out_root()
    _out(f"yueying-mcp {__version__} setup")
    _out(f"  python : {sys.executable}")
    _out(f"  ffmpeg : {_ffmpeg_path()}")
    with contextlib.suppress(OSError):
        root.mkdir(parents=True, exist_ok=True)
    _out(f"  output : {root}")
    _out("")

    device = os.environ.get("YUEYING_DEVICE") or "auto"
    _out(f"[1/4] GPU probe (device={device})")
    probe = ("import sys; from yueying import asr; asr._add_cuda_dlls(); d, c = asr.pick_device(sys.argv[1]); "
             "print(d + ' ' + c)")
    try:
        r = _run_child([sys.executable, "-c", probe, device], timeout=300)
        dev_line = (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else ""
        if r.returncode == 0 and dev_line:
            dev, ct = dev_line.split()[:2]
            _out(f"  speech recognition will run on {dev} ({ct})")
        else:
            dev = "cpu"
            _out(f"  probe failed (exit {r.returncode}); assuming cpu\n{(r.stdout or '')[-800:]}")
    except Exception as e:
        dev = "cpu"
        _out(f"  probe failed ({e}); assuming cpu")

    resolved = models.resolve_model(model, dev)
    cached, hint = _model_cache_status(model, dev)
    _out(f"[2/4] speech model {resolved}" + (" (already cached)" if cached else f" (download {hint})"))
    result_file = Path(tempfile.mkdtemp(prefix="yueying_setup_")) / "model.json"
    dl = ("import json, sys; from yueying.asr import prepare_model; r = prepare_model(sys.argv[1], sys.argv[2]); "
          "open(sys.argv[3], 'w', encoding='utf-8').write(json.dumps(r))")
    rc = 1
    try:
        r = _run_child([sys.executable, "-c", dl, model, device, str(result_file)], passthrough=True)
        rc = r.returncode
        if rc == 0 and result_file.is_file():
            info = json.loads(result_file.read_text(encoding="utf-8"))
            _out(f"  model {info.get('model')} ready on {info.get('device')} ({info.get('compute_type')})"
                 + (f"; cache {info.get('cache_dir')}" if info.get("cache_dir") else ""))
        else:
            _out(f"  model download/load FAILED (exit {rc}). Check the network, or set HF_ENDPOINT=https://hf-mirror.com")
    except Exception as e:
        _out(f"  model download/load FAILED ({e})")
    finally:
        shutil.rmtree(result_file.parent, ignore_errors=True)

    _out("[3/4] smoke test (2 s synthetic clip through the pipeline, no speech recognition)")
    tmp = Path(tempfile.mkdtemp(prefix="yueying_smoke_"))
    try:
        from yueying import ffm
        clip = tmp / "setup.mp4"
        p = ffm.run(["-f", "lavfi", "-i", "testsrc=duration=2:size=640x360:rate=10", "-f", "lavfi",
                     "-i", "sine=frequency=440:duration=2", "-shortest", "-pix_fmt", "yuv420p", str(clip)],
                    check=False, timeout=120)
        if p.returncode != 0 or not clip.is_file():
            _out("  smoke test skipped: lavfi not available")
        else:
            r = _run_child([sys.executable, "-m", "yueying.cli", str(clip), "--no-asr", "--out", str(tmp / "out")],
                           timeout=600)
            if r.returncode == 0 and (tmp / "out" / "manifest.json").is_file():
                _out("  smoke test passed")
            else:
                _out(f"  smoke test FAILED (exit {r.returncode})\n{(r.stdout or '')[-1500:]}")
    except Exception as e:
        _out(f"  smoke test skipped: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _out("[4/4] client configuration")
    cmd = _launch_command()
    env = {"PYTHONUTF8": "1"}
    desktop = {"mcpServers": {"yueying": {"command": cmd[0], "args": cmd[1:], "env": env}}}
    cline = {"mcpServers": {"yueying": {"command": cmd[0], "args": cmd[1:], "env": env, "timeout": 1800,
                                         "autoApprove": ["get_transcript", "search_transcript", "get_frames",
                                                         "get_frame_at", "list_videos"]}}}
    _out("")
    _out("Claude Desktop (claude_desktop_config.json) / Cursor / Windsurf (mcp.json):")
    _out(json.dumps(desktop, indent=2))
    _out("")
    _out("Cline (cline_mcp_settings.json):")
    _out(json.dumps(cline, indent=2))
    _out("")
    _out("Claude Code:")
    _out("  claude mcp add --transport stdio --scope user yueying --env PYTHONUTF8=1 -- " + subprocess.list2cmdline(cmd))
    _out("")
    _out(_check_line())
    return 0 if rc == 0 else 1


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="yueying mcp", add_help=True,
                                 description="yueying MCP server (stdio). Without options it serves on stdin/stdout.")
    ap.add_argument("--check", action="store_true", help="print a one-line self-check to stderr and exit 0")
    ap.add_argument("--version", action="store_true", help="print the version and exit")
    ap.add_argument("--setup", action="store_true",
                    help="probe the GPU, download the speech model, run a smoke test and print client config JSON")
    ap.add_argument("--model", default=os.environ.get("YUEYING_MODEL") or "auto", choices=list(models.MODEL_NAMES),
                    help="model to prepare with --setup (default auto)")
    a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if a.version:
        _out(_check_line())
        return 0
    if a.check:
        sys.stderr.write(_check_line() + "\n")
        sys.stderr.flush()
        return 0
    if a.setup:
        with contextlib.suppress(Exception):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        return _setup(a.model)

    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
