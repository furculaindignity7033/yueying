"""0.2.0 CLI / library backward-compatibility tests.

Fast (no ASR, no network) unless marked `asr`. The byte-identical checks compare against
tests/fixtures/cli_baseline/, captured with the v0.1.3 CLI before the refactor (paths normalised to
<OUT> / <REPO>).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

from yueying import __version__, asr, ffm, models, report
from yueying import frames as fr

REPO = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "cli_baseline"
OPTION_KEYS = {"model", "device", "lang", "interval", "frames", "scene", "no_dedupe", "no_frames",
               "no_asr", "force_asr", "keep", "ui_lang"}
NEW_KEYS = {"schema", "yueying_version", "created_at", "ui_lang", "options", "out_dir", "uploader"}


# tail of a placeholder path, e.g. <OUT>\frames\f001.jpg (JSON) or <OUT>/frames/f001.jpg (posix)
_PLACEHOLDER_TAIL = re.compile(r"(<OUT>|<REPO>)((?:\\\\|\\|/)[^\"'\s]*)")


def _slashes(text: str) -> str:
    """Spell placeholder paths with "/" whatever platform wrote them."""
    return _PLACEHOLDER_TAIL.sub(
        lambda m: m.group(1) + m.group(2).replace("\\\\", "/").replace("\\", "/"), text)


def _norm(text: str, out_dir: str) -> str:
    """Replace the output folder / repo root (JSON-escaped, backslash and slash forms) with placeholders.

    The baselines were captured on Windows, so the separators *inside* a placeholder path are
    normalised to "/" as well — otherwise every comparison fails on Linux and macOS.
    """
    for real, ph in ((os.path.abspath(out_dir), "<OUT>"), (str(REPO), "<REPO>")):
        for form in (real.replace("\\", "\\\\"), real, real.replace("\\", "/")):
            text = text.replace(form, ph)
    return _slashes(text)


def _fixture(name: str, fn: str) -> str:
    """Baseline text, separators normalised (it was captured on Windows)."""
    return _slashes((FIX / name / fn).read_text(encoding="utf-8"))


def run_cli(args, env, timeout=600) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "yueying.cli", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=timeout)


def _json_line(p: subprocess.CompletedProcess) -> dict:
    line = p.stdout.splitlines()[-1]
    assert line.startswith("{") or line.startswith("["), p.stdout[-500:]
    return json.loads(line)


# ---------------------------------------------------------------------------------------------- byte identity

@pytest.mark.parametrize("name", ["test", "test_zh"])
def test_no_asr_output_byte_identical_to_0_1_3(name, tmp_path, cli_env, TEST_MP4, TEST_ZH):
    src = (REPO / "test-media" / f"{name}.mp4").as_posix()      # same spelling as the baseline run
    out = str(tmp_path / "out")
    p = run_cli([src, "--no-asr", "--out", out, "--json"], cli_env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stderr == ""
    lines = p.stdout.splitlines()

    # the --json stdout line
    assert _norm(lines[-1], out) == _fixture(name, "json_line.txt").rstrip("\n")
    # files
    for fn in ("report.md", "transcript.txt", "transcript.srt"):
        got = _norm((Path(out) / fn).read_text(encoding="utf-8"), out)
        assert got == _fixture(name, fn), fn
    # the Chinese log lines (only the elapsed seconds may differ)
    scrub = lambda s: re.sub(r"完成，用时 \d+ 秒", "完成，用时 N 秒", s)
    assert scrub(_norm("\n".join(lines[:-1]), out)) == scrub(_fixture(name, "log.txt").rstrip("\n"))


def test_manifest_on_disk_has_schema_2_and_additive_keys(tmp_path, cli_env, TEST_MP4):
    out = str(tmp_path / "out")
    p = run_cli([TEST_MP4.as_posix(), "--no-asr", "--out", out, "--json"], cli_env)
    assert p.returncode == 0, p.stdout + p.stderr
    m = json.loads((Path(out) / "manifest.json").read_text(encoding="utf-8"))

    assert m["schema"] == 2
    assert m["yueying_version"] == __version__
    assert m["ui_lang"] == "zh"
    assert os.path.normcase(m["out_dir"]) == os.path.normcase(os.path.abspath(out))
    datetime.fromisoformat(m["created_at"])          # ISO 8601 with local offset
    assert set(m["options"]) == OPTION_KEYS
    assert m["options"]["no_asr"] is True
    assert m["options"]["model"] == "large-v3-turbo" and m["options"]["device"] == "auto"
    assert m["options"]["scene"] == 0.3 and m["options"]["ui_lang"] == "zh"
    # every 0.1.x key is still there with the same value
    old = json.loads(_norm(_fixture("test", "manifest.json"), out))
    got = json.loads(_norm(json.dumps(m, ensure_ascii=False), out))
    assert {k: got[k] for k in old} == old
    assert set(m) == set(old) | NEW_KEYS
    # the stdout line filters segments + the new keys
    line = _json_line(p)
    assert set(line) == set(old) - {"segments"}
    assert not (NEW_KEYS & set(line))


# ---------------------------------------------------------------------------------------------- --ui-lang en

def test_ui_lang_en_yields_english_report(tmp_path, cli_env, TEST_MP4):
    out = str(tmp_path / "out")
    p = run_cli([TEST_MP4.as_posix(), "--no-asr", "--ui-lang", "en", "--out", out, "--json"], cli_env)
    assert p.returncode == 0, p.stdout + p.stderr
    rep = (Path(out) / "report.md").read_text(encoding="utf-8")
    for s in ("# test\n", "- Source: ", "- Duration: 00:24, resolution 1280x720", "- Text source: none",
              "- Keyframes: 6 (frames/), contact sheets: 1", "## Visual overview", "- grid_01.jpg: frames 1–6, 00:01 ~ 00:16",
              "## Keyframes", "| # | Time | File |", "## Transcript",
              "(no transcript: no subtitles and speech recognition skipped)"):
        assert s in rep, s
    assert not re.search(r"[\u4e00-\u9fff]", rep), "Chinese text leaked into the English report"
    line = _json_line(p)
    assert line["text_source"] == {"kind": "none", "desc": "none"}
    m = json.loads((Path(out) / "manifest.json").read_text(encoding="utf-8"))
    assert m["ui_lang"] == "en" and m["options"]["ui_lang"] == "en"
    # the Chinese log lines are a contract and do NOT change with --ui-lang
    assert "[1/4] 本地文件 " in p.stdout and "无字幕且已跳过语音识别" in p.stdout
    # language-independent files are unchanged
    for fn in ("transcript.txt", "transcript.srt"):
        assert _norm((Path(out) / fn).read_text(encoding="utf-8"), out) == _fixture("test", fn)


def test_describe_text_source_strings():
    d = report.describe_text_source
    assert d({"kind": "none"}, "zh") == "无"
    assert d({"kind": "none"}, "en") == "none"
    assert d({}, "en") == "none" and d(None, "zh") == "无"
    sub = {"kind": "subtitle", "file": os.path.join("x", "y", "a.srt"), "count": 12}
    assert d(sub, "zh") == "字幕文件 a.srt（12 条）"          # 0.1.x string verbatim
    assert d(sub, "en") == "subtitle file a.srt (12 cues)"
    asr_ts = {"kind": "asr", "model": "small", "device": "cpu", "language": "zh",
              "language_probability": 0.996, "compute_type": "int8", "count": 5}
    assert d(asr_ts, "zh") == "本地语音识别 faster-whisper small（cpu），检测语言 zh（100%），5 段"
    assert d(asr_ts, "en") == "local speech recognition, faster-whisper small on cpu, detected zh (100%), 5 segments"
    # 0.1.x manifests have no count -> clause omitted, never an exception
    old = {"kind": "asr", "model": "large-v3-turbo", "device": "cuda", "language": "zh", "language_probability": 1.0}
    assert d(old, "en") == "local speech recognition, faster-whisper large-v3-turbo on cuda, detected zh (100%)"
    assert d({"kind": "subtitle", "file": "C:\\a\\b.vtt"}, "en") == "subtitle file b.vtt"
    assert d({"kind": "none"}, "fr") == "none"          # unknown ui_lang falls back to English


def test_write_all_keeps_paragraphs_api_and_fills_desc(tmp_path):
    segs = [{"start": 0.0, "end": 1.0, "text": "你好"}, {"start": 1.2, "end": 2.0, "text": "世界"},
            {"start": 9.0, "end": 10.0, "text": "again"}]
    paras = report.paragraphs(segs)
    assert [p["text"] for p in paras] == ["你好，世界", "again"]
    assert report.paragraphs(segs, max_len=45.0, gap=2.0) == paras
    m = report.write_all(str(tmp_path), {"title": "t", "source": "s.mp4", "duration": 10, "width": 0, "height": 0},
                         segs, [], [], {"kind": "subtitle", "file": "s.srt", "count": 3}, ui_lang="en",
                         options={"model": "auto"})
    assert m["text_source"]["desc"] == "subtitle file s.srt (3 cues)"
    assert m["schema"] == 2 and m["options"] == {"model": "auto"} and m["ui_lang"] == "en"
    assert Path(m["report"]).read_text(encoding="utf-8").count("[00:00] 你好，世界") == 1
    assert (tmp_path / "transcript.txt").read_text(encoding="utf-8") == "[00:00] 你好，世界\n[00:09] again\n"
    assert os.path.isabs(m["out_dir"]) and os.path.isabs(m["transcript_srt"])


# ---------------------------------------------------------------------------------------------- multi input

def test_multi_input_still_writes_index(tmp_path, cli_env, TEST_MP4, TEST_ZH):
    out = str(tmp_path / "batch")
    p = run_cli([TEST_MP4.as_posix(), TEST_ZH.as_posix(), "--no-asr", "--out", out, "--json"], cli_env)
    assert p.returncode == 0, p.stdout + p.stderr
    index = (Path(out) / "index.md").read_text(encoding="utf-8")
    assert "# 阅影批量结果（2 个视频）" in index
    assert "[test/report.md](test/report.md)" in index and "[test_zh/report.md](test_zh/report.md)" in index
    arr = _json_line(p)
    assert isinstance(arr, list) and [x["title"] for x in arr] == ["test", "test_zh"]
    assert all(not (NEW_KEYS | {"segments"}) & set(x) for x in arr)
    assert (Path(out) / "test" / "manifest.json").exists() and (Path(out) / "test_zh" / "manifest.json").exists()
    assert "===== 第 1/2 个：" in p.stdout and "全部完成：2/2 个成功" in p.stdout


def test_multi_input_failure_is_reported_and_exit_1(tmp_path, cli_env, TEST_MP4):
    out = str(tmp_path / "batch")
    missing = str(tmp_path / "nope.mp4")
    p = run_cli([TEST_MP4.as_posix(), missing, "--no-asr", "--out", out, "--json"], cli_env)
    assert p.returncode == 1
    assert "失败：FileNotFoundError: 找不到文件：" in p.stdout
    index = (Path(out) / "index.md").read_text(encoding="utf-8")
    assert "✗" in index and "test/report.md" in index
    arr = _json_line(p)
    assert arr[0]["title"] == "test" and "error" in arr[1]


def test_single_missing_file_propagates_file_not_found(tmp_path, cli_env):
    p = run_cli([str(tmp_path / "nope.mp4"), "--no-asr", "--out", str(tmp_path / "out")], cli_env)
    assert p.returncode != 0
    assert "FileNotFoundError" in p.stderr and "找不到文件：" in p.stderr


# ---------------------------------------------------------------------------------------------- entry points

@pytest.mark.parametrize("mod", ["yueying", "yueying.cli"])
def test_version_flag(mod, cli_env):
    p = subprocess.run([sys.executable, "-m", mod, "--version"], capture_output=True, text=True, env=cli_env)
    assert p.returncode == 0
    assert p.stdout.strip() == f"yueying {__version__}"


def test_mcp_subcommand_dispatches_to_mcp_server(monkeypatch):
    from yueying import cli
    calls = []
    fake = types.ModuleType("yueying.mcp_server")
    fake.main = lambda argv=None: calls.append(list(argv)) or 7
    monkeypatch.setitem(sys.modules, "yueying.mcp_server", fake)
    assert cli.main(["mcp", "--check"]) == 7
    assert cli.main(["mcp"]) == 7
    assert calls == [["--check"], []]


def test_mcp_subcommand_without_server_module_fails_cleanly(monkeypatch, capsys):
    from yueying import cli
    monkeypatch.setitem(sys.modules, "yueying.mcp_server", None)      # makes the import raise ImportError
    assert cli.main(["mcp"]) == 1
    err = capsys.readouterr().err
    assert "MCP server module is not available" in err


def test_model_helpers_are_pure_and_match_faster_whisper():
    assert asr.resolve_model("auto", "cuda") == "large-v3-turbo"
    assert asr.resolve_model("auto", "cpu") == "small"
    assert asr.resolve_model("medium", "cuda") == "medium"
    assert models.resolve_model is asr.resolve_model
    assert models.model_repo("large-v3-turbo") == "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    assert models.model_repo("small") == "Systran/faster-whisper-small"
    assert set(models.MODEL_SIZES) == {"tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"}
    assert models.MODEL_SIZES["small"] == "480 MB" and models.MODEL_SIZES["large-v3-turbo"] == "1.6 GB"
    # models.py is imported by the MCP server process: it must not import anything (esp. faster_whisper)
    import ast
    tree = ast.parse(Path(models.__file__).read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))], "models.py must stay import-free"


# ---------------------------------------------------------------------------------------------- ffm / frames

def _lavfi_ok() -> bool:
    p = ffm.run(["-f", "lavfi", "-i", "testsrc=duration=0.2:size=64x64:rate=5", "-f", "null", "-"], check=False)
    return p.returncode == 0


def test_ffm_media_error_is_runtime_error_and_timeout_kills():
    assert issubclass(ffm.MediaError, RuntimeError)
    with pytest.raises(ffm.MediaError, match="ffmpeg 失败"):
        ffm.run(["-i", "definitely-missing-file.mp4", "-f", "null", "-"])
    if not _lavfi_ok():
        pytest.skip("this ffmpeg build has no lavfi")
    with pytest.raises(ffm.MediaError, match="timed out"):
        ffm.run(["-re", "-f", "lavfi", "-i", "testsrc=duration=30:size=64x64:rate=5", "-f", "null", "-"], timeout=1)
    assert ffm.ffmpeg_exe() is ffm.ffmpeg_exe()          # lru_cache


def test_extract_one_writes_labelled_frame(tmp_path, TEST_MP4):
    from PIL import Image
    out = tmp_path / "frames" / "extra" / "at_00m05s.jpg"
    assert fr.extract_one(str(TEST_MP4), 5.0, str(out), width=640, label="@00:05") is True
    with Image.open(out) as im:
        assert im.width == 640 and im.height == 360
    # no label -> raw ffmpeg frame, full width
    raw = tmp_path / "raw.jpg"
    assert fr.extract_one(str(TEST_MP4), 1.0, str(raw)) is True
    with Image.open(raw) as im:
        assert im.width == 1280
    # beyond the end -> ffmpeg produces nothing -> False, no exception
    assert fr.extract_one(str(TEST_MP4), 9999.0, str(tmp_path / "none.jpg")) is False


# ---------------------------------------------------------------------------------------------- load_manifest

def test_load_manifest_rebases_moved_and_copied_folder(tmp_path, cli_env, TEST_MP4):
    orig = tmp_path / "orig"
    p = run_cli([TEST_MP4.as_posix(), "--no-asr", "--out", str(orig)], cli_env)
    assert p.returncode == 0, p.stdout + p.stderr
    # unchanged folder: paths untouched
    m0 = report.load_manifest(str(orig))
    assert Path(m0["report"]) == orig / "report.md" and m0["schema"] == 2
    # copied folder (original still exists): paths follow the copy
    copy = tmp_path / "copy"
    shutil.copytree(orig, copy)
    m1 = report.load_manifest(copy)
    assert Path(m1["report"]) == copy / "report.md"
    assert all(Path(f["file"]).parent == copy / "frames" for f in m1["frames"]) and len(m1["frames"]) == 6
    assert [Path(g) for g in m1["grids"]] == [copy / "grid_01.jpg"]
    assert os.path.normcase(m1["out_dir"]) == os.path.normcase(str(copy))
    # moved folder
    moved = tmp_path / "moved"
    shutil.move(str(orig), str(moved))
    m2 = report.load_manifest(str(moved / "manifest.json"))          # file path accepted too
    assert Path(m2["transcript_txt"]).exists() and Path(m2["transcript_txt"]).parent == moved
    assert all(Path(f["file"]).exists() for f in m2["frames"])
    with pytest.raises(FileNotFoundError):
        report.load_manifest(tmp_path / "nothing-here")


def test_load_manifest_handles_0_1_x_folder_without_out_dir(tmp_path):
    folder = tmp_path / "old"
    (folder / "frames").mkdir(parents=True)
    for rel in ("report.md", "transcript.txt", "transcript.srt", "grid_01.jpg", "frames/f001_00m01s.jpg"):
        (folder / rel).write_bytes(b"x")
    gone = str(tmp_path / "gone" / "yueying_out" / "old")             # original location, no longer exists
    old = {"title": "old", "source": "old.mp4", "duration": 24.0, "width": 1280, "height": 720,
           "text_source": {"kind": "none", "desc": "无"},
           "report": os.path.join(gone, "report.md"),
           "transcript_srt": os.path.join(gone, "transcript.srt"),
           "transcript_txt": os.path.join(gone, "transcript.txt"),
           "grids": [os.path.join(gone, "grid_01.jpg")],
           "frames": [{"index": 1, "time": 1.0, "file": os.path.join(gone, "frames", "f001_00m01s.jpg")}],
           "segments": [], "chapters": []}
    (folder / "manifest.json").write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    m = report.load_manifest(folder)
    assert m["schema"] == 1 and "created_at" not in m
    assert Path(m["report"]) == folder / "report.md"
    assert Path(m["grids"][0]) == folder / "grid_01.jpg"
    assert Path(m["frames"][0]["file"]) == folder / "frames" / "f001_00m01s.jpg"
    assert os.path.normcase(m["out_dir"]) == os.path.normcase(str(folder))


# ---------------------------------------------------------------------------------------------- real ASR (opt-in)

@pytest.mark.asr
def test_model_auto_real_asr_run(tmp_path, cli_env, TEST_ZH):
    """`--model auto` resolves in the child (cuda -> large-v3-turbo, cpu -> small); log lines unchanged."""
    out = str(tmp_path / "asr")
    p = run_cli([TEST_ZH.as_posix(), "--out", out, "--model", "auto", "--json"], cli_env, timeout=1800)
    assert p.returncode == 0, p.stdout + p.stderr
    assert re.search(r"^  模型 (large-v3-turbo，设备 cuda \(float16\)|small，设备 cpu \(int8\))；首次使用会下载模型，请耐心等待$",
                     p.stdout, re.M), p.stdout
    assert re.search(r"^  检测语言 zh（置信度 \d+%）$", p.stdout, re.M)
    assert re.search(r"^  识别进度 \d+%$", p.stdout, re.M)
    assert "      没有可用字幕，做本地语音识别" in p.stdout
    m = _json_line(p)
    ts = m["text_source"]
    assert ts["kind"] == "asr" and ts["requested_model"] == "auto" and ts["model"] in ("large-v3-turbo", "small")
    assert ts["count"] >= 1 and ts["desc"].startswith("本地语音识别 faster-whisper ")
    assert not (Path(out) / "audio.wav").exists()
    txt = (Path(out) / "transcript.txt").read_text(encoding="utf-8")
    assert txt.startswith("[00:0")
    disk = json.loads((Path(out) / "manifest.json").read_text(encoding="utf-8"))
    assert disk["options"]["model"] == "auto" and disk["text_source"]["model"] == ts["model"]
