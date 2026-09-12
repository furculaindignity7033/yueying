"""End-to-end tests for the yueying MCP server.

* in-memory Client: tool schemas / annotations / ERROR texts (no child process)
* stdio round-trip: a real `python -m yueying.mcp_server` child, watch_video on test.mp4 (mode=frames, no ASR),
  cache hit, read tools, images, progress notifications, clean wire and clean stderr
* refresh kills a running job (in-memory)
* opt-in ASR round-trip on test_zh.mp4 (`pytest -m asr`)
"""
import logging
import os
import sys
import time
from pathlib import Path

import pytest

from mcp import Client, ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from yueying import mcp_server as S

pytestmark = pytest.mark.anyio

TOOLS = {"watch_video", "get_transcript", "search_transcript", "get_frames", "get_frame_at", "list_videos"}
READ_TOOLS = TOOLS - {"watch_video"}


def _text(result) -> str:
    return "\n".join(c.text for c in result.content if c.type == "text")


def _images(result):
    return [c for c in result.content if c.type == "image"]


# --------------------------------------------------------------------------- in-memory
async def test_in_memory_schema():
    async with Client(S.mcp, raise_exceptions=True) as c:
        tools = (await c.list_tools()).tools
        assert {t.name for t in tools} == TOOLS and len(tools) == 6
        by = {t.name: t for t in tools}
        assert by["watch_video"].input_schema["required"] == ["video"]
        assert by["search_transcript"].input_schema["required"] == ["video", "query"]
        assert by["get_frame_at"].input_schema["required"] == ["video", "time"]
        assert set(by["watch_video"].input_schema["properties"]) == {
            "video", "mode", "language", "model", "frame_interval_seconds", "cookies_from_browser",
            "output_dir", "refresh", "wait_seconds", "max_chars"}
        assert "ctx" not in by["watch_video"].input_schema["properties"]
        assert by["watch_video"].input_schema["properties"]["wait_seconds"]["default"] == 45
        assert by["watch_video"].input_schema["properties"]["wait_seconds"]["maximum"] == 1500
        assert by["get_frames"].input_schema["properties"]["count"]["maximum"] == 3
        for name in READ_TOOLS:
            ann = by[name].annotations
            assert ann is not None and ann.read_only_hint is True and ann.idempotent_hint is True, name
        wv = by["watch_video"].annotations
        assert wv.read_only_hint is False and wv.open_world_hint is True and wv.idempotent_hint is True
        assert wv.destructive_hint is False and wv.title == "Watch a video"
        for t in tools:
            assert t.description and "already-watched" in t.description or t.name in ("watch_video", "list_videos")
            assert t.output_schema is None, t.name                    # structured_output=False
        assert c.instructions == S.INSTRUCTIONS
        assert c.server_info.name == "yueying" and c.server_info.version == S.__version__


async def test_in_memory_error_texts(tmp_path):
    async with Client(S.mcp, raise_exceptions=True) as c:
        r = await c.call_tool("watch_video", {"video": "C:/nope.mp4"})
        assert _text(r).startswith("ERROR: file not found: C:/nope.mp4.")
        r = await c.call_tool("watch_video", {"video": str(tmp_path / "missing.mp4"), "wait_seconds": 0})
        assert _text(r).startswith("ERROR: file not found:")
        for name, extra in (("get_transcript", {}), ("search_transcript", {"query": "x"}),
                            ("get_frames", {}), ("get_frame_at", {"time": "1"})):
            r = await c.call_tool(name, {"video": "zzz", **extra})
            assert _text(r).startswith('ERROR: no processed video matches "zzz".'), name
        r = await c.call_tool("list_videos", {})
        assert "No processed videos in" in _text(r)
        # unknown 8-hex id and a folder without manifest.json are "no processed video" too
        r = await c.call_tool("get_transcript", {"video": "deadbeef"})
        assert _text(r).startswith("ERROR: no processed video matches")
        r = await c.call_tool("get_transcript", {"video": str(tmp_path)})
        assert _text(r).startswith("ERROR: no processed video matches")


async def test_in_memory_read_tools_on_reference(REF_ZH, out_root):
    """The read tools accept a results folder directly (a 0.1.x-style reference output)."""
    folder = str(REF_ZH.parent)
    async with Client(S.mcp, raise_exceptions=True) as c:
        r = await c.call_tool("get_transcript", {"video": folder, "start": "0", "max_chars": 1000})
        txt = _text(r)
        assert txt.startswith("Transcript of ") and "[00:" in txt and "source: local speech recognition" in txt
        r = await c.call_tool("get_transcript", {"video": folder, "format": "srt", "max_chars": 1000})
        assert "-->" in _text(r)
        r = await c.call_tool("get_transcript", {"video": folder, "start": "nope"})
        assert _text(r).startswith("ERROR: bad time")
        r = await c.call_tool("get_transcript", {"video": folder, "start": "59:00"})
        assert _text(r).startswith("ERROR: start=59:00 is beyond the last paragraph")
        r = await c.call_tool("get_transcript", {"video": folder, "start": "00:10", "end": "00:05"})
        assert _text(r) == "ERROR: end (00:05) must be after start (00:10)."
        r = await c.call_tool("get_transcript", {"video": folder, "start": "10", "end": "10"})
        assert _text(r).startswith("ERROR: end (00:10) must be after start")
        r = await c.call_tool("search_transcript", {"video": folder, "query": "视频"})
        txt = _text(r)
        assert "hit" in txt.splitlines()[0] and "- [00:" in txt
        r = await c.call_tool("search_transcript", {"video": folder, "query": "zzzzqqq"})
        assert _text(r).startswith('No hits for "zzzzqqq"')
        r = await c.call_tool("get_frames", {"video": folder, "count": 1, "max_width": 640})
        imgs = _images(r)
        assert len(imgs) == 1 and imgs[0].mime_type == "image/jpeg" and len(imgs[0].data) < 300000
        assert _text(r).startswith("grids 1–1 of ")
        r = await c.call_tool("get_frames", {"video": folder, "start": 99})
        assert _text(r).startswith("ERROR: start=99 is beyond the last contact sheet")
        r = await c.call_tool("get_frames", {"video": folder, "kind": "frames", "start": 99})
        assert _text(r).startswith("ERROR: start=99 is beyond the last keyframe")
        r = await c.call_tool("get_frame_at", {"video": folder, "time": "00:05"})
        txt = _text(r)
        assert len(_images(r)) == 1 and ("keyframe #" in txt or "extracted exactly" in txt)
        assert "Spoken around then:" in txt
        r = await c.call_tool("get_frame_at", {"video": folder, "time": "59:00"})
        assert _text(r).startswith("ERROR: time=59:00 is beyond the end of the video")


# --------------------------------------------------------------------------- stdio round-trip
def _server_params(root: Path) -> StdioServerParameters:
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "YUEYING_OUT_DIR": str(root)})
    return StdioServerParameters(command=sys.executable, args=["-m", "yueying.mcp_server"], env=env,
                                 cwd=str(root.parent))


async def test_stdio_roundtrip(TEST_MP4, out_root, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    errfile = tmp_path / "server_stderr.txt"
    progress = []

    async def on_progress(p, total, message):
        progress.append((p, total, message))

    with open(errfile, "w", encoding="utf-8") as errlog:
        async with stdio_client(_server_params(out_root), errlog=errlog) as (r, w):
            async with ClientSession(r, w, read_timeout_seconds=600) as s:
                init = await s.initialize()
                assert init.server_info.name == "yueying"
                assert init.instructions == S.INSTRUCTIONS
                tools = (await s.list_tools()).tools
                assert {t.name for t in tools} == TOOLS

                res = await s.call_tool("watch_video", {"video": str(TEST_MP4), "mode": "frames", "wait_seconds": 300},
                                        progress_callback=on_progress)
                txt = _text(res)
                assert txt.startswith("DONE video_id="), txt
                assert "cached: no" in txt.splitlines()[0] and "took" in txt.splitlines()[0]
                assert "Text: none (skipped: mode=frames)" in txt
                assert "contact sheet" in txt and "keyframes in frames" in txt
                assert "No transcript:" in txt and "get_frames(video=" in txt
                vid = txt.split("video_id=", 1)[1].split()[0]
                assert len(vid) == 8

                t0 = time.perf_counter()
                res = await s.call_tool("watch_video", {"video": str(TEST_MP4), "mode": "frames"})
                assert time.perf_counter() - t0 < 2.0
                txt2 = _text(res)
                assert txt2.startswith(f"DONE video_id={vid} (cached: yes)")
                # the same video by id, folder and path resolve to one entry
                entries = [d for d in out_root.iterdir() if d.is_dir()]
                assert len(entries) == 1 and entries[0].name.endswith("-" + vid)
                assert (entries[0] / "manifest.json").is_file() and not (entries[0] / ".job").exists()

                res = await s.call_tool("get_transcript", {"video": vid})
                assert _text(res).startswith("No transcript:")
                res = await s.call_tool("search_transcript", {"video": vid, "query": "hello"})
                assert _text(res).startswith("No transcript:")

                res = await s.call_tool("get_frames", {"video": vid, "count": 1})
                imgs = _images(res)
                assert len(imgs) == 1
                assert imgs[0].mime_type == "image/jpeg" and len(imgs[0].data) < 300000
                texts = [c.text for c in res.content if c.type == "text"]
                assert texts[0].startswith("grids 1–1 of 1 · ")
                assert texts[1].startswith("grid_01.jpg · frames #1–#") and texts[1].endswith("grid_01.jpg")
                assert res.content[1].type == "text" and res.content[2].type == "image"   # path before image

                res = await s.call_tool("get_frame_at", {"video": vid, "time": "00:05"})
                cap = _text(res)
                assert "extracted exactly" in cap and "frame at 00:05" in cap
                assert len(_images(res)) == 1 and len(_images(res)[0].data) < 300000
                assert (entries[0] / "frames" / "extra" / "at_00m05s.jpg").is_file()

                res = await s.call_tool("get_frames", {"video": str(entries[0]), "kind": "frames", "start": 1, "count": 3})
                assert len(_images(res)) <= 3 and all(len(i.data) < 300000 for i in _images(res))

                res = await s.call_tool("list_videos", {})
                txt = _text(res)
                assert txt.startswith("Processed videos in ") and vid in txt and "test" in txt
                assert str(entries[0]) in txt

    assert progress, "no progress notifications received"
    assert all(0.0 <= p <= 1.0 and t == 1.0 for p, t, _ in progress)
    assert progress[-1][0] == 1.0
    assert "Failed to parse JSONRPC message" not in caplog.text
    err = errfile.read_text(encoding="utf-8", errors="replace")
    assert "Traceback" not in err, err[-2000:]


async def test_refresh_kills_running_job(TEST_MP4, out_root):
    async with Client(S.mcp, raise_exceptions=True) as c:
        r = await c.call_tool("watch_video", {"video": str(TEST_MP4), "mode": "frames", "wait_seconds": 0})
        txt = _text(r)
        assert txt.startswith("RUNNING video_id="), txt
        assert "Call watch_video again with the same video" in txt
        vid = txt.split("video_id=", 1)[1].split()[0]
        job = S.JOBS[vid]
        assert job.alive
        r = await c.call_tool("watch_video", {"video": str(TEST_MP4), "mode": "frames", "refresh": True,
                                              "wait_seconds": 120})
        txt = _text(r)
        assert txt.startswith("DONE video_id=" + vid), txt
        assert "cached: no" in txt.splitlines()[0]
        assert job.state == "error" and job.kill_reason == "cancelled by refresh=true"
        assert job.proc is None or job.proc.poll() is not None      # no orphan child
        assert S.JOBS.get(vid) is None                                # done jobs are dropped from JOBS
        entries = [d for d in out_root.iterdir() if d.is_dir()]
        assert len(entries) == 1
        assert S._DONE_ENTRIES[vid] == entries[0]
        assert not (entries[0] / ".job").exists()
        # an entry that does not cover the requested mode is re-processed (frames -> full needs text)
        r = await c.call_tool("list_videos", {})
        assert vid in _text(r)


async def test_custom_output_dir_and_id_resolution(TEST_MP4, out_root, tmp_path):
    custom = tmp_path / "my results"
    async with Client(S.mcp, raise_exceptions=True) as c:
        r = await c.call_tool("watch_video", {"video": str(TEST_MP4), "mode": "frames", "wait_seconds": 120,
                                              "output_dir": str(custom)})
        txt = _text(r)
        assert txt.startswith("DONE video_id="), txt
        assert f"Folder: {custom.resolve()}" in txt
        assert (custom / "manifest.json").is_file()
        assert not any(out_root.iterdir()) if out_root.exists() else True     # nothing under the root
        vid = txt.split("video_id=", 1)[1].split()[0]
        # the id from the DONE line, the folder and the source path all work for the read tools
        for ref in (vid, str(custom), str(TEST_MP4)):
            r = await c.call_tool("get_frames", {"video": ref, "count": 1})
            assert len(_images(r)) == 1, ref
        r = await c.call_tool("watch_video", {"video": str(TEST_MP4), "mode": "frames", "output_dir": str(custom)})
        assert "(cached: yes)" in _text(r).splitlines()[0]
        # refresh=true on a user folder must reprocess WITHOUT touching the user's own files
        (custom / "thesis.docx").write_text("precious")
        (custom / "photos").mkdir()
        (custom / "photos" / "holiday.jpg").write_bytes(b"me")
        r = await c.call_tool("watch_video", {"video": str(TEST_MP4), "mode": "frames", "output_dir": str(custom),
                                              "refresh": True, "wait_seconds": 120})
        assert _text(r).startswith("DONE video_id=") and "cached: no" in _text(r).splitlines()[0]
        assert (custom / "thesis.docx").read_text() == "precious"
        assert (custom / "photos" / "holiday.jpg").read_bytes() == b"me"
        assert (custom / "manifest.json").is_file() and not (custom / ".job").exists()


# --------------------------------------------------------------------------- opt-in real ASR
@pytest.mark.asr
async def test_stdio_asr(TEST_ZH, out_root, tmp_path):
    errfile = tmp_path / "server_stderr.txt"
    with open(errfile, "w", encoding="utf-8") as errlog:
        async with stdio_client(_server_params(out_root), errlog=errlog) as (r, w):
            async with ClientSession(r, w, read_timeout_seconds=1200) as s:
                await s.initialize()
                res = await s.call_tool("watch_video", {"video": str(TEST_ZH), "model": "small", "language": "zh",
                                                        "wait_seconds": 900})
                txt = _text(res)
                assert txt.startswith("DONE video_id="), txt
                assert "Text: local speech recognition (faster-whisper small on" in txt
                assert "[00:" in txt
                vid = txt.split("video_id=", 1)[1].split()[0]
                res = await s.call_tool("get_transcript", {"video": vid, "start": "0", "max_chars": 1000})
                t2 = _text(res)
                assert t2.startswith("Transcript of test_zh 00:00–00:24") and "[00:" in t2
                res = await s.call_tool("search_transcript", {"video": vid, "query": "视频 工具 报告"})
                t3 = _text(res)
                assert t3.splitlines()[0].endswith("in test_zh:") and t3.count("- [") >= 1
                res = await s.call_tool("get_frame_at", {"video": vid, "time": "00:10"})
                assert "Spoken around then:" in _text(res) and len(_images(res)) == 1
    assert "Traceback" not in errfile.read_text(encoding="utf-8", errors="replace")
