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


# YouTube 自动字幕里新说的词带内嵌时间标签，如 welcome<00:00:02.960><c> to</c>
_INLINE_TIME = re.compile(r"<\d+:\d+:\d+[.,]\d+>")


def _new_lines(lines: list, prev_text: str) -> list:
    """滚动式字幕的每条 cue 都把上一行原样带下来，只留真正新说的那几行。

    YouTube 的自动字幕长这样（两行，第一行是上一句、第二行才是新词）：

        00:00:04.319 --> 00:00:06.550
        welcome to this get and gifts video
        the<00:00:04.480><c> series</c><00:00:04.799><c> where</c>

    带内嵌时间标签的行就是新词；没有标签的滚动字幕则靠“和上一条一样就丢掉”兜底。
    普通 srt/vtt 的多行字幕不会命中这两条规则，原样保留。
    """
    tagged = [l for l in lines if _INLINE_TIME.search(l)]
    if tagged:
        return tagged
    return [l for l in lines if _clean(l) and _clean(l) != prev_text]


def parse_srt_vtt(content: str) -> list:
    """按时间轴行切分：一条字幕从它的时间轴行开始，到第一个真正的空行为止。

    不能单纯按空行分块：YouTube 的自动字幕在 cue 内部放了只含一个空格的行（不是空行），
    那样会把字幕拦腰截断。也不能一路读到下一条时间轴行：中间的空行之后可能是下一条的
    序号（srt）、cue 标识或 NOTE 注释（vtt），会被当成正文。
    """
    lines = [l.strip("﻿") for l in content.splitlines()]
    marks = [i for i, l in enumerate(lines) if "-->" in l and _TIME.search(l.split("-->", 1)[0])]
    segs = []
    for n, i in enumerate(marks):
        a, b = lines[i].split("-->", 1)
        body = lines[i + 1:marks[n + 1] if n + 1 < len(marks) else len(lines)]
        if "" in body:
            body = body[:body.index("")]                   # 空行结束一条字幕
        elif n + 1 < len(marks) and body and body[-1].strip().isdigit():
            body.pop()                                     # 没有空行分隔的 srt：末尾是下一条的序号
        while body and not body[-1].strip():
            body.pop()
        text = _clean(" ".join(_new_lines(body, segs[-1]["text"] if segs else "")))
        if text:
            segs.append({"start": _ts(a), "end": _ts(b), "text": text})
        elif segs and body:
            segs[-1]["end"] = max(segs[-1]["end"], _ts(b))   # 整条都是带下来的旧内容
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
