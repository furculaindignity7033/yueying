"""命令行入口：yueying <视频文件或链接> [--out 目录] ..."""
import argparse
import glob
import json
import os
import re
import sys
import time

from . import __version__, ffm, subs, frames as fr, report
from .download import is_url, download


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
    import shutil
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


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="yueying", description="阅影：把视频变成 AI 能读的文字稿和关键帧")
    ap.add_argument("input", nargs="?", help="本地视频文件，或 B站 / YouTube / 抖音等视频链接")
    ap.add_argument("--install-skill", action="store_true", help="把 Claude Code 技能装到 ~/.claude/skills/yueying，装一次即可")
    ap.add_argument("--out", help="输出目录（默认 ./yueying_out/<视频名>）")
    ap.add_argument("--lang", help="语音语言代码，如 zh / en / ja；默认自动检测")
    ap.add_argument("--model", default="large-v3-turbo", help="whisper 模型：tiny/base/small/medium/large-v3/large-v3-turbo（默认）")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"], help="识别设备，默认有显卡用显卡")
    ap.add_argument("--frames", type=int, help="最多抽多少关键帧（默认按时长自动，上限 60）")
    ap.add_argument("--scene", type=float, default=0.3, help="场景切换灵敏度 0~1，越小越敏感（默认 0.3）")
    ap.add_argument("--no-frames", action="store_true", help="不抽画面")
    ap.add_argument("--no-asr", action="store_true", help="没有字幕也不做语音识别")
    ap.add_argument("--force-asr", action="store_true", help="即使有字幕也重新做语音识别")
    ap.add_argument("--cookies-from-browser", metavar="BROWSER", help="从浏览器读登录 cookie（chrome/edge/firefox），B站高清或会员视频需要")
    ap.add_argument("--keep", action="store_true", help="保留下载的原视频（默认处理完删掉）")
    ap.add_argument("--json", action="store_true", help="最后额外打印一行 manifest JSON")
    ap.add_argument("--version", action="version", version=f"yueying {__version__}")
    a = ap.parse_args(argv)
    if a.install_skill:
        return install_skill()
    if not a.input:
        ap.error("请给出视频文件路径或链接，例如：yueying 视频.mp4")

    t0 = time.time()
    meta = {"source": a.input}
    downloaded = None
    if is_url(a.input):
        out_dir = a.out or os.path.join("yueying_out", "pending")
        log(f"[1/4] 下载 {a.input}")
        tmp_dir = os.path.join(a.out or "yueying_out", "_download")
        downloaded = download(a.input, tmp_dir, a.cookies_from_browser, log)
        video = downloaded["video"]
        meta.update({k: downloaded[k] for k in ("title", "uploader", "url", "description", "chapters", "language")})
        out_dir = a.out or os.path.join("yueying_out", slug(meta["title"]))
        sub_files = downloaded["subs"]
    else:
        video = os.path.abspath(a.input)
        if not os.path.exists(video):
            log(f"找不到文件：{video}")
            return 2
        log(f"[1/4] 本地文件 {video}")
        meta["title"] = os.path.splitext(os.path.basename(video))[0]
        out_dir = a.out or os.path.join("yueying_out", slug(meta["title"]))
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
    segs, text_source = [], {"kind": "none", "desc": "无"}
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
                text_source = {"kind": "subtitle", "file": sf, "desc": f"字幕文件 {os.path.basename(sf)}（{len(segs)} 条）"}
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
            try:
                os.remove(wav)
            except OSError:
                pass
            text_source = {"kind": "asr", **ainfo,
                           "desc": f"本地语音识别 faster-whisper {ainfo['model']}（{ainfo['device']}），"
                                   f"检测语言 {ainfo['language']}（{ainfo['language_probability']:.0%}），{len(segs)} 段"}
    elif not segs:
        log("      无字幕且已跳过语音识别")

    # ---- 画面 ----
    frame_list, grids = [], []
    log("[3/4] 画面")
    if a.no_frames or not info["width"]:
        log("      跳过")
    else:
        target = a.frames or fr.default_target(meta["duration"])
        scenes = fr.scene_times(video, a.scene)
        times = fr.plan_times(meta["duration"], scenes, target)
        log(f"      场景切换 {len(scenes)} 处，抽 {len(times)} 帧")
        frame_list = fr.extract(video, times, os.path.join(out_dir, "frames"), log=log)
        grids = fr.make_grids(frame_list, out_dir)

    # ---- 报告 ----
    log("[4/4] 写报告")
    manifest = report.write_all(out_dir, meta, segs, frame_list, grids, text_source)
    if downloaded and not a.keep:
        import shutil
        shutil.rmtree(os.path.dirname(downloaded["video"]), ignore_errors=True)
    log(f"完成，用时 {time.time() - t0:.0f} 秒")
    log(f"报告：{manifest['report']}")
    if a.json:
        print(json.dumps({k: v for k, v in manifest.items() if k != "segments"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
