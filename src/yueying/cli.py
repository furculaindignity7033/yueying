"""命令行入口：yueying <视频文件或链接>... [--out 目录] ...；yueying mcp 启动 MCP 服务器。"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import time

from . import __version__, ffm, subs, frames as fr, report
from .download import is_url, download, list_entries

# manifest keys that never appear in the --json stdout line (keeps it byte-identical to 0.1.x)
_JSON_SKIP = frozenset({"segments", "schema", "yueying_version", "created_at", "options", "ui_lang", "out_dir",
                        "uploader", "notes"})
# nested text_source keys that are new in 0.2.0 (manifest.json keeps them; the stdout line does not)
_JSON_SKIP_TEXT_SOURCE = frozenset({"count", "requested_model"})
# argparse fields recorded in manifest["options"]
_OPTION_KEYS = ("model", "device", "lang", "interval", "frames", "scene", "no_dedupe", "no_frames",
                "no_asr", "force_asr", "keep", "ui_lang")


def log(msg: str) -> None:
    print(msg, flush=True)


def slug(s: str) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("._")
    return s[:60] or "video"


def find_sidecar_subs(video: str) -> list:
    base = os.path.splitext(video)[0]
    found = []
    for ext in (".srt", ".vtt", ".ass", ".json"):
        found += glob.glob(glob.escape(base) + "*" + ext)
    return sorted(found)


def find_exe() -> str:
    """找到 yueying 命令的绝对路径，写进技能文件，免得 Claude 的 shell 里 PATH 不一样找不到。"""
    exe = shutil.which("yueying")
    if exe:
        return os.path.abspath(exe)
    cand = os.path.join(os.path.dirname(sys.executable), "yueying.exe" if os.name == "nt" else "yueying")
    return cand if os.path.exists(cand) else "yueying"


def install_skill() -> int:
    """把 SKILL.md 装到 ~/.claude/skills/yueying/，Claude Code 需要看视频时就会自动用。"""
    from importlib import resources
    text = resources.files("yueying").joinpath("skill/SKILL.md").read_text(encoding="utf-8")
    exe = find_exe()
    if exe != "yueying":
        text = text.replace('   yueying "<', f'   "{exe}" "<')
    dst_dir = os.path.join(os.path.expanduser("~"), ".claude", "skills", "yueying")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "SKILL.md")
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    log(f"技能已安装：{dst}")
    log(f"技能里使用的命令：{exe}")
    log("现在可以在 Claude Code 里说：帮我看看这个视频 <路径或链接>")
    return 0


def _json_view(manifest: dict) -> dict:
    """The manifest as printed on the --json stdout line: the 0.1.x shape, byte-identical.

    Top-level keys new in 0.2.0 and the nested text_source keys count/requested_model are dropped;
    manifest.json on disk keeps everything.
    """
    out = {k: v for k, v in manifest.items() if k not in _JSON_SKIP}
    ts = out.get("text_source")
    if isinstance(ts, dict):
        out["text_source"] = {k: v for k, v in ts.items() if k not in _JSON_SKIP_TEXT_SOURCE}
    return out


def _options_dict(a) -> dict:
    """The CLI options worth recording in manifest["options"] (missing attributes -> None)."""
    return {k: getattr(a, k, None) for k in _OPTION_KEYS}


def _cleanup(wav, download_dir) -> None:
    if wav:
        try:
            os.remove(wav)
        except OSError:
            pass
    if download_dir:
        shutil.rmtree(download_dir, ignore_errors=True)


# LOAD-BEARING LOG LINES: mcp_server.py parses "[n/4]", "  下载 N%", "  模型 ", "  检测语言",
# "  识别进度 N%", "  抽帧 n/N", "完成，用时", "《title》". Changing them requires updating
# mcp_server._parse_line and tests/fixtures/cli_log_sample.txt.
# (They stay Chinese on purpose: they are an internal contract, not UI. The report language is --ui-lang.)
def process(inp: str, out_dir, base: str, a) -> dict:
    """处理一个视频，返回 manifest。out_dir 为 None 时自动放在 base/<视频名>。

    audio.wav and the _download folder (unless --keep) are removed even when processing fails.
    """
    t0 = time.time()
    meta = {"source": inp}
    downloaded = None
    tmp_dir = wav = None
    ui_lang = getattr(a, "ui_lang", None) or "zh"
    notes: list[str] = []          # degraded steps, recorded in manifest["notes"] (English)
    try:
        if is_url(inp):
            log(f"[1/4] 下载 {inp}")
            tmp_dir = os.path.join(out_dir or base, "_download")
            downloaded = download(inp, tmp_dir, a.cookies_from_browser, log)
            video = downloaded["video"]
            meta.update({k: downloaded[k] for k in ("title", "uploader", "url", "description", "chapters", "language")})
            out_dir = out_dir or os.path.join(base, slug(meta["title"]))
            sub_files = downloaded["subs"]
        else:
            video = os.path.abspath(inp)
            if not os.path.exists(video):
                raise FileNotFoundError(f"找不到文件：{video}")
            log(f"[1/4] 本地文件 {video}")
            meta["title"] = os.path.splitext(os.path.basename(video))[0]
            out_dir = out_dir or os.path.join(base, slug(meta["title"]))
            sub_files = find_sidecar_subs(video)
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)

        info = ffm.probe(video)
        meta.update(info)
        if downloaded and downloaded.get("duration") and not info["duration"]:
            meta["duration"] = downloaded["duration"]
        log(f"      时长 {fr.fmt_time(meta['duration'])}，{info['width']}x{info['height']}，"
            f"{'有' if info['has_audio'] else '无'}音轨，内嵌字幕 {info['subtitle_streams']} 条")

        # ---- 文字 ----
        segs, text_source = [], {"kind": "none"}
        log("[2/4] 文字")
        if not a.force_asr:
            if not sub_files and info["subtitle_streams"]:
                emb = os.path.join(out_dir, "embedded.srt")
                if ffm.extract_embedded_subtitle(video, emb):
                    sub_files = [emb]
            for sf in sub_files:
                try:
                    segs = subs.parse_file(sf)
                except Exception as e:
                    log(f"      字幕 {os.path.basename(sf)} 解析失败：{e}")
                    continue
                if segs:
                    text_source = {"kind": "subtitle", "file": sf, "count": len(segs)}
                    log(f"      用字幕 {os.path.basename(sf)}，{len(segs)} 条")
                    break
        if not segs and not a.no_asr:
            if not info["has_audio"]:
                log("      没有音轨，跳过语音识别")
            else:
                log("      没有可用字幕，做本地语音识别")
                from . import asr
                wav = os.path.join(out_dir, "audio.wav")
                ffm.extract_audio(video, wav)
                segs, ainfo = asr.transcribe(wav, a.model, a.device, a.lang, meta["duration"], log)
                _cleanup(wav, None)
                wav = None
                text_source = {"kind": "asr", **ainfo, "count": len(segs)}
        elif not segs:
            log("      无字幕且已跳过语音识别")
        text_source["desc"] = report.describe_text_source(text_source, ui_lang)

        # ---- 画面 ----
        frame_list, grids = [], []
        log("[3/4] 画面")
        if a.no_frames or not info["width"]:
            log("      跳过")
        else:
            target = a.frames or fr.default_target(meta["duration"], a.interval)
            try:
                scenes = fr.scene_times(video, a.scene)
            except ffm.MediaError as e:
                if "timed out" not in str(e):
                    raise
                # scene detection decodes every frame; on a huge file it can exceed its limit.
                # Degrade to evenly spaced frames instead of losing the whole run (0.1.x had no limit),
                # and say so in the manifest (spec 3.3 would fail the run; see CHANGELOG 0.2.0).
                log("      场景检测超时，按固定间隔抽帧")
                notes.append("scene detection timed out; keyframes are evenly spaced instead of at scene changes")
                scenes = []
            times = fr.plan_times(meta["duration"], scenes, target)
            log(f"      场景切换 {len(scenes)} 处，抽 {len(times)} 帧")
            frame_list = fr.extract(video, times, os.path.join(out_dir, "frames"), dedupe=not a.no_dedupe, log=log)
            grids = fr.make_grids(frame_list, out_dir)

        # ---- 报告 ----
        log("[4/4] 写报告")
        manifest = report.write_all(out_dir, meta, segs, frame_list, grids, text_source,
                                    ui_lang=ui_lang, options=_options_dict(a), notes=notes)
        log(f"完成，用时 {time.time() - t0:.0f} 秒")
        log(f"报告：{manifest['report']}")
        return manifest
    finally:
        _cleanup(wav, None if getattr(a, "keep", False) else tmp_dir)


def write_index(base: str, results: list) -> str:
    """多个视频时在 base 下写一份 index.md，列出每个视频的报告位置。"""
    lines = [f"# 阅影批量结果（{len(results)} 个视频）", ""]
    for r in results:
        if r.get("error"):
            lines.append(f"- ✗ {r['input']}：失败，{r['error']}")
        else:
            m = r["manifest"]
            rel = os.path.relpath(m["report"], base).replace("\\", "/")
            lines.append(f"- {m.get('title')}（{fr.fmt_time(m.get('duration') or 0)}）：[{rel}]({rel})")
    path = os.path.join(base, "index.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["mcp"]:
        # `yueying mcp [--setup|--check|--version]` -> MCP server (stdio). Imported lazily: the server must
        # not pull in the pipeline, and the pipeline must not pull in the server.
        try:
            from .mcp_server import main as _mcp_main
        except ImportError as e:
            sys.stderr.write(f"yueying mcp: the MCP server module is not available ({e}).\n"
                             f"Reinstall with: pip install -U \"yueying>=0.2\"\n")
            return 1
        return _mcp_main(args[1:])

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="yueying", description="阅影：把视频变成 AI 能读的文字稿和关键帧",
                                 epilog="MCP 服务器：yueying mcp [--setup | --check]（给 Claude Desktop / Cursor / Claude Code 等用）")
    ap.add_argument("input", nargs="*", help="本地视频文件，或 B站 / YouTube / 抖音等视频链接，可以给多个")
    ap.add_argument("--install-skill", action="store_true", help="把 Claude Code 技能装到 ~/.claude/skills/yueying，装一次即可")
    ap.add_argument("--out", help="输出目录（默认 ./yueying_out/<视频名>；多个视频时是它们的父目录）")
    ap.add_argument("--all", action="store_true", help="链接是 B站 分 P / 合集 / 播放列表时，把里面的视频全部处理（默认只取第一个）")
    ap.add_argument("--lang", help="语音语言代码，如 zh / en / ja；默认自动检测")
    ap.add_argument("--model", default="large-v3-turbo",
                    help="whisper 模型：tiny/base/small/medium/large-v3/large-v3-turbo（默认）/auto（有显卡 large-v3-turbo，否则 small）")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"], help="识别设备，默认有显卡用显卡")
    ap.add_argument("--interval", type=float, metavar="秒", help="大约每隔几秒抽一帧（默认按时长自动：1 分钟内 2 秒，3 分钟内 3 秒，10 分钟内 6 秒，30 分钟内 12 秒，更长 20 秒）")
    ap.add_argument("--frames", type=int, help="最多抽多少关键帧（默认按时长自动，上限 150）")
    ap.add_argument("--scene", type=float, default=0.3, help="场景切换灵敏度 0~1，越小越敏感（默认 0.3）")
    ap.add_argument("--no-dedupe", action="store_true", help="不去掉和前一帧几乎相同的画面（默认去掉，只留变化的）")
    ap.add_argument("--no-frames", action="store_true", help="不抽画面")
    ap.add_argument("--no-asr", action="store_true", help="没有字幕也不做语音识别")
    ap.add_argument("--force-asr", action="store_true", help="即使有字幕也重新做语音识别")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER", help="从浏览器读登录 cookie（chrome/edge/firefox），B站高清或会员视频需要")
    ap.add_argument("--keep", action="store_true", help="保留下载的原视频（默认处理完删掉）")
    ap.add_argument("--ui-lang", default="zh", choices=["zh", "en"], help="report.md 和文字来源描述的语言：zh（默认）或 en")
    ap.add_argument("--json", action="store_true", help="最后额外打印一行 manifest JSON")
    ap.add_argument("--version", action="version", version=f"yueying {__version__}")
    a = ap.parse_args(args)
    if a.install_skill:
        return install_skill()
    if not a.input:
        ap.error("请给出视频文件路径或链接，例如：yueying 视频.mp4")

    inputs = []
    for inp in a.input:
        if a.all and is_url(inp):
            entries = list_entries(inp, a.cookies_from_browser, log)
            if len(entries) > 1:
                log(f"      {inp} 里有 {len(entries)} 个视频，全部处理")
            inputs += [e["url"] for e in entries]
        else:
            inputs.append(inp)

    multi = len(inputs) > 1
    base = a.out if (multi or not a.out) else os.path.dirname(os.path.abspath(a.out))
    base = base or "yueying_out"
    results = []
    for i, inp in enumerate(inputs, 1):
        if multi:
            log(f"\n===== 第 {i}/{len(inputs)} 个：{inp} =====")
        try:
            m = process(inp, None if multi else a.out, base, a)
            results.append({"input": inp, "manifest": m})
        except Exception as e:
            if not multi:
                raise
            log(f"失败：{type(e).__name__}: {str(e)[:200]}")
            results.append({"input": inp, "error": f"{type(e).__name__}: {str(e)[:200]}"})

    if multi:
        idx = write_index(os.path.abspath(base), results)
        ok = sum(1 for r in results if not r.get("error"))
        log(f"\n全部完成：{ok}/{len(results)} 个成功，总目录：{idx}")
    if a.json:
        out = [_json_view(r["manifest"]) if r.get("manifest") else r for r in results]
        print(json.dumps(out if multi else out[0], ensure_ascii=False))
    return 0 if all(not r.get("error") for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
