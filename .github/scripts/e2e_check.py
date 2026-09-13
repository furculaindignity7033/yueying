"""Real end-to-end run on the CI runner: speech recognition + keyframes, CLI and MCP.

The fast suite skips everything that needs a media file or a model, so without this
job the pipeline has only ever been proven on the maintainer's Windows box.  Uses the
`tiny` model on CPU (~75 MB) so it stays cheap on every runner.
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

REPO = Path(__file__).resolve().parents[2]
EN = REPO / "test-media" / "test.mp4"
ZH = REPO / "test-media" / "test_zh_small.mp4"
ENV = dict(os.environ, PYTHONUTF8="1", HF_HUB_DISABLE_PROGRESS_BARS="1", TQDM_DISABLE="1")


def cli_run(out: Path) -> dict:
    """CLI path: English clip, real recognition, keyframes, report."""
    cmd = [sys.executable, "-m", "yueying.cli", str(EN), "--model", "tiny", "--device", "cpu",
           "--lang", "en", "--out", str(out), "--json"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    print(p.stdout[-2000:], file=sys.stderr)
    assert p.returncode == 0, p.stdout[-4000:]
    manifest = json.loads(p.stdout.strip().splitlines()[-1])
    assert manifest["text_source"]["kind"] == "asr", manifest["text_source"]
    assert manifest["text_source"]["language"] == "en", manifest["text_source"]
    assert manifest["frames"] and manifest["grids"], "no keyframes"
    for key in ("report", "transcript_txt", "transcript_srt"):
        assert Path(manifest[key]).is_file(), key
    text = Path(manifest["transcript_txt"]).read_text(encoding="utf-8").strip()
    assert text.startswith("[00:") and len(text) > 40, text[:200]
    print("CLI ok:", len(manifest["frames"]), "frames;", text.split("]", 1)[1][:70].strip())
    return manifest


async def mcp_run(out_root: Path) -> None:
    """MCP path: Chinese clip end to end, then the read tools."""
    # pin CPU so the assertion below holds on a GPU machine too (runners have none)
    env = dict(ENV, YUEYING_OUT_DIR=str(out_root), YUEYING_DEVICE="cpu")
    params = StdioServerParameters(command=sys.executable, args=["-m", "yueying.mcp_server"], env=env)
    with open(out_root / "server_stderr.log", "w", encoding="utf-8") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=1800) as session:
                await session.initialize()
                res = await session.call_tool(
                    "watch_video",
                    {"video": str(ZH), "model": "tiny", "language": "zh", "wait_seconds": 1200},
                )
                text = res.content[0].text
                assert text.startswith("DONE video_id="), text[:800]
                assert "faster-whisper tiny on cpu" in text, text[:800]
                assert "[00:" in text, text[:800]
                video_id = text.split("video_id=", 1)[1].split()[0]
                print("MCP ok:", text.splitlines()[0])

                res = await session.call_tool("get_frames", {"video": video_id, "count": 1})
                images = [c for c in res.content if c.type == "image"]
                assert len(images) == 1 and images[0].mime_type == "image/jpeg", res.content
                assert len(images[0].data) < 400_000, len(images[0].data)

                res = await session.call_tool("get_frame_at", {"video": video_id, "time": "00:10"})
                caption = res.content[0].text
                assert "extracted exactly" in caption, caption
                assert [c for c in res.content if c.type == "image"], "no image"
                print("frames ok:", caption.split(" · ")[0])
    stderr = (out_root / "server_stderr.log").read_text(encoding="utf-8", errors="replace")
    assert "Traceback" not in stderr, stderr[-2000:]


def main() -> int:
    assert EN.is_file() and ZH.is_file(), "test media missing from the checkout"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cli_run(tmp_path / "cli")
        (tmp_path / "mcp").mkdir()
        asyncio.run(mcp_run(tmp_path / "mcp"))
    print("end-to-end ok on", sys.platform)
    return 0


sys.exit(main())
