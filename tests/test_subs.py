"""字幕解析：YouTube 滚动式自动字幕不能重复，普通 srt/vtt 的多行不能被吃掉。"""
from yueying import subs

# 真实的 YouTube 自动字幕片段（每条 cue 把上一行带下来，新词带内嵌时间标签）
ROLLING_VTT = """WEBVTT
Kind: captions
Language: en

00:00:02.639 --> 00:00:04.309 align:start position:0%
 
welcome<00:00:02.960><c> to</c><00:00:03.040><c> this</c><00:00:03.199><c> talk</c>

00:00:04.309 --> 00:00:04.319 align:start position:0%
welcome to this talk
 

00:00:04.319 --> 00:00:06.550 align:start position:0%
welcome to this talk
the<00:00:04.480><c> series</c><00:00:04.799><c> where</c><00:00:04.960><c> we</c><00:00:05.120><c> explain</c>

00:00:06.550 --> 00:00:06.560 align:start position:0%
the series where we explain
 

00:00:06.560 --> 00:00:08.230 align:start position:0%
the series where we explain
git<00:00:06.799><c> concepts</c>
"""

# 人工字幕：一条 cue 两行是一句话拆行，必须都留下
PLAIN_SRT = """1
00:00:01,000 --> 00:00:04,000
A branch is a version
of the working tree

2
00:00:04,000 --> 00:00:06,000
that you can move freely
"""

# 没有内嵌标签的滚动字幕（部分平台如此），靠“和上一条相同就丢掉”兜底
ROLLING_PLAIN_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
first line

00:00:03.000 --> 00:00:05.000
first line
second line
"""


def test_rolling_autocaptions_are_not_duplicated():
    segs = subs.parse_srt_vtt(ROLLING_VTT)
    texts = [s["text"] for s in segs]
    assert texts == ["welcome to this talk", "the series where we explain", "git concepts"]
    joined = " ".join(texts)
    assert joined.count("welcome to this talk") == 1
    assert joined.count("the series where we explain") == 1


def test_plain_multiline_cue_keeps_both_lines():
    segs = subs.parse_srt_vtt(PLAIN_SRT)
    assert [s["text"] for s in segs] == [
        "A branch is a version of the working tree",
        "that you can move freely",
    ]
    assert segs[0]["start"] == 1.0 and segs[0]["end"] == 4.0


def test_rolling_without_inline_tags_drops_the_carried_line():
    segs = subs.parse_srt_vtt(ROLLING_PLAIN_VTT)
    assert [s["text"] for s in segs] == ["first line", "second line"]


def test_identical_adjacent_cues_merge():
    srt = ("1\n00:00:01,000 --> 00:00:02,000\nsame text\n\n"
           "2\n00:00:02,000 --> 00:00:04,000\nsame text\n")
    segs = subs.parse_srt_vtt(srt)
    assert len(segs) == 1 and segs[0]["end"] == 4.0


# --------------------------------------------------------------------- vtt 的结构性行不能当正文
NUMERIC_VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
the build number is
2024

00:00:03.000 --> 00:00:05.000
3

00:00:05.000 --> 00:00:07.000
liftoff
"""

IDENTIFIER_VTT = """WEBVTT

NOTE this file was exported by some tool

intro
00:00:01.000 --> 00:00:03.000
Hello

verse-1
00:00:03.000 --> 00:00:05.000
World
"""

# 没有空行分隔的 srt（有些工具会这么导出）：末尾那个数字确实是下一条的序号
DENSE_SRT = """1
00:00:01,000 --> 00:00:02,000
first
2
00:00:02,000 --> 00:00:03,000
second
"""


def test_vtt_numeric_text_is_not_mistaken_for_an_srt_index():
    segs = subs.parse_srt_vtt(NUMERIC_VTT)
    assert [s["text"] for s in segs] == ["the build number is 2024", "3", "liftoff"]


def test_vtt_cue_identifiers_and_notes_do_not_leak_into_text():
    segs = subs.parse_srt_vtt(IDENTIFIER_VTT)
    assert [s["text"] for s in segs] == ["Hello", "World"]


def test_srt_without_blank_separators_still_drops_the_index():
    segs = subs.parse_srt_vtt(DENSE_SRT)
    assert [s["text"] for s in segs] == ["first", "second"]


def test_arrow_inside_caption_text_does_not_break_timing():
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nuse a --> b arrow here\n\n"
           "00:00:03.000 --> 00:00:05.000\nnext line\n")
    segs = subs.parse_srt_vtt(vtt)
    assert [s["text"] for s in segs] == ["use a --> b arrow here", "next line"]
    assert [s["start"] for s in segs] == [1.0, 3.0]
