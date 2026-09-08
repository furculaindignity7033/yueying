"""字幕解析：srt / vtt / B站 json 都转成统一的 segments = [{start, end, text}]。"""
import json
import re

_TIME = re.compile(r"(\d+):(\d+):(\d+)[.,](\d+)|(\d+):(\d+)[.,](\d+)")


def _ts(s: str) -> float:
    m = _TIME.search(s)
    if not m:
        return 0.0
    if m.group(1) is not None:
        h, mi, se, ms = m.group(1), m.group(2), m.group(3), m.group(4)
    else:
        h, mi, se, ms = "0", m.group(5), m.group(6), m.group(7)
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms.ljust(3, "0")[:3]) / 1000


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)          # <c>、<00:00:01.000> 之类标签
    text = re.sub(r"\{\[^}]*\}", "", text)      # ass 样式
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return " ".join(text.split())


def parse_srt_vtt(content: str) -> list:
    segs = []
    block = []
    for raw in content.splitlines() + [""]:
        line = raw.strip("﻿").rstrip()
        if line.strip():
            block.append(line)
            continue
        if not block:
            continue
        # 找时间轴行
        idx = next((i for i, l in enumerate(block) if "-->" in l), None)
        if idx is not None:
            a, b = block[idx].split("-->", 1)
            text = _clean(" ".join(block[idx + 1:]))
            if text:
                segs.append({"start": _ts(a), "end": _ts(b), "text": text})
        block = []
    return _dedupe(segs)


def parse_bilibili_json(content: str) -> list:
    data = json.loads(content)
    body = data.get("body", data) if isinstance(data, dict) else data
    segs = []
    for it in body:
        text = _clean(str(it.get("content", "")))
        if text:
            segs.append({"start": float(it.get("from", 0)), "end": float(it.get("to", 0)), "text": text})
    return segs


def parse_file(path: str) -> list:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    stripped = content.lstrip("﻿").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return parse_bilibili_json(stripped)
    return parse_srt_vtt(content)


def _dedupe(segs: list) -> list:
    """YouTube 自动字幕是滚动式的，同一句会在相邻 cue 里重复出现，去掉。"""
    out = []
    for s in segs:
        if out:
            prev = out[-1]
            if s["text"] == prev["text"]:
                prev["end"] = max(prev["end"], s["end"])
                continue
            if s["text"].startswith(prev["text"]) and len(prev["text"]) > 8:
                s = {"start": prev["start"], "end": s["end"], "text": s["text"]}
                out.pop()
            elif prev["text"].endswith(s["text"]) and len(s["text"]) > 8:
                prev["end"] = max(prev["end"], s["end"])
                continue
        out.append(s)
    return out
