"""query.py: transcript paging, search, frames/grids, overview (DONE body), shrink.

Runs against the reference outputs in test-media/out_zh and test-media/out_demo (0.1.x manifests)
plus small synthetic manifests for paging/ranking edge cases.  No ASR, no network, no ffmpeg.
"""
import io
import json
from pathlib import Path

import pytest

from yueying import query, store

ROOT = Path(__file__).resolve().parents[1]
REF_ZH = ROOT / "test-media" / "out_zh"
REF_DEMO = ROOT / "test-media" / "out_demo"


def _load(folder: Path) -> dict:
    """Reference manifest with its recorded (Windows, absolute) paths rebased onto this checkout."""
    if not (folder / "manifest.json").is_file():
        pytest.skip(f"reference output missing: {folder}")
    from yueying.report import load_manifest
    return load_manifest(str(folder))


@pytest.fixture(scope="module")
def zh():
    return _load(REF_ZH)


@pytest.fixture(scope="module")
def demo():
    return _load(REF_DEMO)


def _synthetic(n_paras=12, para_len=10.0, gap=3.0, frames=0, grids=None, text=None, **extra) -> dict:
    """n_paras cues separated by gaps > 2 s so report.paragraphs() keeps each cue as its own paragraph."""
    segs, t = [], 0.0
    for i in range(n_paras):
        segs.append({"start": round(t, 2), "end": round(t + para_len, 2),
                     "text": (text[i] if text else f"paragraph number {i + 1} spoken here")})
        t += para_len + gap
    fl = [{"index": i + 1, "time": float(i * 5 + 1), "file": f"C:/x/frames/f{i + 1:03d}.jpg"} for i in range(frames)]
    if grids is None:
        grids = [f"C:/x/grid_{g + 1:02d}.jpg" for g in range((frames + 8) // 9)]
    m = {"title": "Synthetic", "source": "C:/x/synthetic.mp4", "duration": t, "width": 1920, "height": 1080,
         "text_source": {"kind": "subtitle", "file": "C:/x/synthetic.zh.srt", "desc": "x"},
         "report": "C:/x/report.md", "transcript_srt": "C:/x/transcript.srt", "transcript_txt": "C:/x/transcript.txt",
         "grids": grids, "frames": fl, "segments": segs, "chapters": []}
    m.update(extra)
    return m


# ----------------------------------------------------------------------------- transcript_text
@pytest.mark.parametrize("folder", [REF_ZH, REF_DEMO])
def test_paragraphs_identical_to_transcript_txt(folder):
    m = _load(folder)
    text, nxt = query.transcript_text(m, 0.0, None, "paragraphs", 10 ** 6)
    expected = (folder / "transcript.txt").read_text(encoding="utf-8").splitlines()
    assert text.splitlines() == expected
    assert nxt is None


@pytest.mark.parametrize("folder", [REF_ZH, REF_DEMO])
def test_srt_identical_to_transcript_srt(folder):
    m = _load(folder)
    text, nxt = query.transcript_text(m, 0.0, None, "srt", 10 ** 6)
    assert nxt is None
    assert text + "\n\n" == (folder / "transcript.srt").read_text(encoding="utf-8")


def test_segments_format(zh):
    text, nxt = query.transcript_text(zh, 0.0, None, "segments", 10 ** 6)
    lines = text.splitlines()
    assert nxt is None and len(lines) == len(zh["segments"]) == 5
    assert lines[0] == "[00:00-00:03] " + zh["segments"][0]["text"]
    assert lines[-1].startswith("[00:17-00:21] ")
    with pytest.raises(ValueError):
        query.transcript_text(zh, 0.0, None, "bogus", 100)


def test_paging_covers_everything_once():
    m = _synthetic(12)
    full = query.transcript_text(m, 0.0, None, "paragraphs", 10 ** 6)[0].splitlines()
    assert len(full) == 12
    pages, start, guard = [], 0.0, 0
    while True:
        text, nxt = query.transcript_text(m, start, None, "paragraphs", 100)
        assert len(text) <= 100
        assert text.splitlines()                               # never an empty page
        pages += text.splitlines()
        if nxt is None:
            break
        # next_start is exactly the start of the first paragraph not returned
        assert nxt == m["segments"][len(pages)]["start"]
        # the mm:ss rounding an agent would pass back still continues at the right paragraph
        start = store.parse_time(store.fmt_time(nxt))
        guard += 1
        assert guard < 20
    assert pages == full


def test_next_start_and_range_semantics():
    m = _synthetic(5)                          # paragraphs at 0, 13, 26, 39, 52 (10 s each)
    text, nxt = query.transcript_text(m, 0.0, None, "paragraphs", 1)
    assert text.splitlines() == ["[00:00] paragraph number 1 spoken here"]   # always at least one item
    assert nxt == 13.0
    # start inside paragraph 2 -> it is included (has > 1 s left); end excludes items starting at/after it
    text, nxt = query.transcript_text(m, 15.0, 39.0, "paragraphs", 10 ** 6)
    assert [l[:7] for l in text.splitlines()] == ["[00:13]", "[00:26]"]
    assert nxt is None
    # start within the last second of a paragraph -> that paragraph is skipped (rounding tolerance)
    text, _ = query.transcript_text(m, 22.5, None, "paragraphs", 10 ** 6)
    assert text.splitlines()[0].startswith("[00:26]")
    # beyond the end -> empty
    assert query.transcript_text(m, 500.0, None, "paragraphs", 10 ** 6) == ("", None)
    # an inverted or empty range is a caller error, never silently "to the end of the video"
    with pytest.raises(ValueError):
        query.transcript_text(m, 10.0, 5.0, "segments", 10 ** 6)
    with pytest.raises(ValueError):
        query.transcript_text(m, 10.0, 10.0, "paragraphs", 10 ** 6)
    # segments/srt page the same way, srt numbering follows the original cue index
    text, nxt = query.transcript_text(m, 26.0, None, "srt", 60)
    assert text.startswith("3\n00:00:26,000 --> 00:00:36,000\n")
    assert nxt == 39.0


def test_no_segments():
    m = _synthetic(0)
    assert query.transcript_text(m) == ("", None)
    assert query.search(m, "x") == []
    assert query.paragraphs_around(m, 3) == []
    assert query.paragraphs(m) == []


# ----------------------------------------------------------------------------- search
def test_search_ranking_and_numbers():
    texts = ["first we install Docker on the machine", "then docker compose up starts everything",
             "unrelated paragraph", "Compose files are YAML", "closing words"]
    m = _synthetic(5, frames=20, text=texts)          # 20 frames -> 3 grids
    hits = query.search(m, "docker compose")
    assert [h["time"] for h in hits] == [13.0, 0.0, 39.0]           # 2 terms > 1 term, then by time
    assert [h["matched"] for h in hits] == [2, 1, 1]
    top = hits[0]
    assert top["end"] == 23.0 and top["text"] == texts[1]
    assert top["frame_index"] == query.nearest_frame(m, 13.0)["index"] == 3
    assert top["grid_no"] == 1
    assert texts[0] in top["snippet"] and texts[1] in top["snippet"] and texts[2] in top["snippet"]
    assert texts[3] not in top["snippet"]                            # 39 s is outside +-15 s
    # hit near the end lands on the last grid
    last = query.search(m, "closing")[0]
    assert last["time"] == 52.0 and last["frame_index"] == 11 and last["grid_no"] == 2
    # limit, context, case-insensitivity, no hits
    assert len(query.search(m, "docker compose", limit=1)) == 1
    assert query.search(m, "DOCKER", context_s=0)[0]["snippet"] == texts[0]
    assert query.search(m, "kubernetes") == []
    assert query.search(m, "   ") == []


def test_search_reference_cjk(zh, demo):
    hits = query.search(zh, "安装")
    assert len(hits) == 1 and hits[0]["time"] == 0.0 and hits[0]["frame_index"] == 1 and hits[0]["grid_no"] == 1
    assert "安装" in hits[0]["snippet"]
    assert query.search(demo, "桌面")[0]["matched"] == 1
    assert query.search(demo, "不存在的词") == []


# ----------------------------------------------------------------------------- frames / grids
def test_nearest_frame_and_grids(zh):
    assert query.nearest_frame(zh, 9.0)["index"] == 3            # 8.0 is closer than 10.5
    assert query.nearest_frame(zh, 0)["index"] == 1
    assert query.nearest_frame(zh, 999)["index"] == 8
    assert query.nearest_frame(zh, 2.9)["index"] == 1            # tie-break-free: 1.5 vs 4.5
    assert query.nearest_frame({"frames": []}, 1) is None
    assert query.grid_no_for_frame(zh, 1) == 1 and query.grid_no_for_frame(zh, 8) == 1
    assert query.grid_no_for_frame(zh, 99) is None
    g = query.grid_ranges(zh)
    assert g == [{"no": 1, "file": zh["grids"][0], "first_index": 1, "last_index": 8, "start": 1.5, "end": 22.5}]

    m = _synthetic(0, frames=20)
    assert query.grid_no_for_frame(m, 9) == 1 and query.grid_no_for_frame(m, 10) == 2 and query.grid_no_for_frame(m, 20) == 3
    r = query.grid_ranges(m)
    assert [(x["no"], x["first_index"], x["last_index"]) for x in r] == [(1, 1, 9), (2, 10, 18), (3, 19, 20)]
    assert r[1]["start"] == 46.0 and r[1]["end"] == 86.0
    # grid count shorter than the frame list -> no sheet number for the orphan frames
    m["grids"] = m["grids"][:1]
    assert query.grid_no_for_frame(m, 10) is None
    assert query.grid_ranges({"grids": ["C:/x/grid_01.jpg"], "frames": []})[0]["first_index"] is None


def test_paragraphs_around():
    m = _synthetic(6)                          # starts 0, 13, 26, 39, 52, 65
    starts = lambda ps: [p["start"] for p in ps]
    assert starts(query.paragraphs_around(m, 30)) == [13.0, 26.0, 39.0]
    assert starts(query.paragraphs_around(m, 0)) == [0.0, 13.0, 26.0]
    assert starts(query.paragraphs_around(m, 1000)) == [39.0, 52.0, 65.0]
    assert starts(query.paragraphs_around(m, 11.5)) == [0.0, 13.0, 26.0]     # gap: nearest is p1 (ends 10)
    assert starts(query.paragraphs_around(m, 30, n=1)) == [26.0]
    assert starts(query.paragraphs_around(m, 30, n=2)) == [26.0, 39.0]
    assert len(query.paragraphs_around(m, 30, n=50)) == 6


# ----------------------------------------------------------------------------- overview (DONE body)
def test_overview_reference(zh):
    out = query.overview(zh, 12000, video_id="23740085", cached=False, took=87.4)
    lines = out.splitlines()
    assert lines[0] == "DONE video_id=23740085 (cached: no, took 87 s)"
    assert lines[1] == "Title: test_zh"
    assert lines[2] == "Source: test_zh.mp4"
    assert lines[3] == "Duration: 00:24 · 1280x720"
    assert lines[4] == "Text: local speech recognition (faster-whisper large-v3-turbo on cuda), detected zh 100%, 5 segments"
    assert lines[5] == "Folder: " + str(REF_ZH)
    assert lines[6] == "Files: report.md, transcript.txt, transcript.srt, manifest.json, 1 contact sheet (grid_01.jpg), 8 keyframes in frames\\" \
        or lines[6] == "Files: report.md, transcript.txt, transcript.srt, manifest.json, 1 contact sheet (grid_01.jpg), 8 keyframes in frames/"
    assert lines[7] == "Contact sheets (see with get_frames):"
    assert lines[8] == "  grid 1: frames #1–#8, 00:02–00:22"
    assert lines[9] == "Transcript (paragraphs, [mm:ss]):"
    assert lines[10] == (REF_ZH / "transcript.txt").read_text(encoding="utf-8").splitlines()[0]
    assert lines[11] == 'Next: cite timestamps like (03:15); call get_frames(video="23740085") to see the visuals.'
    assert "TRUNCATED" not in out and "Uploader" not in out and "Chapters" not in out
    # cached hit without a duration
    assert query.overview(zh, 12000, video_id="23740085", cached=True).splitlines()[0] == "DONE video_id=23740085 (cached: yes)"


def test_overview_truncates_on_paragraph_boundary():
    m = _synthetic(12, frames=9)
    full = query.transcript_text(m, 0.0, None, "paragraphs", 10 ** 6)[0].splitlines()
    out = query.overview(m, 150, video_id="abcd1234", cached=True, took=0)
    lines = out.splitlines()
    i = lines.index("Transcript (paragraphs, [mm:ss]):")
    body = lines[i + 1:-1]
    assert body[-1].startswith("TRUNCATED at ")
    shown = body[:-1]
    assert shown and shown == full[:len(shown)]                    # whole paragraphs only, in order
    assert len("\n".join(shown)) <= 150
    nxt = m["segments"][len(shown)]["start"]
    at = store.fmt_time(nxt)
    assert body[-1] == f'TRUNCATED at {at} — continue with get_transcript(video="abcd1234", start="{at}")'
    # following the hint returns exactly the remainder
    rest, _ = query.transcript_text(m, store.parse_time(at), None, "paragraphs", 10 ** 6)
    assert rest.splitlines() == full[len(shown):]
    assert lines[-1] == 'Next: cite timestamps like (03:15); call get_frames(video="abcd1234") to see the visuals.'


def test_overview_urls_chapters_and_no_text():
    m = _synthetic(3, frames=20, source="https://www.bilibili.com/video/BV1GJ411x7h7", uploader="Someone",
                   chapters=[{"start": 0, "title": "Intro"}, {"start": 71.6, "title": "Setup"}],
                   text_source={"kind": "subtitle", "file": "C:/x/_download/source.zh-Hans.vtt", "desc": "x"})
    out = query.overview(m, 5000, video_id="deadbeef", cached=False, took=12, folder="C:/out/bili-x-deadbeef")
    lines = out.splitlines()
    assert "Uploader: Someone" in lines
    assert "Text: platform subtitles (zh-Hans, 3 cues)" in lines
    assert "Folder: C:/out/bili-x-deadbeef" in lines
    assert "Chapters: 00:00 Intro · 01:12 Setup" in lines
    assert any(l.startswith("Files: ") and "3 contact sheets (grid_01.jpg…grid_03.jpg), 20 keyframes in frames" in l for l in lines)
    assert "  grid 2: frames #10–#18, 00:46–01:26" in lines
    assert "  grid 3: frames #19–#20, 01:31–01:36" in lines

    # no text, no frames (mode=frames on a silent clip)
    m = _synthetic(0, frames=0, text_source={"kind": "none", "desc": "无"}, options={"no_asr": True},
                   width=0, height=0, duration=136.2)
    out = query.overview(m, 5000, video_id="00c0ffee", cached=False, took=3)
    lines = out.splitlines()
    assert "Text: none (skipped: mode=frames)" in lines
    assert "Duration: 02:16 · audio only" in lines
    assert any(l.endswith("manifest.json, no keyframes") for l in lines)
    assert "No transcript: no subtitles were found and speech recognition was skipped (mode=frames)." in lines
    assert "Transcript (paragraphs" not in out and "Contact sheets" not in out
    assert lines[-1] == "Next: cite timestamps like (03:15)."
    assert query.no_text_reason(m) == "no subtitles were found and speech recognition was skipped (mode=frames)"
    assert query.no_text_reason(_synthetic(2)) == ""


def test_text_description_variants():
    base = _synthetic(2, source="C:/v/clip.mp4")
    assert query.text_description(base) == "subtitle file synthetic.zh.srt (zh, 2 cues)"
    base["text_source"] = {"kind": "subtitle", "file": "C:/out/embedded.srt"}
    assert query.text_description(base) == "embedded subtitles (2 cues)"
    base["text_source"] = {"kind": "subtitle", "file": "C:/out/x.vtt", "language": "en"}
    base["source"] = "https://youtu.be/abc"
    assert query.text_description(base) == "platform subtitles (en, 2 cues)"
    base["text_source"] = {"kind": "asr", "model": "small", "device": "cpu", "language": "en", "language_probability": 0.87}
    assert query.text_description(base) == "local speech recognition (faster-whisper small on cpu), detected en 87%, 2 segments"
    base["text_source"] = {"kind": "none"}
    base["segments"] = []
    assert query.text_description(base) == "none"
    base["has_audio"] = False
    assert query.text_description(base) == "none (no audio)"
    assert query.no_text_reason(base) == "the video has no audio track"


# ----------------------------------------------------------------------------- shrink
@pytest.mark.parametrize("folder", [REF_ZH, REF_DEMO])
def test_shrink_grid_under_220kb(folder):
    from PIL import Image
    m = _load(folder)
    grid = Path(m["grids"][0])
    assert grid.name == "grid_01.jpg" and grid.is_file()
    before = grid.stat().st_mtime_ns
    data = query.shrink(grid, 1280, 72)
    assert isinstance(data, bytes) and data[:3] == b"\xff\xd8\xff"
    assert len(data) < 220 * 1024
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == "JPEG" and im.mode == "RGB" and max(im.size) <= 1280
    small = query.shrink(str(grid), 640, 80)
    with Image.open(io.BytesIO(small)) as im:
        assert max(im.size) <= 640
    assert len(small) < len(data)
    assert grid.stat().st_mtime_ns == before                      # original untouched
    # single keyframe at the get_frame_at defaults is comfortably small too
    frame = query.shrink(m["frames"][0]["file"], 960, 80)
    assert len(frame) < 220 * 1024


def test_overview_shows_notes(zh):
    m = dict(zh)
    m["notes"] = ["scene detection timed out; keyframes are evenly spaced instead of at scene changes"]
    lines = query.overview(m, 12000, video_id="23740085", cached=True).splitlines()
    assert lines[7] == "Note: scene detection timed out; keyframes are evenly spaced instead of at scene changes"
    assert lines[8] == "Contact sheets (see with get_frames):"
