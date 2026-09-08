"""把文字稿和关键帧整理成 report.md / transcript.srt / transcript.txt / manifest.json。"""
import json
import os

from .frames import fmt_time


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


def write_all(out_dir: str, meta: dict, segs: list, frames: list, grids: list, text_source: dict) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    rel = lambda p: os.path.relpath(p, out_dir).replace("\\", "/")

    with open(os.path.join(out_dir, "transcript.srt"), "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{_srt_ts(s['start'])} --> {_srt_ts(s['end'])}\n{s['text']}\n\n")
    paras = paragraphs(segs)
    with open(os.path.join(out_dir, "transcript.txt"), "w", encoding="utf-8") as f:
        for p in paras:
            f.write(f"[{fmt_time(p['start'])}] {p['text']}\n")

    lines = [f"# {meta.get('title') or os.path.basename(meta.get('source', ''))}", ""]
    lines.append(f"- 来源：{meta.get('url') or meta.get('source', '')}")
    if meta.get("uploader"):
        lines.append(f"- 作者：{meta['uploader']}")
    lines.append(f"- 时长：{fmt_time(meta.get('duration', 0))}，分辨率 {meta.get('width', 0)}x{meta.get('height', 0)}")
    lines.append(f"- 文字来源：{text_source.get('desc', '无')}")
    lines.append(f"- 关键帧：{len(frames)} 张（frames/），总览图 {len(grids)} 张（每张最多 9 格，左下角黄字是编号和时间）")
    lines.append(f"- 其他文件：transcript.srt（带时间轴）、transcript.txt（按段落）、manifest.json")
    lines.append("")
    lines.append("> 用法提示：先看下面的总览图了解画面走向，需要看清某一刻的细节（代码、PPT、界面）再打开对应的单帧大图；文字稿按段落带时间戳，可与画面对照。")
    lines.append("")

    if meta.get("description"):
        lines += ["## 简介", "", meta["description"].strip(), ""]
    if meta.get("chapters"):
        lines += ["## 章节", ""]
        for c in meta["chapters"]:
            lines.append(f"- [{fmt_time(c['start'])}] {c['title']}")
        lines.append("")

    if grids:
        lines += ["## 画面总览", ""]
        for g in grids:
            lines.append(f"- {rel(g['file'])}：第 {g['frames'][0]}–{g['frames'][-1]} 帧，{fmt_time(g['from'])} ~ {fmt_time(g['to'])}")
        lines.append("")
    if frames:
        lines += ["## 关键帧清单", "", "| # | 时间 | 文件 |", "|---|---|---|"]
        for fr in frames:
            lines.append(f"| {fr['index']} | {fmt_time(fr['time'])} | {rel(fr['file'])} |")
        lines.append("")

    lines += ["## 文字稿", ""]
    if paras:
        for p in paras:
            lines.append(f"[{fmt_time(p['start'])}] {p['text']}")
            lines.append("")
    else:
        lines.append("（没有文字：视频无声、无字幕，或已跳过语音识别）")
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
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest
