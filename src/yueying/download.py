"""在线视频：用 yt-dlp 下载（≤720p 够抽帧用），顺带抓平台字幕，优先原语言。"""
import glob
import os

from . import ffm

VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".flv", ".m4a", ".mp3")
SUB_EXT = (".vtt", ".srt", ".json", ".ass")


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def _pick_sub_langs(info: dict) -> tuple:
    """返回 (要抓的语言列表, 是否自动字幕)。人工字幕全要；只有自动字幕时只要原语言那一条。"""
    manual = [k for k in (info.get("subtitles") or {}) if k != "live_chat"]
    if manual:
        return manual, False
    auto = info.get("automatic_captions") or {}
    if not auto:
        return [], False
    orig = [k for k in auto if k.endswith("-orig")]
    if orig:
        return orig[:1], True
    lang = info.get("language")
    if lang and lang in auto:
        return [lang], True
    for k in auto:
        if k.startswith(("en", "zh")):
            return [k], True
    return [next(iter(auto))], True


def list_entries(url: str, cookies_from_browser=None, log=print) -> list:
    """链接是分 P / 合集 / 播放列表时，列出里面每个视频的链接和标题；普通链接就返回它自己。"""
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "noplaylist": False, "extract_flat": "in_playlist"}
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False)
    if info.get("_type") != "playlist" or not info.get("entries"):
        return [{"url": url, "title": info.get("title") or ""}]
    out = []
    for e in info["entries"]:
        u = e.get("webpage_url") or e.get("url")
        if u:
            out.append({"url": u, "title": e.get("title") or ""})
    return out or [{"url": url, "title": info.get("title") or ""}]


def download(url: str, out_dir: str, cookies_from_browser=None, log=print) -> dict:
    import yt_dlp

    os.makedirs(out_dir, exist_ok=True)
    common = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "ffmpeg_location": ffm.ffmpeg_exe(),  # 传完整路径：自带的 ffmpeg 文件名带版本号，传目录 yt-dlp 找不到
        "playlist_items": "1", "noplaylist": True,
    }
    if cookies_from_browser:
        common["cookiesfrombrowser"] = (cookies_from_browser,)

    log("  读取视频信息…")
    with yt_dlp.YoutubeDL(common) as y:
        info = y.extract_info(url, download=False)
    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]
    langs, auto = _pick_sub_langs(info)
    log(f"  《{info.get('title', '')}》 时长 {int(info.get('duration') or 0)} 秒；平台字幕：{','.join(langs) or '无'}")

    last = {"pct": -1}

    def hook(d):
        if d.get("status") != "downloading":
            return
        tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        if tot:
            pct = int(d.get("downloaded_bytes", 0) * 100 / tot)
            if pct // 20 != last["pct"] // 20:
                last["pct"] = pct
                log(f"  下载 {pct}%")

    opts = dict(common)
    opts.update({
        "outtmpl": os.path.join(out_dir, "source.%(ext)s"),
        "format": "bv*[height<=720]+ba/b[height<=720]/bv*+ba/b",
        "merge_output_format": "mp4",
        "writesubtitles": bool(langs) and not auto,
        "writeautomaticsub": bool(langs) and auto,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt/srt/best",
        "progress_hooks": [hook],
    })
    with yt_dlp.YoutubeDL(opts) as y:
        y.download([info.get("webpage_url") or url])

    files = glob.glob(os.path.join(out_dir, "source.*"))
    videos = [p for p in files if os.path.splitext(p)[1].lower() in VIDEO_EXT]
    subs = [p for p in files if os.path.splitext(p)[1].lower() in SUB_EXT]
    if not videos:
        raise RuntimeError("下载失败，没有得到视频文件")
    return {
        "video": videos[0], "subs": sorted(subs),
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "url": info.get("webpage_url") or url,
        "duration": info.get("duration") or 0,
        "description": (info.get("description") or "")[:2000],
        "chapters": [{"start": c.get("start_time", 0), "end": c.get("end_time", 0), "title": c.get("title", "")}
                     for c in (info.get("chapters") or [])],
        "language": info.get("language"),
    }
