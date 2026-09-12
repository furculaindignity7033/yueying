"""Read-side helpers over a loaded manifest: transcript paging, search, frames/grids, the DONE
overview and image shrinking.

Pure functions over the manifest dict (0.1.x or 0.2.0 format).  Paragraphs always come from
``report.paragraphs()`` with its defaults so timestamps match transcript.txt exactly.
``report`` (which pulls in Pillow through frames.py) and Pillow itself are imported lazily so
that importing this module costs nothing at server start-up.
"""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

from .store import fmt_time, is_url

FRAMES_PER_GRID = 9            # frames.make_grids: 3 x 3 tiles per contact sheet, in order
_EDGE_TOLERANCE = 1.0          # seconds; absorbs mm:ss rounding of next_start / user input
_SUB_LANG_RE = re.compile(r"\.([A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*)\.(?:vtt|srt|ass|json)$")


# --------------------------------------------------------------------------- basics
def _segments(manifest: dict) -> list:
    return [s for s in (manifest.get("segments") or []) if isinstance(s, dict)]


def paragraphs(manifest: dict) -> list:
    """report.paragraphs(segments) with default max_len/gap -> same lines as transcript.txt."""
    segs = _segments(manifest)
    if not segs:
        return []
    from .report import paragraphs as _paras   # lazy: report -> frames -> PIL
    return _paras(segs)


def _srt_ts(t: float) -> str:
    ms = int(round(float(t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _in_range(item: dict, start: float, end: float | None) -> bool:
    """An item counts when it starts at/after `start` or still has > 1 s to run at `start`
    (tolerates mm:ss rounding), and starts before `end`."""
    if end is not None and item["start"] >= end:
        return False
    return item["start"] >= start or item["end"] - start > _EDGE_TOLERANCE


def _render(item: dict, fmt: str, n: int) -> str:
    if fmt == "segments":
        return f"[{fmt_time(item['start'])}-{fmt_time(item['end'])}] {item['text']}"
    if fmt == "srt":
        return f"{n}\n{_srt_ts(item['start'])} --> {_srt_ts(item['end'])}\n{item['text']}"
    return f"[{fmt_time(item['start'])}] {item['text']}"


# --------------------------------------------------------------------------- transcript
def transcript_text(manifest: dict, start: float = 0.0, end: float | None = None,
                    fmt: str = "paragraphs", max_chars: int = 8000) -> tuple[str, float | None]:
    """Transcript between start and end (seconds) as text, cut on an item boundary.

    fmt: "paragraphs" -> ``[mm:ss] text`` (transcript.txt lines); "segments" ->
    ``[mm:ss-mm:ss] text`` per cue; "srt" -> subtitle blocks (numbered as in transcript.srt).
    Returns (text, next_start): next_start is the start time of the first item that did not
    fit (pass it back as `start` to continue) or None when everything was returned.  At least
    one item is always returned when any item is in range.  Raises ValueError for an unknown
    fmt or when end <= start (the range is never silently widened to the end of the video).
    """
    if fmt not in ("paragraphs", "segments", "srt"):
        raise ValueError(f"unknown format {fmt!r}")
    start = max(0.0, float(start or 0))
    if end is not None:
        end = float(end)
        if end <= start:
            raise ValueError(f"end ({end:g} s) must be after start ({start:g} s)")
    items = paragraphs(manifest) if fmt == "paragraphs" else _segments(manifest)
    sep = "\n\n" if fmt == "srt" else "\n"
    chosen, total = [], 0
    for n, item in enumerate(items, 1):
        if not _in_range(item, start, end):
            continue
        line = _render(item, fmt, n)
        if chosen and total + len(sep) + len(line) > max_chars:
            return sep.join(chosen), float(item["start"])
        chosen.append(line)
        total += len(line) + (len(sep) if len(chosen) > 1 else 0)
    return sep.join(chosen), None


def paragraphs_around(manifest: dict, t: float, n: int = 3) -> list[dict]:
    """Up to n paragraphs centred on the one spoken at time t (or the nearest one)."""
    paras = paragraphs(manifest)
    if not paras or n <= 0:
        return []
    t = float(t)
    best = min(range(len(paras)),
               key=lambda i: (0.0 if paras[i]["start"] <= t <= paras[i]["end"]
                              else min(abs(paras[i]["start"] - t), abs(paras[i]["end"] - t)), i))
    lo = max(0, best - (n - 1) // 2)
    hi = min(len(paras), lo + n)
    lo = max(0, hi - n)
    return [dict(p) for p in paras[lo:hi]]


# --------------------------------------------------------------------------- search
def search(manifest: dict, query: str, context_s: float = 15.0, limit: int = 10) -> list[dict]:
    """Case-insensitive substring search, one hit per matching paragraph.

    Terms = query.split(); a paragraph matches when any term occurs in it.  Ranked by number of
    distinct terms matched (desc) then time (asc).  Each hit: {time, end, text, snippet, matched,
    frame_index, grid_no}; snippet = paragraphs within +-context_s merged.
    """
    terms = [t.lower() for t in (query or "").split() if t.strip()]
    paras = paragraphs(manifest)
    if not terms or not paras:
        return []
    scored = []
    for i, p in enumerate(paras):
        low = p["text"].lower()
        matched = {t for t in terms if t in low}
        if matched:
            scored.append((-len(matched), p["start"], i))
    scored.sort()
    hits = []
    for neg, _t, i in scored[:max(1, int(limit))]:
        p = paras[i]
        ctx = [q for q in paras if q["end"] >= p["start"] - context_s and q["start"] <= p["end"] + context_s]
        fr = nearest_frame(manifest, p["start"])
        hits.append({
            "time": float(p["start"]), "end": float(p["end"]), "text": p["text"],
            "snippet": _merge([q["text"] for q in ctx]),
            "matched": -neg,
            "frame_index": fr["index"] if fr else None,
            "grid_no": grid_no_for_frame(manifest, fr["index"]) if fr else None,
        })
    return hits


def _merge(texts: list) -> str:
    out = ""
    for t in texts:
        t = (t or "").strip()
        if not t:
            continue
        if out and not out[-1].isspace():
            out += "" if (_cjk(out[-1]) or _cjk(t[0])) else " "
        out += t
    return out


def _cjk(ch: str) -> bool:
    return bool(ch) and ("　" <= ch <= "鿿" or "가" <= ch <= "힯" or "＀" <= ch <= "￯")


# --------------------------------------------------------------------------- frames / grids
def frames(manifest: dict) -> list:
    return [f for f in (manifest.get("frames") or []) if isinstance(f, dict)]


def nearest_frame(manifest: dict, t: float) -> dict | None:
    """The keyframe dict {index, time, file} closest to time t, or None without frames."""
    fl = frames(manifest)
    if not fl:
        return None
    t = float(t)
    return min(fl, key=lambda f: (abs(float(f.get("time", 0)) - t), f.get("index", 0)))


def grid_no_for_frame(manifest: dict, index: int) -> int | None:
    """1-based contact-sheet number holding keyframe #index (9 per sheet, list order)."""
    fl = frames(manifest)
    for pos, f in enumerate(fl):
        if f.get("index") == index:
            no = pos // FRAMES_PER_GRID + 1
            grids = manifest.get("grids") or []
            return no if no <= len(grids) else None
    return None


def grid_ranges(manifest: dict) -> list[dict]:
    """[{no, file, first_index, last_index, start, end}] for every contact sheet."""
    fl = frames(manifest)
    out = []
    for i, g in enumerate(manifest.get("grids") or []):
        chunk = fl[i * FRAMES_PER_GRID:(i + 1) * FRAMES_PER_GRID]
        out.append({
            "no": i + 1, "file": str(g),
            "first_index": chunk[0]["index"] if chunk else None,
            "last_index": chunk[-1]["index"] if chunk else None,
            "start": float(chunk[0]["time"]) if chunk else None,
            "end": float(chunk[-1]["time"]) if chunk else None,
        })
    return out


# --------------------------------------------------------------------------- text-source wording
def text_description(manifest: dict) -> str:
    """English one-liner for the Text: line, e.g. 'platform subtitles (zh, 95 cues)'."""
    ts = manifest.get("text_source") or {}
    kind = ts.get("kind") or "none"
    n = len(_segments(manifest))
    if kind == "subtitle":
        name = os.path.basename(str(ts.get("file") or ""))
        lang = ts.get("language")
        if not lang:
            m = _SUB_LANG_RE.search(name)
            lang = m.group(1) if m else None
        if name.startswith("embedded"):
            what = "embedded subtitles"
        elif is_url(str(manifest.get("source") or "")):
            what = "platform subtitles"
        else:
            what = f"subtitle file {name}" if name else "subtitle file"
        count = ts.get("count") or n
        cues = f"{count} cue" + ("s" if count != 1 else "")
        return f"{what} ({lang}, {cues})" if lang else f"{what} ({cues})"
    if kind == "asr":
        lang = ts.get("language") or "?"
        prob = ts.get("language_probability")
        detected = f"detected {lang} {prob:.0%}" if isinstance(prob, (int, float)) else f"detected {lang}"
        count = ts.get("count") or n
        return (f"local speech recognition (faster-whisper {ts.get('model', '?')} on {ts.get('device', '?')}), "
                f"{detected}, {count} segment" + ("s" if count != 1 else ""))
    reason = no_text_reason(manifest, short=True)
    return f"none ({reason})" if reason else "none"


def no_text_reason(manifest: dict, short: bool = False) -> str:
    """Why there is no transcript ('' when there is one)."""
    ts = manifest.get("text_source") or {}
    if (ts.get("kind") or "none") != "none" and _segments(manifest):
        return ""
    opts = manifest.get("options") or {}
    if manifest.get("has_audio") is False:
        return "no audio" if short else "the video has no audio track"
    if opts.get("no_asr"):
        return "skipped: mode=frames" if short else "no subtitles were found and speech recognition was skipped (mode=frames)"
    if (ts.get("kind") or "none") != "none":
        return "empty" if short else "the subtitles/speech recognition produced no text"
    return "" if short else "no subtitles were found and no speech was recognised"


# --------------------------------------------------------------------------- overview (DONE)
def overview(manifest: dict, max_chars: int, *, video_id: str = "", cached: bool = False,
             took: float | None = None, folder: str | None = None) -> str:
    """The DONE reply of watch_video (spec 6.1).  max_chars limits the transcript part only."""
    vid = video_id or ""
    folder = folder or manifest.get("out_dir") or os.path.dirname(str(manifest.get("report") or "")) or ""
    head = f"cached: {'yes' if cached else 'no'}"
    if took is not None:
        head += f", took {int(round(took))} s"
    lines = [f"DONE video_id={vid} ({head})"]
    lines.append(f"Title: {manifest.get('title') or os.path.basename(str(manifest.get('source') or '')) or '(untitled)'}")
    src = str(manifest.get("source") or "")
    lines.append(f"Source: {src}")
    if manifest.get("uploader") and is_url(src):
        lines.append(f"Uploader: {manifest['uploader']}")
    w, h = manifest.get("width") or 0, manifest.get("height") or 0
    lines.append(f"Duration: {fmt_time(manifest.get('duration') or 0)} · " + (f"{w}x{h}" if w else "audio only"))
    lines.append(f"Text: {text_description(manifest)}")
    lines.append(f"Folder: {folder}")

    grids = list(manifest.get("grids") or [])
    fl = frames(manifest)
    files = ["report.md", "transcript.txt", "transcript.srt", "manifest.json"]
    if grids:
        names = [os.path.basename(str(g)) for g in grids]
        files.append(f"1 contact sheet ({names[0]})" if len(grids) == 1
                     else f"{len(grids)} contact sheets ({names[0]}…{names[-1]})")
    files.append(f"{len(fl)} keyframes in frames{os.sep}" if fl else "no keyframes")
    lines.append("Files: " + ", ".join(files))
    for note in manifest.get("notes") or []:
        lines.append(f"Note: {note}")

    chapters = [c for c in (manifest.get("chapters") or []) if isinstance(c, dict) and c.get("title")]
    if chapters:
        lines.append("Chapters: " + " · ".join(f"{fmt_time(c.get('start') or 0)} {c['title']}" for c in chapters))
    if grids:
        lines.append("Contact sheets (see with get_frames):")
        for g in grid_ranges(manifest):
            if g["first_index"] is None:
                lines.append(f"  grid {g['no']}: (empty)")
            else:
                lines.append(f"  grid {g['no']}: frames #{g['first_index']}–#{g['last_index']}, "
                             f"{fmt_time(g['start'])}–{fmt_time(g['end'])}")

    text, nxt = transcript_text(manifest, 0.0, None, "paragraphs", max_chars)
    if text:
        lines.append("Transcript (paragraphs, [mm:ss]):")
        lines.append(text)
        if nxt is not None:
            at = fmt_time(nxt)
            lines.append(f'TRUNCATED at {at} — continue with get_transcript(video="{vid}", start="{at}")')
    else:
        lines.append(f"No transcript: {no_text_reason(manifest)}."
                     + (" Use get_frames to see the frames." if grids else ""))
    nxt_line = "Next: cite timestamps like (03:15)"
    if grids:
        nxt_line += f'; call get_frames(video="{vid}") to see the visuals'
    lines.append(nxt_line + ".")
    return "\n".join(lines)


# --------------------------------------------------------------------------- images
def shrink(path, max_width: int, quality: int) -> bytes:
    """Re-encode an image as JPEG no wider/taller than max_width (RGB, optimize).  Original untouched."""
    from PIL import Image                      # lazy: keep server import light
    with Image.open(str(Path(path))) as im:
        im = im.convert("RGB")
        im.thumbnail((int(max_width), int(max_width)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=int(quality), optimize=True)
    return buf.getvalue()
