"""store.py: canonical URLs, source keys, entry names, resolve(), listing, .job markers, parse_time."""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yueying import store

ROOT = Path(__file__).resolve().parents[1]
TEST_MP4 = ROOT / "test-media" / "test.mp4"
TEST_ZH = ROOT / "test-media" / "test_zh.mp4"

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
BV = "BV1GJ411x7h7"


# ----------------------------------------------------------------------------- canonical_url
@pytest.mark.parametrize("url", [
    "https://youtu.be/dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?si=AbCdEf123&t=42",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&feature=share&si=xyz",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://YOUTUBE.com/watch?feature=youtu.be&v=dQw4w9WgXcQ#t=10",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share",
    "https://www.youtube.com/live/dQw4w9WgXcQ?si=abc",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
])
def test_canonical_youtube(url):
    assert store.canonical_url(url) == YT


@pytest.mark.parametrize("url,expected", [
    (f"https://www.bilibili.com/video/{BV}/?spm_id_from=333.999.0.0&vd_source=abc", f"https://www.bilibili.com/video/{BV}"),
    (f"https://www.bilibili.com/video/{BV}?p=1", f"https://www.bilibili.com/video/{BV}"),
    (f"https://www.bilibili.com/video/{BV}?p=2&spm_id_from=x", f"https://www.bilibili.com/video/{BV}?p=2"),
    (f"https://m.bilibili.com/video/{BV}", f"https://www.bilibili.com/video/{BV}"),
    (f"http://bilibili.com/video/{BV}/", f"https://www.bilibili.com/video/{BV}"),
    ("https://www.bilibili.com/video/av170001?from=search&t=5", "https://www.bilibili.com/video/av170001"),
    ("https://www.bilibili.com/video/AV170001?p=3", "https://www.bilibili.com/video/av170001?p=3"),
])
def test_canonical_bilibili(url, expected):
    assert store.canonical_url(url) == expected


def test_canonical_generic_strips_tracking_and_sorts():
    url = "https://WWW.Example.com/Path/Video?utm_source=x&utm_medium=y&z=1&a=2&fbclid=q&gclid=g&share_id=1&from=wx#frag"
    assert store.canonical_url(url) == "https://example.com/Path/Video?a=2&z=1"
    # blank values survive, parameters without noise are kept in sorted order
    assert store.canonical_url("https://vimeo.com/123?b=&a=1") == "https://vimeo.com/123?a=1&b="


def test_canonical_short_links_untouched():
    assert store.canonical_url("https://b23.tv/AbC123") == "https://b23.tv/AbC123"
    assert store.canonical_url("https://v.douyin.com/iAbCdEf/") == "https://v.douyin.com/iAbCdEf/"
    # a Bilibili URL that is not /video/... falls back to the generic rule
    assert store.canonical_url("https://www.bilibili.com/bangumi/play/ep1?spm_id_from=1&x=1") == "https://bilibili.com/bangumi/play/ep1?x=1"


# ----------------------------------------------------------------------------- source_key
def test_source_key_url_variants_share_key():
    keys = {store.source_key(u) for u in [
        "https://youtu.be/dQw4w9WgXcQ?si=1", YT, "https://www.youtube.com/shorts/dQw4w9WgXcQ"]}
    assert len(keys) == 1
    k = keys.pop()
    assert len(k) == 8 and all(c in "0123456789abcdef" for c in k)
    assert store.source_key(f"https://www.bilibili.com/video/{BV}?p=2") != store.source_key(f"https://www.bilibili.com/video/{BV}")


def test_source_key_local_stable_and_mtime_sensitive(tmp_path):
    src = TEST_MP4 if TEST_MP4.exists() else None
    a = tmp_path / "a.mp4"
    if src:
        shutil.copy2(src, a)
    else:
        a.write_bytes(b"x" * 1000)
    k1 = store.source_key(str(a))
    assert k1 == store.source_key(str(a)) == store.source_key(str(a).replace("\\", "/"))
    assert len(k1) == 8
    # same bytes, different mtime -> different key
    st = a.stat()
    os.utime(a, (st.st_atime, st.st_mtime - 3600))
    k2 = store.source_key(str(a))
    assert k2 != k1
    # same bytes + mtime, different path -> different key
    b = tmp_path / "b.mp4"
    shutil.copy2(a, b)
    assert store.source_key(str(b)) != k2
    # content change -> different key
    with open(a, "ab") as f:
        f.write(b"more")
    os.utime(a, (st.st_atime, st.st_mtime - 3600))
    assert store.source_key(str(a)) != k2


def test_source_key_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        store.source_key(str(tmp_path / "nope.mp4"))


# ----------------------------------------------------------------------------- entry_name / entry_dir
def test_entry_name_rules(tmp_path):
    n = store.entry_name("https://youtu.be/dQw4w9WgXcQ?si=1")
    assert n == "yt-dQw4w9WgXcQ-" + store.source_key(YT)
    assert store.entry_name(f"https://www.bilibili.com/video/{BV}?p=1").startswith(f"bili-{BV}-")
    assert "-p1-" not in store.entry_name(f"https://www.bilibili.com/video/{BV}?p=1")
    assert store.entry_name(f"https://www.bilibili.com/video/{BV}?p=2").startswith(f"bili-{BV}-p2-")
    assert store.entry_name("https://www.bilibili.com/video/av170001").startswith("bili-av170001-")
    assert store.entry_name("https://v.douyin.com/iAbCdEf/").startswith("v-iAbCdEf-")
    assert store.entry_name("https://www.xiaohongshu.com/explore/abc123?xsec_token=zz").startswith("xiaohongshu-abc123-")
    assert store.entry_name("https://b23.tv/AbC123").startswith("b23-AbC123-")
    assert store.entry_name("https://example.com/").startswith("example-")

    f = tmp_path / "My Video: final?.mp4"
    f.write_bytes(b"data")
    n = store.entry_name(str(f))
    assert n == "My_Video_final-" + store.source_key(str(f))
    assert store.entry_dir(str(f), tmp_path) == tmp_path / n
    assert store.entry_dir(str(f)).parent == store.out_root()


def test_entry_name_truncates_cjk_to_40(tmp_path):
    stem = "阅影测试视频" * 10          # 60 CJK chars
    f = tmp_path / (stem + ".mp4")
    f.write_bytes(b"data")
    n = store.entry_name(str(f))
    slug_part, _, key = n.rpartition("-")
    assert len(key) == 8
    assert 0 < len(slug_part) <= 40
    assert slug_part == stem[:40]
    assert n == slug_part + "-" + store.source_key(str(f))
    # long URL path segments are cut the same way
    long_url = "https://example.com/v/" + "x" * 100
    slug_part = store.entry_name(long_url).rpartition("-")[0]
    assert len(slug_part) <= 40 and slug_part.startswith("example-x")


def test_entry_name_never_empty_slug(tmp_path):
    f = tmp_path / "___.mp4"                   # stem is only slug-stripped characters
    f.write_bytes(b"d")
    assert store.entry_name(str(f)) == "video-" + store.source_key(str(f))


# ----------------------------------------------------------------------------- out_root
def test_out_root_env_and_default(monkeypatch, tmp_path):
    monkeypatch.delenv("YUEYING_OUT_DIR", raising=False)
    assert store.out_root() == Path.home() / "yueying_out"
    monkeypatch.setenv("YUEYING_OUT_DIR", str(tmp_path / "custom"))
    assert store.out_root() == Path(os.path.abspath(tmp_path / "custom"))
    monkeypatch.setenv("YUEYING_OUT_DIR", "~/yy_root_test")
    r = store.out_root()
    assert r.is_absolute() and "~" not in str(r) and r.name == "yy_root_test"
    monkeypatch.setenv("YUEYING_OUT_DIR", "   ")
    assert store.out_root() == Path.home() / "yueying_out"


# ----------------------------------------------------------------------------- resolve
def _make_entry(d: Path, title="t", **extra) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    m = {"title": title, "duration": 1.0, "width": 1, "height": 1, "text_source": {"kind": "none"},
         "report": str(d / "report.md"), "grids": [], "frames": [], "segments": [], "chapters": []}
    m.update(extra)
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    return d


def test_resolve_four_ways(tmp_path, monkeypatch):
    root = tmp_path / "root"
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"video-bytes")
    url = "https://youtu.be/dQw4w9WgXcQ?si=1"

    e_file = _make_entry(store.entry_dir(str(video), root), title="file")
    e_url = _make_entry(store.entry_dir(url, root), title="url")
    (root / "not-an-entry").mkdir()                          # folder without manifest
    (root / "junk.txt").write_text("x")

    # (a) by 8-hex video_id (any case), explicit root and via $YUEYING_OUT_DIR
    key = store.source_key(str(video))
    assert store.resolve(key, root) == e_file
    assert store.resolve(key.upper(), root) == e_file
    monkeypatch.setenv("YUEYING_OUT_DIR", str(root))
    assert store.resolve(key) == e_file
    assert store.resolve(store.source_key(url)) == e_url
    assert store.resolve("00000000", root) is None

    # (b) by folder: absolute path, quoted path, name relative to root, manifest.json path
    assert store.resolve(str(e_url), root) == e_url
    assert store.resolve(f'"{e_url}"', root) == e_url
    assert store.resolve(e_url.name, root) == e_url
    assert store.resolve(str(e_url / "manifest.json"), root) == e_url
    assert store.resolve(str(root / "not-an-entry"), root) is None

    # (c) by source path (any spelling) and by URL (any variant)
    assert store.resolve(str(video), root) == e_file
    assert store.resolve(str(video).replace("\\", "/"), root) == e_file
    assert store.resolve(YT, root) == e_url
    assert store.resolve("https://www.youtube.com/shorts/dQw4w9WgXcQ", root) == e_url
    assert store.resolve("https://youtu.be/otherVid01", root) is None
    assert store.resolve(str(tmp_path / "missing.mp4"), root) is None
    assert store.resolve("", root) is None
    assert store.resolve("zzz", root) is None

    # a not-yet-finished entry (no manifest.json) is not resolved
    other = tmp_path / "other.mp4"
    other.write_bytes(b"o")
    store.entry_dir(str(other), root).mkdir()
    assert store.resolve(str(other), root) is None
    # root that does not exist
    assert store.resolve(key, tmp_path / "nowhere") is None


# ----------------------------------------------------------------------------- list_entries / dir_size
def test_list_entries_newest_first_with_size(tmp_path):
    root = tmp_path / "root"
    old = _make_entry(root / "old-aaaaaaaa", title="Old", duration=61.0, source="https://x/1")
    (old / "frames").mkdir()
    (old / "frames" / "f.jpg").write_bytes(b"0" * 5000)
    new = _make_entry(root / "new-bbbbbbbb", title="New", duration=5.0, created_at="2026-09-09T10:00:00")
    (root / "broken-cccccccc").mkdir()
    (root / "broken-cccccccc" / "manifest.json").write_text("{not json", encoding="utf-8")
    (root / "empty-dir").mkdir()
    (root / "loose.txt").write_text("x")
    t = time.time()
    os.utime(old / "manifest.json", (t - 1000, t - 1000))
    os.utime(new / "manifest.json", (t, t))

    items = store.list_entries(root)
    assert [i["title"] for i in items] == ["New", "Old"]
    n, o = items
    assert n["key"] == "bbbbbbbb" and o["key"] == "aaaaaaaa"
    assert n["created_at"] == "2026-09-09T10:00:00"
    assert o["created_at"].startswith("20") and "T" in o["created_at"]
    assert o["size_bytes"] >= 5000 + len(json.dumps({})) and o["size_bytes"] == store.dir_size(old)
    assert o["duration"] == 61.0 and o["source"] == "https://x/1" and o["dir"] == str(old)
    assert n["text_source"] == {"kind": "none"}
    assert store.list_entries(tmp_path / "missing") == []
    assert store.dir_size(tmp_path / "missing") == 0


# ----------------------------------------------------------------------------- .job markers
def test_job_marker_fresh_stale_dead(tmp_path):
    entry = tmp_path / "entry"
    assert store.read_job_marker(entry) is None
    store.write_job_marker(entry, os.getpid())
    m = store.read_job_marker(entry)
    assert m and m["pid"] == os.getpid() and m["server_pid"] == os.getpid()
    assert abs(m["started"] - time.time()) < 5
    assert (entry / ".job").is_file()

    # stale: started more than 3 h ago
    data = json.loads((entry / ".job").read_text())
    data["started"] = time.time() - store.JOB_STALE_SECONDS - 60
    (entry / ".job").write_text(json.dumps(data))
    assert store.read_job_marker(entry) is None

    # dead pid: a child that has already exited
    store.write_job_marker(entry, _pid_of_finished_child())
    assert store.read_job_marker(entry) is None

    # garbage marker
    (entry / ".job").write_text("{broken")
    assert store.read_job_marker(entry) is None

    # pid reuse: the pid is alive but its start time is not the one recorded for our child
    store.write_job_marker(entry, os.getpid())
    data = json.loads((entry / ".job").read_text())
    if "pid_start" in data:                                   # Windows / Linux record it
        assert abs(data["pid_start"] - store.pid_start_time(os.getpid())) < 0.01
        assert store.read_job_marker(entry) is not None
        data["pid_start"] -= 3600
        (entry / ".job").write_text(json.dumps(data))
        assert store.read_job_marker(entry) is None
    else:
        assert store.pid_start_time(os.getpid()) is None

    store.clear_job_marker(entry)
    assert not (entry / ".job").exists()
    store.clear_job_marker(entry)                          # idempotent
    store.clear_job_marker(tmp_path / "never-existed")


def _pid_of_finished_child() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.wait()
    return p.pid


def test_pid_alive():
    assert store.pid_alive(os.getpid()) is True
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert store.pid_alive(p.pid) is True
    finally:
        p.kill()
        p.wait()
    assert store.pid_alive(p.pid) is False
    assert store.pid_alive(0) is False
    assert store.pid_alive(-1) is False
    assert store.pid_alive("x") is False


# ----------------------------------------------------------------------------- parse_time
@pytest.mark.parametrize("s,expected", [
    ("185", 185.0), ("185.5", 185.5), ("3:05", 185.0), ("03:05", 185.0), ("1:02:03", 3723.0),
    ("0:00", 0.0), (" 12 ", 12.0), (42, 42.0), (7.25, 7.25), ("[03:15]", 195.0), ("(1:02:03)", 3723.0),
    ("1:02:03.5", 3723.5), ("1h02m03s", 3723.0), ("02m03s", 123.0), ("00m02s", 2.0), ("90s", 90.0),
])
def test_parse_time(s, expected):
    assert store.parse_time(s) == pytest.approx(expected)


@pytest.mark.parametrize("s", ["", "   ", "abc", "1:2:3:4", "-5", "-1:00", "3:", ":5", None, True, "nan"])
def test_parse_time_rejects(s):
    with pytest.raises(ValueError):
        store.parse_time(s)


def test_fmt_time_matches_frames():
    assert store.fmt_time(0) == "00:00"
    assert store.fmt_time(185.4) == "03:05"
    assert store.fmt_time(3723) == "1:02:03"
    assert store.fmt_time(None) == "00:00"


def test_pid_start_time():
    if os.name != "nt" and not sys.platform.startswith("linux"):
        assert store.pid_start_time(os.getpid()) is None
        return
    t = store.pid_start_time(os.getpid())
    assert t is not None and 0 < time.time() - t < 3600 * 24
    assert store.pid_start_time(-1) is None and store.pid_start_time("x") is None
