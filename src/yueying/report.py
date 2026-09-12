"""把文字稿和关键帧整理成 report.md / transcript.srt / transcript.txt / manifest.json。

Report labels exist in zh (CLI default, byte-identical to 0.1.x) and en (`ui_lang="en"`, used by the MCP server).
"""
import datetime
import json
import os

from . import __version__
from .frames import fmt_time

MANIFEST_SCHEMA = 2

# Label / template table. zh strings are the 0.1.x strings verbatim — they are a parsing contract for
# existing users' report.md files; only ever ADD keys here.
_T = {
    "zh": {
        "source": "- 来源：{src}",
        "author": "- 作者：{author}",
        "duration": "- 时长：{dur}，分辨率 {w}x{h}",
        "text_source": "- 文字来源：{desc}",
        "frames": "- 关键帧：{n} 张（frames/），总览图 {g} 张（每张最多 9 格，左下角黄字是编号和时间）",
        "files": "- 其他文件：transcript.srt（带时间轴）、transcript.txt（按段落）、manifest.json",
        "hint": "> 用法提示：先看下面的总览图了解画面走向，需要看清某一刻的细节（代码、PPT、界面）再打开对应的单帧大图；文字稿按段落带时间戳，可与画面对照。",
        "description": "## 简介",
        "chapters": "## 章节",
        "overview": "## 画面总览",
        "grid": "- {file}：第 {a}–{b} 帧，{t1} ~ {t2}",
        "frame_list": "## 关键帧清单",
        "table_head": "| # | 时间 | 文件 |",
        "transcript": "## 文字稿",
        "no_text": "（没有文字：视频无声、无字幕，或已跳过语音识别）",
        "src_none": "无",
        "src_subtitle": "字幕文件 {name}（{n} 条）",
        "src_subtitle_short": "字幕文件 {name}",
        "src_asr": "本地语音识别 faster-whisper {model}（{device}），检测语言 {lang}（{p:.0%}），{n} 段",
        "src_asr_short": "本地语音识别 faster-whisper {model}（{device}），检测语言 {lang}（{p:.0%}）",
    },
    "en": {
        "source": "- Source: {src}",
        "author": "- Author: {author}",
        "duration": "- Duration: {dur}, resolution {w}x{h}",
        "text_source": "- Text source: {desc}",
        "frames": "- Keyframes: {n} (frames/), contact sheets: {g} (up to 9 tiles each; the yellow label bottom-left is the frame number and time)",
        "files": "- Other files: transcript.srt (timed), transcript.txt (paragraphs), manifest.json",
        "hint": "> Tip: Read the contact sheets first, then open individual frames for details; match pictures to the transcript by timestamp.",
        "description": "## Description",
        "chapters": "## Chapters",
        "overview": "## Visual overview",
        "grid": "- {file}: frames {a}–{b}, {t1} ~ {t2}",
        "frame_list": "## Keyframes",
        "table_head": "| # | Time | File |",
        "transcript": "## Transcript",
        "no_text": "(no transcript: no subtitles and speech recognition skipped)",
        "src_none": "none",
        "src_subtitle": "subtitle file {name} ({n} cues)",
        "src_subtitle_short": "subtitle file {name}",
        "src_asr": "local speech recognition, faster-whisper {model} on {device}, detected {lang} ({p:.0%}), {n} segments",
        "src_asr_short": "local speech recognition, faster-whisper {model} on {device}, detected {lang} ({p:.0%})",
    },
}


def _labels(ui_lang: str) -> dict:
    return _T.get(ui_lang) or _T["en"]


def describe_text_source(text_source: dict, ui_lang: str = "zh") -> str:
    """Human-readable one-liner for a manifest's text_source, in zh (0.1.x strings verbatim) or en.

    kinds: "subtitle" (file, count) / "asr" (model, device, language, language_probability, count) / "none".
    `count` is optional (0.1.x manifests lack it): the count clause is then omitted.
    """
    t = _labels(ui_lang)
    ts = text_source or {}
    kind = ts.get("kind") or "none"
    if kind == "subtitle":
        name = os.path.basename((ts.get("file") or "").replace("\\", "/"))
        n = ts.get("count")
        return t["src_subtitle"].format(name=name, n=n) if n is not None else t["src_subtitle_short"].format(name=name)
    if kind == "asr":
        kw = {"model": ts.get("model") or "", "device": ts.get("device") or "",
              "lang": ts.get("language") or "", "p": float(ts.get("language_probability") or 0.0)}
        n = ts.get("count")
        return t["src_asr"].format(n=n, **kw) if n is not None else t["src_asr_short"].format(**kw)
    return t["src_none"]


def _srt_ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def paragraphs(segs: list, max_len: int = 45.0, gap: float = 2.0) -> list:
    """把零碎的字幕段合并成段落：停顿超过 gap 秒或一段超过 max_len 秒就换段。"""
    out = []
    cur = None
    for s in segs:
        if cur and (s["start"] - cur["end"] > gap or s["end"] - cur["start"] > max_len):
            out.append(cur)
            cur = None
        if cur is None:
            cur = {"start": s["start"], "end": s["end"], "text": s["text"]}
        else:
            cur["text"] += _joiner(cur["text"], s["text"]) + s["text"]
            cur["end"] = s["end"]
    if cur:
        out.append(cur)
    return out


_PUNCT = "。！？，、；：…—」』）,.!?;:"


def _joiner(prev: str, nxt: str) -> str:
    """两段之间放什么：已有标点就不加；中日文补个逗号；其他语言补空格。"""
    if not prev or prev[-1] in _PUNCT or nxt[:1] in _PUNCT:
        return ""
    if _cjk(prev[-1]) or _cjk(nxt[:1]):
        return "，"
    return " "


def _cjk(ch: str) -> bool:
    return bool(ch) and ("　" <= ch <= "鿿" or "가" <= ch <= "힯" or "＀" <= ch <= "￯")


def write_all(out_dir: str, meta: dict, segs: list, frames: list, grids: list, text_source: dict, *,
              ui_lang: str = "zh", options: dict | None = None, notes: list[str] | None = None) -> dict:
    """Write transcript.srt / transcript.txt / report.md and, LAST, manifest.json; returns the manifest.

    Manifest keys (0.1.x, untouched): title source duration width height text_source report transcript_srt
    transcript_txt grids frames segments chapters — all paths absolute.
    Additive since 0.2.0: schema=2, yueying_version, created_at (ISO 8601, local tz), ui_lang, options, out_dir,
    uploader, and `notes` (English, only present when the run degraded somewhere, e.g. scene detection timed out).
    """
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    t = _labels(ui_lang)
    ui_lang = "zh" if t is _T["zh"] else "en"
    text_source = dict(text_source or {"kind": "none"})
    text_source.setdefault("desc", describe_text_source(text_source, ui_lang))
    rel = lambda p: os.path.relpath(p, out_dir).replace("\\", "/")

    with open(os.path.join(out_dir, "transcript.srt"), "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{_srt_ts(s['start'])} --> {_srt_ts(s['end'])}\n{s['text']}\n\n")
    paras = paragraphs(segs)
    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as f:
        for p in paras:
            f.write(f"[{fmt_time(p['start'])}] {p['text']}\n")

    lines = [f"# {meta.get('title') or os.path.basename(meta.get('source', ''))}", ""]
    lines.append(t["source"].format(src=meta.get("url") or meta.get("source", "")))
    if meta.get("uploader"):
        lines.append(t["author"].format(author=meta["uploader"]))
    lines.append(t["duration"].format(dur=fmt_time(meta.get("duration", 0)), w=meta.get("width", 0), h=meta.get("height", 0)))
    lines.append(t["text_source"].format(desc=text_source.get("desc") or t["src_none"]))
    lines.append(t["frames"].format(n=len(frames), g=len(grids)))
    lines.append(t["files"])
    lines.append("")
    lines.append(t["hint"])
    lines.append("")

    if meta.get("description"):
        lines += [t["description"], "", meta["description"].strip(), ""]
    if meta.get("chapters"):
        lines += [t["chapters"], ""]
        for c in meta["chapters"]:
            lines.append(f"- [{fmt_time(c['start'])}] {c['title']}")
        lines.append("")

    if grids:
        lines += [t["overview"], ""]
        for g in grids:
            lines.append(t["grid"].format(file=rel(g["file"]), a=g["frames"][0], b=g["frames"][-1],
                                          t1=fmt_time(g["from"]), t2=fmt_time(g["to"])))
        lines.append("")
    if frames:
        lines += [t["frame_list"], "", t["table_head"], "|---|---|---|"]
        for fr in frames:
            lines.append(f"| {fr['index']} | {fmt_time(fr['time'])} | {rel(fr['file'])} |")
        lines.append("")

    lines += [t["transcript"], ""]
    if paras:
        for p in paras:
            lines.append(f"[{fmt_time(p['start'])}] {p['text']}")
            lines.append("")
    else:
        lines.append(t["no_text"])
        lines.append("")

    report = os.path.join(out_dir, "report.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    manifest = {
        "title": meta.get("title"), "source": meta.get("url") or meta.get("source"),
        "duration": meta.get("duration"), "width": meta.get("width"), "height": meta.get("height"),
        "text_source": text_source, "report": report,
        "transcript_srt": os.path.join(out_dir, "transcript.srt"),
        "transcript_txt": os.path.join(out_dir, "transcript.txt"),
        "grids": [g["file"] for g in grids],
        "frames": [{"index": f["index"], "time": f["time"], "file": f["file"]} for f in frames],
        "segments": segs, "chapters": meta.get("chapters") or [],
        # ---- additive since 0.2.0 (cli.py filters these out of the --json stdout line) ----
        "schema": MANIFEST_SCHEMA,
        "yueying_version": __version__,
        "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "ui_lang": ui_lang,
        "options": dict(options or {}),
        "out_dir": out_dir,
        "uploader": meta.get("uploader") or "",     # URLs only (report.md prints it too); "" for local files
    }
    if notes:
        manifest["notes"] = [str(n) for n in notes]
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def _dir_of(path: str) -> str:
    """dirname that understands both separators (manifests may come from another OS)."""
    p = (path or "").replace("\\", "/")
    return p.rsplit("/", 1)[0] if "/" in p else ""


def _rebase(path: str, base: str, folder: str, sub: str = "") -> str:
    """Point an absolute path recorded under `base` at the same file under `folder`.

    Rebased when the folder differs from the recorded base (moved/copied folder) or the path no longer
    exists; a path outside `base` that does not exist falls back to `folder/<sub>/<basename>`.
    """
    if not path:
        return path
    p = path.replace("\\", "/")
    b = base.replace("\\", "/").rstrip("/") + "/" if base else ""
    same = bool(base) and os.path.normcase(os.path.normpath(base)) == os.path.normcase(os.path.normpath(folder))
    if b and p.lower().startswith(b.lower()) and not same:
        rel = p[len(b):]
    elif not os.path.exists(path):
        rel = (sub + "/" if sub else "") + p.rsplit("/", 1)[-1]
    else:
        return path
    return os.path.normpath(os.path.join(folder, *rel.split("/")))


def load_manifest(folder: str) -> dict:
    """Read <folder>/manifest.json (a path to the file itself is accepted too).

    Paths (report, transcript_srt, transcript_txt, grids[], frames[].file) are rebased onto `folder` when
    the folder was moved or copied, or the recorded files are gone — also for 0.1.x manifests, which have
    no out_dir (the report's folder is used instead). Sets out_dir to the actual folder and fills
    schema=1 / empty lists for 0.1.x manifests. Raises FileNotFoundError when there is no manifest.json.
    """
    folder = os.path.abspath(folder)
    if os.path.isfile(folder):
        path, folder = folder, os.path.dirname(folder)
    else:
        path = os.path.join(folder, "manifest.json")
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    base = m.get("out_dir") or _dir_of(m.get("report") or "") or folder
    for k in ("report", "transcript_srt", "transcript_txt"):
        if m.get(k):
            m[k] = _rebase(m[k], base, folder)
    m["grids"] = [_rebase(g, base, folder) for g in (m.get("grids") or [])]
    m["frames"] = m.get("frames") or []
    for fr in m["frames"]:
        if fr.get("file"):
            fr["file"] = _rebase(fr["file"], base, folder, sub="frames")
    m.setdefault("segments", [])
    m.setdefault("chapters", [])
    m.setdefault("text_source", {"kind": "none"})
    m.setdefault("schema", 1)
    m["out_dir"] = folder
    return m
