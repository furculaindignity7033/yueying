"""refresh=true must only rmtree folders yueying itself named (<slug>-<key8> directly under the output root)."""
from pathlib import Path

from yueying import mcp_server


def test_is_entry_dir_rules(tmp_path, monkeypatch):
    root = tmp_path / "out"
    root.mkdir()
    monkeypatch.setenv("YUEYING_OUT_DIR", str(root))
    auto = root / "clip-0123abcd"
    auto.mkdir()
    custom = root / "notes"
    custom.mkdir()
    nested = root / "sub" / "clip-0123abcd"
    nested.mkdir(parents=True)
    outside = tmp_path / "elsewhere-0123abcd"
    outside.mkdir()
    assert mcp_server._is_entry_dir(auto) is True
    assert mcp_server._is_entry_dir(custom) is False
    assert mcp_server._is_entry_dir(nested) is False
    assert mcp_server._is_entry_dir(outside) is False
    assert mcp_server._is_entry_dir(root) is False


def test_clear_entry_keeps_user_files_under_root(tmp_path, monkeypatch):
    root = tmp_path / "out"
    root.mkdir()
    monkeypatch.setenv("YUEYING_OUT_DIR", str(root))
    custom = root / "notes"
    custom.mkdir()
    keep = custom / "keep_me.txt"
    keep.write_text("mine", encoding="utf-8")
    (custom / "report.md").write_text("# x", encoding="utf-8")
    (custom / "manifest.json").write_text('{"report": "report.md", "grids": [], "frames": []}', encoding="utf-8")
    mcp_server._clear_entry(custom)
    assert keep.exists() and keep.read_text(encoding="utf-8") == "mine"
    assert not (custom / "report.md").exists()
