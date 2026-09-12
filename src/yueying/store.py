"""Result store: output root, canonical source keys, entry folders, listing and .job markers.

Standard library only (no PIL, no yueying.cli/frames/report) so the MCP server can import it in
well under 100 ms.  Everything here works on plain 0.1.x manifests too.

Layout::

    <root>/<slug40>-<key8>/          one entry per distinct source
        manifest.json                 written last by the CLI => "entry is complete"
        .job                          JSON {pid, started, server_pid[, pid_start]} while a job is running
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

JOB_STALE_SECONDS = 3 * 3600          # a .job marker older than this is ignored
SLUG_MAX = 40                         # entry name = slug[:40] + "-" + key8
_KEY_RE = re.compile(r"^[0-9a-fA-F]{8}$")
_ENTRY_KEY_RE = re.compile(r"-([0-9a-f]{8})$")

# query parameters that never identify a video (tracking / share noise)
_DROP_PARAMS = re.compile(r"^(utm_.*|spm.*|vd_source|share_.*|from|t|feature|si|fbclid|gclid)$", re.I)
_YT_HOSTS = {"youtube.com", "youtube-nocookie.com", "music.youtube.com"}
_YT_PATH = re.compile(r"^/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{6,})/?$")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,}$")
_BILI_PATH = re.compile(r"^/video/(BV[0-9A-Za-z]{10}|[aA][vV]\d+)/?$")


# --------------------------------------------------------------------------- basics
def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def slug(s: str, limit: int = 60) -> str:
    """Same rule as cli.slug (kept in sync by hand so store stays stdlib-only)."""
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("._")
    return s[:limit] or "video"


def out_root() -> Path:
    """$YUEYING_OUT_DIR (absolute, ~ expanded) else ~/yueying_out.  Never created here."""
    env = (os.environ.get("YUEYING_OUT_DIR") or "").strip()
    if env:
        return Path(os.path.abspath(os.path.expanduser(env)))
    return Path.home() / "yueying_out"


# --------------------------------------------------------------------------- canonical URLs
def _split_host(url: str):
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    return parts, host


def canonical_url(url: str) -> str:
    """Normalise a video page URL so the same video always gets the same key.

    * scheme and host lower-cased, leading ``www.`` / ``m.`` removed, fragment dropped
    * YouTube watch / youtu.be / shorts / live / embed  -> ``https://www.youtube.com/watch?v=ID``
    * Bilibili ``/video/BV...`` or ``/video/avNNN``      -> ``https://www.bilibili.com/video/<id>``
      plus ``?p=N`` only when N > 1
    * anything else: tracking parameters (utm_*, spm*, vd_source, share_*, from, t, feature, si,
      fbclid, gclid) removed, remaining parameters sorted.  Short links (b23.tv, v.douyin.com)
      are NOT resolved - the server never touches the network.
    """
    parts, host = _split_host(url)
    scheme = (parts.scheme or "https").lower()
    path = parts.path or ""
    query = parse_qsl(parts.query, keep_blank_values=True)

    # ---- YouTube
    if host == "youtu.be":
        vid = path.strip("/").split("/")[0]
        if _YT_ID.match(vid):
            return f"https://www.youtube.com/watch?v={vid}"
    if host in _YT_HOSTS:
        vid = None
        if path.rstrip("/") == "/watch":
            vid = next((v for k, v in query if k == "v"), None)
        else:
            m = _YT_PATH.match(path)
            if m:
                vid = m.group(1)
        if vid and _YT_ID.match(vid):
            return f"https://www.youtube.com/watch?v={vid}"

    # ---- Bilibili
    if host == "bilibili.com":
        m = _BILI_PATH.match(path)
        if m:
            vid = m.group(1)
            if vid[:2].lower() == "av":
                vid = "av" + vid[2:]
            page = next((v for k, v in query if k == "p"), None)
            try:
                p = int(page) if page else 1
            except ValueError:
                p = 1
            return f"https://www.bilibili.com/video/{vid}" + (f"?p={p}" if p > 1 else "")

    # ---- generic
    keep = sorted((k, v) for k, v in query if not _DROP_PARAMS.match(k))
    return urlunsplit((scheme, host, path, urlencode(keep, doseq=True), ""))


# --------------------------------------------------------------------------- keys and names
def local_path(source: str) -> str:
    """The one canonical spelling of a local path used for keys and entry names.

    ``~`` expanded, made absolute and passed through ``os.path.realpath`` so that symlinks, junctions,
    subst/mapped drives and 8.3 short names all collapse to the same key (watch_video and the read tools
    must agree on it).  Non-existent paths are returned unchanged apart from being made absolute.
    """
    p = os.path.abspath(os.path.expanduser(source))
    try:
        return os.path.realpath(p)
    except OSError:
        return p


_local_path = local_path      # backward-compatible private alias


def source_key(source: str) -> str:
    """8 hex chars identifying a source.

    URL   -> sha1(canonical_url)
    file  -> sha1(normcase(realpath) + size + int(mtime)); editing the file gives a new key.
    Raises FileNotFoundError for a local path that does not exist.
    """
    if is_url(source):
        payload = canonical_url(source)
    else:
        p = _local_path(source)
        st = os.stat(p)                       # FileNotFoundError propagates on purpose
        payload = os.path.normcase(p) + "\0" + str(st.st_size) + "\0" + str(int(st.st_mtime))
    return hashlib.sha1(payload.encode("utf-8", "surrogateescape")).hexdigest()[:8]


def _url_slug(url: str) -> str:
    canon = canonical_url(url)
    parts, host = _split_host(canon)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if host == "youtube.com" and query.get("v"):
        return "yt-" + query["v"]
    m = _BILI_PATH.match(parts.path or "") if host == "bilibili.com" else None
    if m:
        name = "bili-" + m.group(1)
        if query.get("p"):
            name += "-p" + query["p"]
        return name
    label = host.split(".")[0] if host else ""
    segs = [s for s in (parts.path or "").split("/") if s]
    last = segs[-1] if segs else ""
    return "-".join(x for x in (label, last) if x)


def entry_name(source: str) -> str:
    """``<slug>-<key8>``; slug = yt-<id> / bili-<BV>[-pN] / <host>-<last path segment> / file stem."""
    if is_url(source):
        raw = _url_slug(source)
    else:
        raw = os.path.splitext(os.path.basename(_local_path(source)))[0]
    s = slug(raw)[:SLUG_MAX].strip("._-") or "video"
    return f"{s}-{source_key(source)}"


def entry_dir(source: str, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else out_root()
    return base / entry_name(source)


def _has_manifest(d: Path) -> bool:
    try:
        return d.is_dir() and (d / "manifest.json").is_file()
    except OSError:
        return False


def resolve(video: str, root: Path | None = None) -> Path | None:
    """Find the finished entry for a video_id (8 hex), an entry folder, a local path or a URL.

    Returns the entry directory (contains manifest.json) or None.
    """
    video = (video or "").strip().strip('"')
    if not video:
        return None
    base = Path(root) if root is not None else out_root()

    if _KEY_RE.match(video):                                   # (a) video_id
        key = video.lower()
        try:
            for d in sorted(base.iterdir()):
                if d.name.lower().endswith("-" + key) and _has_manifest(d):
                    return d
        except OSError:
            pass
        return None

    if not is_url(video):                                      # (b) folder (absolute, or relative to root)
        p = Path(os.path.expanduser(video))
        if _has_manifest(p):
            return Path(os.path.abspath(p))
        if not p.is_absolute() and _has_manifest(base / p):
            return base / p
        if p.is_file() and p.name == "manifest.json" and _has_manifest(p.parent):
            return Path(os.path.abspath(p.parent))
        if not p.is_file():                                    # not a source file either
            return None

    try:                                                       # (c) URL or existing source file
        d = entry_dir(video, base)
    except OSError:
        return None
    return d if _has_manifest(d) else None


# --------------------------------------------------------------------------- listing
def dir_size(p: Path) -> int:
    """Total size in bytes of all files below p (symlinks not followed)."""
    total = 0
    stack = [str(p)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def list_entries(root: Path | None = None) -> list[dict]:
    """All finished entries under root, newest first.

    Each item: {dir, key, title, duration, text_source, created_at, mtime, size_bytes, source}.
    """
    base = Path(root) if root is not None else out_root()
    out = []
    try:
        dirs = [d for d in base.iterdir() if d.is_dir()]
    except OSError:
        return out
    for d in dirs:
        mf = d / "manifest.json"
        try:
            with open(mf, encoding="utf-8") as f:
                m = json.load(f)
            mtime = mf.stat().st_mtime
        except (OSError, ValueError):
            continue
        if not isinstance(m, dict):
            continue
        km = _ENTRY_KEY_RE.search(d.name.lower())
        created = m.get("created_at") or _dt.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
        out.append({
            "dir": str(d),
            "key": km.group(1) if km else "",
            "title": m.get("title") or d.name,
            "duration": m.get("duration") or 0,
            "text_source": m.get("text_source") or {"kind": "none"},
            "created_at": created,
            "mtime": mtime,
            "size_bytes": dir_size(d),
            "source": m.get("source") or "",
        })
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


# --------------------------------------------------------------------------- .job markers
def pid_alive(pid: int) -> bool:
    """True when a process with this pid exists (dependency-free, fast)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    ERROR_ACCESS_DENIED = 5
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED     # exists but not ours
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


def pid_start_time(pid: int) -> float | None:
    """Creation time (epoch seconds) of a running process, or None when unknown / unsupported.

    Used to tell a recorded child pid from an unrelated process that reused the number.  Windows via
    GetProcessTimes, Linux via /proc; other platforms return None (the check is then skipped)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.GetProcessTimes.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                                        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                                        ctypes.POINTER(wintypes.FILETIME))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            c, e, k, u = (wintypes.FILETIME() for _ in range(4))
            if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)):
                return None
            ticks = (c.dwHighDateTime << 32) | c.dwLowDateTime          # 100 ns since 1601-01-01
            return ticks / 1e7 - 11644473600.0
        finally:
            k32.CloseHandle(h)
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
                stat = f.read()
            start_ticks = int(stat.rsplit(")", 1)[1].split()[19])      # field 22, after "(comm)"
            with open("/proc/stat", encoding="utf-8") as f:
                btime = next(int(ln.split()[1]) for ln in f if ln.startswith("btime "))
            hz = os.sysconf(os.sysconf_names["SC_CLK_TCK"]) if hasattr(os, "sysconf") else 100
            return btime + start_ticks / float(hz or 100)
        except (OSError, ValueError, IndexError, StopIteration, KeyError):
            return None
    return None


def _marker(entry: Path) -> Path:
    return Path(entry) / ".job"


def write_job_marker(entry: Path, pid: int) -> None:
    entry = Path(entry)
    entry.mkdir(parents=True, exist_ok=True)
    data = {"pid": int(pid), "started": time.time(), "server_pid": os.getpid()}
    pid_start = pid_start_time(pid)
    if pid_start is not None:
        data["pid_start"] = pid_start
    tmp = _marker(entry).with_suffix(".job.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, _marker(entry))


def read_job_marker(entry: Path) -> dict | None:
    """The live marker {pid, started, server_pid}; None when absent, unreadable, older than 3 h
    or when its pid is no longer running."""
    mf = _marker(entry)
    try:
        with open(mf, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        started = float(data.get("started") or 0)
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        return None
    if time.time() - started > JOB_STALE_SECONDS:
        return None
    if not pid_alive(pid):
        return None
    recorded = data.get("pid_start")
    if recorded is not None:
        # the pid is alive, but is it still OUR child?  (pid numbers are reused once a process exits)
        try:
            now_start = pid_start_time(pid)
            if now_start is not None and abs(now_start - float(recorded)) > 2.0:
                return None
        except (TypeError, ValueError):
            pass
    data["pid"], data["started"] = pid, started
    return data


def clear_job_marker(entry: Path) -> None:
    try:
        os.remove(_marker(entry))
    except FileNotFoundError:
        pass
    except OSError:
        pass


# --------------------------------------------------------------------------- time parsing
_HMS_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s?$")


def parse_time(s) -> float:
    """'185' | '185.5' | '3:05' | '1:02:03' | '1h02m03s' | '[03:15]' -> seconds (float, >= 0)."""
    if isinstance(s, bool):
        raise ValueError("not a time")
    if isinstance(s, (int, float)):
        t = float(s)
    else:
        txt = str(s).strip().strip("[]()").strip()
        if not txt:
            raise ValueError("empty time")
        m = _HMS_RE.match(txt)
        if ":" in txt:
            parts = txt.split(":")
            if not 2 <= len(parts) <= 3 or not all(p.strip() for p in parts):
                raise ValueError(f"bad time: {s!r}")
            try:
                nums = [float(p) for p in parts]
            except ValueError:
                raise ValueError(f"bad time: {s!r}") from None
            t = 0.0
            for n in nums:
                t = t * 60 + n
        elif m:
            h, mnt, sec = m.groups()
            t = float(h or 0) * 3600 + float(mnt or 0) * 60 + float(sec)
        else:
            raise ValueError(f"bad time: {s!r}")
    if t != t or t < 0:                                     # NaN or negative
        raise ValueError(f"bad time: {s!r}")
    return t


def fmt_time(t: float) -> str:
    """Same as frames.fmt_time (mm:ss, or h:mm:ss above one hour) without importing PIL."""
    t = int(round(float(t or 0)))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
