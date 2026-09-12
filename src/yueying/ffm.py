"""ffmpeg 封装：优先用系统 ffmpeg，没有就用 imageio-ffmpeg 自带的静态二进制。"""
import functools
import os
import re
import shutil
import subprocess


class MediaError(RuntimeError):
    """ffmpeg failed, timed out, or could not read the file (subclass of RuntimeError for compatibility)."""


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


@functools.lru_cache(maxsize=None)
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def run(args, check=True, timeout=None) -> subprocess.CompletedProcess:
    """跑 ffmpeg，返回 CompletedProcess（stderr 里是 ffmpeg 的日志）。

    timeout: seconds; on expiry the child is killed and MediaError is raised.
    Never inherits stdin, never opens a console window (Windows).
    """
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-y", *[str(a) for a in args]]
    try:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise MediaError(f"ffmpeg timed out after {timeout}s: " + " ".join(cmd)) from None
    if check and p.returncode != 0:
        raise MediaError("ffmpeg 失败: " + " ".join(cmd) + "\n" + p.stderr[-2000:])
    return p


def probe(path: str) -> dict:
    """用 ffmpeg -i 读时长/分辨率/有没有音轨和字幕轨（不依赖 ffprobe）。"""
    p = run(["-i", path], check=False, timeout=120)
    err = p.stderr
    info = {"duration": 0.0, "width": 0, "height": 0, "has_audio": False, "subtitle_streams": 0}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in err.splitlines():
        if "Stream #" not in line:
            continue
        if ": Video:" in line:
            wm = re.search(r",\s*(\d{2,5})x(\d{2,5})(?:[\s,\[]|$)", line)
            if wm and not info["width"]:
                info["width"], info["height"] = int(wm.group(1)), int(wm.group(2))
        elif ": Audio:" in line:
            info["has_audio"] = True
        elif ": Subtitle:" in line:
            info["subtitle_streams"] += 1
    if not info["duration"] and not info["width"]:
        raise MediaError("ffmpeg 读不了这个文件：" + path + "\n" + err[-800:])
    return info


def extract_audio(video: str, wav: str, timeout: float = 1200) -> None:
    """抽成 16k 单声道 wav，给语音识别用。"""
    run(["-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav], timeout=timeout)


def extract_embedded_subtitle(video: str, out_srt: str, timeout: float = 600) -> bool:
    """把视频内嵌的第一条字幕轨导成 srt，成功返回 True。"""
    p = run(["-i", video, "-map", "0:s:0", "-c:s", "srt", out_srt], check=False, timeout=timeout)
    return p.returncode == 0
