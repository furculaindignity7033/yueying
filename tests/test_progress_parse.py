"""mcp_server._parse_line over a real CLI log (tests/fixtures/cli_log_sample.txt), the Job runner replaying
that log through a child process, and the 9-class error classifier.

The fixture was captured with
    python -m yueying.cli test-media/test_zh.mp4 --out <tmp>/fx --json --ui-lang en
on a machine with a GPU and a cached large-v3-turbo model, so it contains the 模型 / 检测语言 / 识别进度 lines.
No ffmpeg, ASR or network is needed here.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yueying import mcp_server as S

FIXTURE = Path(__file__).parent / "fixtures" / "cli_log_sample.txt"


def _events():
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return lines, [(ln, S._parse_line(ln)) for ln in lines]


# --------------------------------------------------------------------------- _parse_line on the fixture
def test_fixture_stage_sequence():
    _, evs = _events()
    stages = [e["stage"] for _, e in evs if e and "stage" in e]
    assert stages == [1, 2, 3, 4]


def test_fixture_message_sequence():
    _, evs = _events()
    msgs = [e["message"] for _, e in evs if e and "message" in e]
    assert msgs[:4] == ["downloading / reading file", "looking for subtitles", "loading speech model",
                        "speech recognition"]
    asr = [m for m in msgs if m.startswith("speech recognition ") and m.endswith("%")]
    assert asr == ["speech recognition 14%", "speech recognition 37%", "speech recognition 49%",
                   "speech recognition 67%", "speech recognition 88%"]
    assert msgs[-2:] == ["detecting scene changes", "writing report"]


def test_fixture_pct_monotonic_and_bounded():
    _, evs = _events()
    pcts = [e["pct"] for _, e in evs if e and "pct" in e]
    assert pcts[0] == 0.0 and pcts[-1] == 1.0
    assert all(0.0 <= p <= 1.0 for p in pcts)
    assert all(b >= a for a, b in zip(pcts, pcts[1:]))
    assert 0.35 + 0.30 * 0.14 - 1e-9 <= pcts[pcts.index(next(p for p in pcts if p > 0.35))] <= 0.65


def test_fixture_done_and_manifest():
    lines, evs = _events()
    done = [e for _, e in evs if e and e.get("done")]
    assert len(done) == 1 and done[0]["pct"] == 1.0
    mani = [e["manifest"] for _, e in evs if e and "manifest" in e]
    assert len(mani) == 1
    m = mani[0]
    assert m["title"] == "test_zh" and m["duration"] == 24.0
    assert m["text_source"]["kind"] == "asr" and m["text_source"]["language"] == "zh"
    assert m["frames"] and m["grids"] and "segments" not in m           # the --json line drops segments
    # the JSON line is the last line and is the only line that parses as a manifest
    assert lines[-1].startswith("{")


def test_fixture_plain_lines_are_ignored():
    _, evs = _events()
    plain = [ln for ln, e in evs if e is None]
    assert any("时长" in ln for ln in plain)          # "      时长 00:24，..." carries no progress
    assert any(ln.startswith("报告：") for ln in plain)
    assert not any(ln.startswith("[") for ln in plain)
    assert not any(S._parse_line(ln) for ln in ("", "   ", "\n"))


def test_fixture_has_no_title_line_for_local_file():
    _, evs = _events()
    assert not any(e and "title" in e for _, e in evs)


# --------------------------------------------------------------------------- synthetic lines
@pytest.mark.parametrize("line, expect", [
    ("[1/4] 下载 https://www.bilibili.com/video/BV1xx", {"stage": 1, "pct": 0.0, "message": "downloading / reading file"}),
    ("  下载 45%", {"pct": 0.25 * 0.45, "message": "downloading video 45%"}),
    ("  下载 100%", {"pct": 0.25, "message": "downloading video 100%"}),
    ("  《我的 视频：标题》 时长 371 秒；平台字幕：zh-Hans", {"title": "我的 视频：标题"}),
    ("[2/4] 文字", {"stage": 2, "pct": 0.25, "message": "looking for subtitles"}),
    ("      用字幕 x.zh-Hans.vtt，95 条", {"pct": 0.60, "message": "using platform subtitles"}),
    ("  模型 small，设备 cpu (int8)；首次使用会下载模型，请耐心等待", {"pct": 0.30, "message": "loading speech model"}),
    ("  检测语言 en（置信度 98%）", {"pct": 0.35, "message": "speech recognition"}),
    ("  识别进度 50%", {"pct": 0.5, "message": "speech recognition 50%"}),
    ("[3/4] 画面", {"stage": 3, "pct": 0.65, "message": "detecting scene changes"}),
    ("  抽帧 10/40", {"pct": 0.70 + 0.25 * 0.25, "message": "extracting keyframes 10/40"}),
    ("  抽帧 40/40", {"pct": 0.95, "message": "extracting keyframes 40/40"}),
    ("[4/4] 写报告", {"stage": 4, "pct": 0.97, "message": "writing report"}),
    ("完成，用时 8 秒", {"pct": 1.0, "done": True}),
    ('{"title": "x", "frames": []}', {"manifest": {"title": "x", "frames": []}}),
])
def test_parse_line_synthetic(line, expect):
    got = S._parse_line(line)
    assert got is not None
    for k, v in expect.items():
        if isinstance(v, float):
            assert got[k] == pytest.approx(v)
        else:
            assert got[k] == v


@pytest.mark.parametrize("line", [
    "      时长 00:24，1280x720，有音轨，内嵌字幕 0 条",
    "      场景切换 2 处，抽 12 帧",
    "      去掉 6 张和前一帧几乎相同的画面，保留 6 张",
    "      第 3 帧抽取超时，跳过",
    "  yt-dlp: [download] Destination: x.mp4",
    "报告：C:\\x\\report.md",
    "[5/4] nope",
    "{not json",
    "[1, 2, 3]",
    "Traceback (most recent call last):",
])
def test_parse_line_plain(line):
    assert S._parse_line(line) is None


# --------------------------------------------------------------------------- Job replaying the fixture
def _replay_job(tmp_path, monkeypatch, *, create_manifest=True, model_cached=True):
    """Run a Job whose child just prints the fixture; the entry gets the fixture's manifest as manifest.json."""
    hub = tmp_path / "hub"
    if model_cached:
        snap = hub / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "abc"
        snap.mkdir(parents=True)
        (snap / "model.bin").write_bytes(b"x")
    else:
        hub.mkdir()
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    entry = tmp_path / "entry-deadbeef"
    entry.mkdir()
    if create_manifest:
        text = FIXTURE.read_text(encoding="utf-8")
        manifest = json.loads(text.splitlines()[-1])
        manifest["segments"] = []
        (entry / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    code = "import sys; sys.stdout.write(open(sys.argv[1], encoding='utf-8').read())"
    cmd = [sys.executable, "-X", "utf8", "-c", code, str(FIXTURE)]
    job = S.Job("deadbeef", "C:/fake/test_zh.mp4", entry, cmd, model="auto", device="auto")
    assert job.wait(60)
    return job


def test_job_replays_fixture_to_done(tmp_path, monkeypatch):
    job = _replay_job(tmp_path, monkeypatch)
    assert job.state == "done", job.lines
    assert job.rc == 0 and job.stage == 4 and job.pct == 1.0
    assert job.manifest and job.manifest["title"] == "test_zh"
    assert job.manifest["out_dir"] == str(job.entry)          # loaded via report.load_manifest
    assert job.proc is not None and job.proc.poll() == 0
    assert not (job.entry / ".job").exists()                  # marker cleared
    assert job.model_cached is True
    assert len(job.lines) >= 18 and job.took is not None and job.took >= 0
    # the "first run downloads" suffix is only added when the model is not cached
    job._apply({"message": "loading speech model"})
    assert job.message == "loading speech model"


def test_job_without_manifest_on_disk_is_an_error(tmp_path, monkeypatch):
    job = _replay_job(tmp_path, monkeypatch, create_manifest=False)
    assert job.state == "error" and job.rc == 0
    text = S.classify_error(job.lines, job.rc, job.kill_reason, job.source)
    assert text.startswith("ERROR: processing failed (exit code 0).")
    assert "log:" in text


def test_job_model_not_cached_message(tmp_path, monkeypatch):
    job = _replay_job(tmp_path, monkeypatch, model_cached=False)
    assert job.model_cached is False
    job._apply({"message": "loading speech model"})
    assert job.message == ("loading speech model (first run downloads ~480 MB (CPU) / ~1.6 GB (GPU), "
                           "this can take several minutes)")
    assert job.eta_text() == ""                                # no ETA without a cached model


def test_model_cache_status(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    assert S._model_cache_status("small", "auto") == (False, "~480 MB")
    assert S._model_cache_status("auto", "auto") == (False, "~480 MB (CPU) / ~1.6 GB (GPU)")
    snap = hub / "models--Systran--faster-whisper-small" / "snapshots" / "h"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x")
    assert S._model_cache_status("small", "auto") == (True, "~480 MB")
    assert S._model_cache_status("auto", "cpu") == (True, "~480 MB")
    assert S._model_cache_status("auto", "cuda") == (False, "~1.6 GB")
    assert S._model_cache_status("auto", "auto")[0] is True   # either candidate counts
    monkeypatch.delenv("HF_HUB_CACHE")
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hfhome"))
    assert S._hf_hub_cache() == tmp_path / "hfhome" / "hub"


def test_job_kill_while_running(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    entry = tmp_path / "slow-01234567"
    code = "import time, sys; sys.stdout.write('[1/4] x\\n'); sys.stdout.flush(); time.sleep(60)"
    job = S.Job("01234567", "C:/fake.mp4", entry, [sys.executable, "-c", code])
    for _ in range(100):
        if job.proc is not None and job.stage == 1:
            break
        time.sleep(0.1)
    assert job.state == "running" and (entry / ".job").is_file()
    t0 = time.monotonic()
    job.kill("cancelled by refresh=true")
    assert job.wait(30)
    assert time.monotonic() - t0 < 30
    assert job.state == "error" and job.kill_reason == "cancelled by refresh=true"
    assert job.proc.poll() is not None
    assert not (entry / ".job").exists()
    text = S.classify_error(job.lines, job.rc, job.kill_reason, job.source)
    assert text.startswith("ERROR: this job was cancelled (cancelled by refresh=true). Call watch_video again.")


# --------------------------------------------------------------------------- error classifier
def test_classify_file_not_found():
    lines = ["[1/4] 本地文件 C:\\x\\nope.mp4", "Traceback (most recent call last):",
             '  File "cli.py", line 1, in process', "FileNotFoundError: 找不到文件：C:\\x\\nope.mp4"]
    text = S.classify_error(lines, 1, None, "C:/x/nope.mp4")
    assert text == "ERROR: file not found: C:\\x\\nope.mp4. Pass an absolute path to an existing video file."
    assert "log:" not in text


def test_classify_login_required():
    lines = ["[1/4] 下载 https://www.bilibili.com/video/BV1xx",
             "ERROR: [BiliBili] BV1xx: HTTP Error 412: Precondition Failed"]
    text = S.classify_error(lines, 1, None, "")
    assert text.startswith("ERROR: the site refused the download (login or cookies required). "
                           'Retry with cookies_from_browser="edge" or "chrome"')
    assert "log:\n" in text and "HTTP Error 412" in text


def test_classify_bad_url():
    lines = ["[1/4] 下载 https://example.com/x", "yt_dlp.utils.DownloadError: ERROR: Unsupported URL: https://example.com/x"]
    text = S.classify_error(lines, 1, None, "")
    assert text.startswith("ERROR: could not fetch this URL: yt_dlp.utils.DownloadError: ERROR: Unsupported URL")
    assert "update yt-dlp" in text


def test_classify_model_download():
    lines = ["  模型 large-v3-turbo，设备 cuda (float16)；首次使用会下载模型，请耐心等待",
             "requests.exceptions.ConnectionError: HTTPSConnectionPool(host='huggingface.co', port=443): Max retries exceeded"]
    text = S.classify_error(lines, 1, None, "")
    assert text.startswith('ERROR: could not download the Whisper speech model. Run "yueying mcp --setup"')
    assert "HF_ENDPOINT=https://hf-mirror.com" in text


def test_classify_gpu():
    lines = ["  模型 large-v3-turbo，设备 cuda (float16)", "RuntimeError: CUDA failed with error out of memory"]
    text = S.classify_error(lines, 1, None, "")
    assert text.startswith('ERROR: GPU speech recognition failed. Retry with model="small"')


def test_classify_ffmpeg():
    lines = ["[1/4] 本地文件 C:\\x\\a.mp4", "yueying.ffm.MediaError: ffmpeg 读不了这个文件：C:\\x\\a.mp4",
             "C:\\x\\a.mp4: Invalid data found when processing input"]
    text = S.classify_error(lines, 1, None, "")
    assert text.startswith("ERROR: ffmpeg could not read this file — it does not look like a playable video/audio file.")


def test_classify_timeout_and_default():
    text = S.classify_error(["a"], -1, "timeout", "")
    assert text.startswith(f"ERROR: the job exceeded YUEYING_JOB_TIMEOUT ({int(S.JOB_TIMEOUT)} s) and was stopped.")
    text = S.classify_error([f"line {i}" for i in range(30)], 3, None, "")
    assert text.startswith("ERROR: processing failed (exit code 3).\nlog:\n")
    assert text.splitlines()[2:] == [f"line {i}" for i in range(20, 30)]      # last 10 raw lines


def test_classify_ignores_recoverable_gpu_fallback_line():
    # asr.py logs this and then succeeds on the CPU; the real failure comes later
    fallback = "  GPU 不可用（RuntimeError: CUDA failed with error cudnn not found），退回 CPU"
    lines = ["[2/4] 字幕", "  模型 large-v3-turbo，设备 cuda (float16)", fallback,
             "  模型 small，设备 cpu (int8)；首次使用会下载模型，请耐心等待", "[3/4] 画面",
             "yueying.ffm.MediaError: ffmpeg 失败: ...", "x.mp4: Invalid data found when processing input"]
    assert S.classify_error(lines, 1, None, "C:/x.mp4").startswith("ERROR: ffmpeg could not read this file")
    lines = ["[2/4] 字幕", fallback, "Traceback (most recent call last):", "KeyError: 'x'"]
    assert S.classify_error(lines, 1, None, "C:/x.mp4").startswith("ERROR: processing failed (exit code 1).")
    # a real GPU failure after the fallback is still class 5
    lines = [fallback, "RuntimeError: CUDA failed with error out of memory"]
    assert S.classify_error(lines, 1, None, "").startswith("ERROR: GPU speech recognition failed")


def test_classify_ignores_title_and_progress_echoes():
    url = "https://www.bilibili.com/video/BV1GJ411x7h7"
    lines = ["[1/4] 下载 " + url, "  《How to Login to Bilibili premium with cookies》", "  下载 50%",
             "  yt-dlp: [download] 100% of 1.00MiB", "Traceback (most recent call last):", "KeyError: 'x'"]
    assert S.classify_error(lines, 1, None, url).startswith("ERROR: processing failed (exit code 1).")
    lines = ["  《CUDA for beginners》", "KeyError: 'x'"]
    assert S.classify_error(lines, 1, None, "C:/x.mp4").startswith("ERROR: processing failed")


def test_classify_first_match_wins_in_spec_order():
    # a login-class line and an ffmpeg-class line: class 2 wins over class 6
    lines = ["Invalid data found when processing input", "Sign in to confirm you're not a bot"]
    assert S.classify_error(lines, 1, None, "").startswith("ERROR: the site refused the download")


# --------------------------------------------------------------------------- misc helpers
def test_covers_rule():
    full = {"frames": [{"index": 1}], "width": 1280, "text_source": {"kind": "asr"}, "options": {}}
    assert all(S._covers(full, m) for m in ("full", "transcript", "frames"))
    frames_only = {"frames": [{"index": 1}], "width": 1280, "text_source": {"kind": "none"}, "options": {"no_asr": True}}
    assert S._covers(frames_only, "frames") and not S._covers(frames_only, "full") and not S._covers(frames_only, "transcript")
    transcript_only = {"frames": [], "width": 1280, "text_source": {"kind": "asr"}, "options": {"no_frames": True}}
    assert S._covers(transcript_only, "transcript") and not S._covers(transcript_only, "full")
    audio_only = {"frames": [], "width": 0, "text_source": {"kind": "asr"}, "options": {}}
    assert S._covers(audio_only, "full")
    no_video_stream = {"frames": [], "width": 1280, "text_source": {"kind": "asr"}, "options": {"no_frames": False}}
    assert S._covers(no_video_stream, "full")
    legacy = {"frames": [{"index": 1}], "width": 1280, "text_source": {"kind": "none"}}      # 0.1.x, no options
    assert S._covers(legacy, "full")


def test_build_command(monkeypatch):
    monkeypatch.delenv("YUEYING_LANG", raising=False)
    monkeypatch.delenv("YUEYING_DEVICE", raising=False)
    monkeypatch.delenv("YUEYING_KEEP_SOURCE", raising=False)
    cmd = S.build_command("C:/v.mp4", Path("C:/out/e"), mode="frames", language="zh", model="small",
                          frame_interval_seconds=2.0, cookies_from_browser="edge")
    assert cmd[:3] == [sys.executable, "-m", "yueying.cli"]
    assert cmd[3:] == ["C:/v.mp4", "--out", os.path.join("C:/out", "e").replace("/", os.sep) if os.name == "nt" else "C:/out/e",
                       "--json", "--ui-lang", "en", "--model", "small", "--device", "auto", "--lang", "zh",
                       "--no-asr", "--interval", "2.0", "--cookies-from-browser", "edge"]
    monkeypatch.setenv("YUEYING_KEEP_SOURCE", "1")
    monkeypatch.setenv("YUEYING_DEVICE", "cpu")
    cmd = S.build_command("https://youtu.be/abc12345", Path("C:/out/e"), mode="transcript")
    assert "--no-frames" in cmd and "--keep" in cmd and cmd[cmd.index("--device") + 1] == "cpu"
    assert "--lang" not in cmd and "--interval" not in cmd


def test_import_is_light():
    for mod in ("yt_dlp", "faster_whisper", "ctranslate2", "torch"):
        assert mod not in sys.modules, mod


# --------------------------------------------------------------------------- hardening
def test_env_numbers_never_crash_import(monkeypatch):
    monkeypatch.setenv("YUEYING_MAX_JOBS", "abc")
    monkeypatch.setenv("YUEYING_JOB_TIMEOUT", "1h")
    assert S._env_number("YUEYING_MAX_JOBS", 1, int) == 1
    assert S._env_number("YUEYING_JOB_TIMEOUT", 7200.0, float) == 7200.0
    monkeypatch.setenv("YUEYING_MAX_JOBS", " 2 ")
    assert S._env_number("YUEYING_MAX_JOBS", 1, int) == 2
    r = subprocess.run([sys.executable, "-c", "import yueying.mcp_server as m; print(m.JOB_TIMEOUT, m._SLOT._value)"],
                       env={**os.environ, "YUEYING_MAX_JOBS": "abc", "YUEYING_JOB_TIMEOUT": "1h", "PYTHONUTF8": "1"},
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]
    assert r.stdout.split() == ["7200.0", "1"]
    assert "ignoring YUEYING_MAX_JOBS" in r.stderr and "ignoring YUEYING_JOB_TIMEOUT" in r.stderr


def _fake_entry(folder: Path) -> None:
    (folder / "frames").mkdir(parents=True, exist_ok=True)
    for name in ("report.md", "transcript.txt", "transcript.srt", "grid_01.jpg", "embedded.srt", ".job"):
        (folder / name).write_text("x")
    (folder / "frames" / "f001_00m01s.jpg").write_bytes(b"jpg")
    (folder / "frames" / "extra").mkdir()
    (folder / "frames" / "extra" / "at_00m05s.jpg").write_bytes(b"jpg")
    (folder / "_download").mkdir()
    (folder / "_download" / "source.mp4").write_bytes(b"mp4")
    # recorded paths deliberately point at another machine (copied folder) - only basenames may be used
    manifest = {"report": "D:\\elsewhere\\report.md", "transcript_srt": "D:\\elsewhere\\transcript.srt",
                "transcript_txt": "D:\\elsewhere\\transcript.txt", "grids": ["D:\\elsewhere\\grid_01.jpg"],
                "frames": [{"index": 1, "time": 1.0, "file": "D:\\elsewhere\\frames\\f001_00m01s.jpg"}]}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_clear_entry_never_wipes_a_user_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("YUEYING_OUT_DIR", str(tmp_path / "root"))
    docs = tmp_path / "Documents"
    _fake_entry(docs)
    (docs / "thesis.docx").write_text("precious")
    (docs / "photos").mkdir()
    (docs / "photos" / "holiday.jpg").write_bytes(b"me")
    (docs / "frames" / "my_own.png").write_bytes(b"mine")     # a user file inside frames/ survives too
    assert not S._is_entry_dir(docs)
    S._clear_entry(docs)
    assert docs.is_dir() and (docs / "thesis.docx").read_text() == "precious"
    assert (docs / "photos" / "holiday.jpg").is_file() and (docs / "frames" / "my_own.png").is_file()
    for name in ("manifest.json", "report.md", "transcript.txt", "transcript.srt", "grid_01.jpg", "embedded.srt",
                 ".job", "_download", "frames/extra", "frames/f001_00m01s.jpg"):
        assert not (docs / name).exists(), name
    # frames/ itself goes only when it ended up empty
    (docs / "frames" / "my_own.png").unlink()
    _fake_entry(docs)
    S._clear_entry(docs)
    assert not (docs / "frames").exists() and (docs / "thesis.docx").is_file()
    # the root itself is never an entry; a folder under the root is removed whole
    root = tmp_path / "root"
    entry = root / "clip-01234567"
    _fake_entry(entry)
    assert S._is_entry_dir(entry) and not S._is_entry_dir(root)
    S._clear_entry(entry)
    assert not entry.exists() and root.is_dir()
    S._clear_entry(tmp_path / "missing")                      # no-op


def test_live_marker_ignores_finished_job(tmp_path):
    from yueying import store
    entry = tmp_path / "e"
    store.write_job_marker(entry, os.getpid())                # "running" (this pid is alive)
    assert S._live_marker(entry) is not None
    time.sleep(0.05)
    (entry / "manifest.json").write_text("{}")                 # written after the marker: that job finished
    assert S._live_marker(entry) is None
    store.clear_job_marker(entry)
    assert S._live_marker(entry) is None


def test_launch_command_prefers_installed_executable(monkeypatch, tmp_path):
    exe = Path(sys.executable).parent / ("yueying-mcp.exe" if os.name == "nt" else "yueying-mcp")
    monkeypatch.delenv("UV_INTERNAL__PARENT_INTERPRETER", raising=False)
    if exe.is_file():
        assert not S._ephemeral_env()
        assert S._launch_command() == [str(exe)]
    fake = tmp_path / "uv" / "cache" / "archive-v0" / "abc" / "Scripts" / "python.exe"
    monkeypatch.setattr(sys, "executable", str(fake))
    assert S._ephemeral_env()
    monkeypatch.setattr(S.shutil, "which", lambda name: "C:/uvx.exe" if name == "uvx" else None)
    # an ephemeral (uvx cache) interpreter: print the ABSOLUTE uvx path, never the bare token (hosts lack PATH)
    assert S._launch_command() == [os.path.abspath("C:/uvx.exe"), "yueying", "mcp"]
    assert os.path.isabs(S._launch_command()[0])
    monkeypatch.setattr(S.shutil, "which", lambda name: None)
    assert S._launch_command() == [str(fake), "-m", "yueying", "mcp"]
