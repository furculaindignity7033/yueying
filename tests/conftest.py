"""Shared pytest fixtures for yueying.

Fixture names follow the 0.2.0 spec: TEST_MP4, TEST_ZH (24 s TTS clips), REF_ZH / REF_DEMO (reference
manifest.json files produced by a real ASR run), an autouse temporary YUEYING_OUT_DIR, and
anyio_backend="asyncio". test.mp4 and test_zh_small.mp4 are committed; the larger clips are git-ignored, so tests that need those are skipped when absent.
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MEDIA = REPO / "test-media"
PYTHON = sys.executable


def _need(p: Path) -> Path:
    if not p.exists():
        pytest.skip(f"test media missing: {p}")
    return p


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture
def TEST_MP4() -> Path:
    """24 s English TTS clip, 1280x720, no subtitles."""
    return _need(MEDIA / "test.mp4")


@pytest.fixture
def TEST_ZH() -> Path:
    """24 s Chinese TTS clip, 1280x720, no subtitles."""
    return _need(MEDIA / "test_zh.mp4")


@pytest.fixture
def REF_ZH() -> Path:
    """manifest.json of a real large-v3-turbo run on test_zh.mp4 (5 segments, 8 frames)."""
    return _need(MEDIA / "out_zh" / "manifest.json")


@pytest.fixture
def REF_DEMO() -> Path:
    """manifest.json of a real run on demo.mp4 (1 segment, 8 frames)."""
    return _need(MEDIA / "out_demo" / "manifest.json")


@pytest.fixture
def ref_zh(REF_ZH) -> Path:
    return REF_ZH


@pytest.fixture
def ref_demo(REF_DEMO) -> Path:
    return REF_DEMO


@pytest.fixture(autouse=True)
def out_root(tmp_path, monkeypatch) -> Path:
    """Every test gets its own YUEYING_OUT_DIR so nothing touches ~/yueying_out."""
    root = tmp_path / "yueying_out"
    monkeypatch.setenv("YUEYING_OUT_DIR", str(root))
    monkeypatch.setenv("PYTHONUTF8", "1")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    return root


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def cli_env() -> dict:
    """Environment for child `python -m yueying.cli` processes (UTF-8, no progress bars)."""
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                "HF_HUB_DISABLE_PROGRESS_BARS": "1", "TQDM_DISABLE": "1"})
    return env
