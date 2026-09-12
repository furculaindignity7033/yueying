"""The version must agree everywhere a release reads it from.

server.json (MCP Registry), pyproject.toml (PyPI), yueying.__version__ (runtime) and the
Claude Code plugin manifest. Registry versions are immutable, so a mismatch must fail before tagging.
"""
import json
import re
from pathlib import Path

import yueying

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    path = ROOT / "pyproject.toml"
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # Python 3.10: the version line is simple enough for a regex
        text = path.read_text(encoding="utf-8")
        project = text.split("[project]", 1)[1].split("\n[", 1)[0]
        return re.search(r'^version\s*=\s*"([^"]+)"', project, re.M).group(1)
    with open(path, "rb") as f:
        return tomllib.load(f)["project"]["version"]


def _json(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_pyproject_matches_package_version():
    assert _pyproject_version() == yueying.__version__


def test_server_json_matches_package_version():
    server = _json("server.json")
    assert server["version"] == yueying.__version__
    assert server["packages"], "server.json must list at least one package"
    for pkg in server["packages"]:
        assert pkg["version"] == yueying.__version__
        assert pkg["identifier"] == "yueying"


def test_plugin_manifest_matches_package_version():
    plugin = _json(".claude-plugin/plugin.json")
    assert plugin["name"] == "yueying"
    assert plugin["version"] == yueying.__version__


def test_registry_name_marker_on_readme_line_one():
    first = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
    assert first.strip() == "<!-- mcp-name: io.github.vsh5dvsch7-png/yueying -->"
    assert _json("server.json")["name"] == "io.github.vsh5dvsch7-png/yueying"


def test_server_json_registry_limits():
    """The registry schema (2025-12-11) caps ServerDetail.description at 100 characters; the publish job
    would reject the release otherwise."""
    server = _json("server.json")
    assert 1 <= len(server["description"]) <= 100, len(server["description"])
    assert len(server["title"]) <= 100
    assert server["$schema"].startswith("https://static.modelcontextprotocol.io/schemas/")


def test_install_cmd_prints_valid_json():
    """install.cmd hand-prints a Claude Desktop block; cmd.exe cannot reliably double backslashes, so the
    path must come out JSON-safe (forward slashes) and parse. Runs the real set/echo lines through cmd.exe."""
    import os
    import subprocess
    import tempfile

    if os.name != "nt":
        import pytest
        pytest.skip("cmd.exe only")
    lines = (ROOT / "install.cmd").read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith('set "MCP_EXE='))
    end = next(i for i, l in enumerate(lines) if l.startswith("echo   Cursor"))
    body = lines[start:end]
    assert any('MCP_JSON=%MCP_EXE:' in l for l in body)
    venv = r"C:\Users\Some One\AppData\Local\yueying\venv"
    script = "\r\n".join(["@echo off", f'set "VENV={venv}"', *body, ""])
    with tempfile.TemporaryDirectory() as d:
        cmd = Path(d) / "probe.cmd"
        cmd.write_bytes(script.encode("utf-8"))
        r = subprocess.run(["cmd.exe", "/d", "/c", str(cmd)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    block = r.stdout[r.stdout.index("{"): r.stdout.rindex("}") + 1]
    cfg = json.loads(block)
    command = cfg["mcpServers"]["yueying"]["command"]
    assert command.endswith("/Scripts/yueying-mcp.exe") and "\\" not in command, command
    assert command.startswith("C:/Users/Some One/")
    assert cfg["mcpServers"]["yueying"]["env"] == {"PYTHONUTF8": "1"}
