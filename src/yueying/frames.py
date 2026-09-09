"""关键帧：场景切换检测 + 等间隔兜底；每张烧时间戳；再拼成九宫格总览图。"""
import os
import re
from PIL import Image, ImageChops, ImageDraw, ImageFont

from . import ffm


def fmt_time(t: float) -> str:
    t = int(round(t))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def slug_time(t: float) -> str:
    t = int(round(t))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m:02d}m{s:02d}s"


def scene_times(video: str, threshold: float = 0.3) -> list:
    """用 ffmpeg 的 scene 检测找画面明显变化的时间点。"""
    p = ffm.run(["-i", video, "-an", "-vf", f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-"],
                check=False)
    return [float(x) for x in re.findall(r"pts_time:\s*([0-9.]+)", p.stderr)]


MAX_FRAMES = 150


def default_interval(duration: float) -> float:
    """按时长定默认间隔（秒）：短视频密一点，长视频稀一点。"""
    if duration <= 60:
        return 2
    if duration <= 180:
        return 3
    if duration <= 600:
        return 6
    if duration <= 1800:
        return 12
    return 20


def default_target(duration: float, interval: float = None) -> int:
    """按时长（或用户给的间隔）定抽多少帧，上限 MAX_FRAMES；用户明确给了间隔就放宽到 2 倍上限。"""
    given = interval is not None
    interval = interval or default_interval(duration)
    n = int(duration / max(0.5, interval))
    return max(6, min(MAX_FRAMES * 2 if given else MAX_FRAMES, n))


def plan_times(duration: float, scenes: list, target: int) -> list:
    """把场景点整理成不多不少的一组时间点：太密就稀释，太少就用等间隔补齐。"""
    if duration <= 0:
        return []
    min_gap = max(1.0, duration / target / 2)
    picked = []
    for t in sorted(scenes):
        t = min(t + 0.4, duration - 0.3)   # 切换点本身常是转场模糊帧，往后挪一点
        if t < 0.5 or t > duration - 0.3:
            continue
        if not picked or t - picked[-1] >= min_gap:
            picked.append(t)
    if len(picked) > target:
        step = len(picked) / target
        picked = [picked[int(i * step)] for i in range(target)]
    if len(picked) < target // 2:
        step = duration / target
        for i in range(target):
            t = step * i + step / 2
            if all(abs(t - q) >= min_gap for q in picked):
                picked.append(t)
        picked.sort()
        picked = picked[:target]
    if not picked:
        picked = [min(1.0, duration / 2)]
    return picked


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # 旧版 Pillow 不支持 size
        return ImageFont.load_default()


def _burn(img: Image.Image, label: str) -> Image.Image:
    draw = ImageDraw.Draw(img)
    size = max(14, img.width // 40)
    font = _font(size)
    box = draw.textbbox((0, 0), label, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    pad = size // 3
    draw.rectangle([0, img.height - h - pad * 2, w + pad * 2, img.height], fill=(0, 0, 0))
    draw.text((pad, img.height - h - pad - box[1]), label, fill=(255, 255, 0), font=font)
    return img


DUP_RATIO = 0.02   # 缩略图上变化的像素不到 2% 就当作和前一帧相同


def _thumb(img: Image.Image) -> Image.Image:
    return img.convert("L").resize((64, 36), Image.BILINEAR)


def changed_ratio(a: Image.Image, b: Image.Image, threshold: int = 20) -> float:
    """两张缩略图里明显变化（灰度差 > threshold）的像素占比。"""
    d = ImageChops.difference(a, b).tobytes()
    return sum(1 for v in d if v > threshold) / len(d)


def extract(video: str, times: list, out_dir: str, width: int = 1280, dedupe: bool = True, log=print) -> list:
    """逐个时间点抽一帧存 jpg，返回 [{index, time, file}]。dedupe 时丢掉和前一帧几乎一样的画面。"""
    os.makedirs(out_dir, exist_ok=True)
    frames, dropped, last_thumb = [], 0, None
    tmp = os.path.join(out_dir, "_tmp.jpg")
    for n, t in enumerate(times, 1):
        p = ffm.run(["-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                     "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", tmp], check=False)
        if p.returncode != 0 or not os.path.exists(tmp):
            continue
        with Image.open(tmp) as im:
            img = im.convert("RGB")
        th = _thumb(img)
        if dedupe and last_thumb is not None and changed_ratio(last_thumb, th) < DUP_RATIO:
            dropped += 1
            continue
        last_thumb = th
        i = len(frames) + 1
        path = os.path.join(out_dir, f"f{i:03d}_{slug_time(t)}.jpg")
        _burn(img, f"#{i} {fmt_time(t)}").save(path, quality=88)
        frames.append({"index": i, "time": t, "file": path})
        if n % 10 == 0:
            log(f"  抽帧 {n}/{len(times)}")
    try:
        os.remove(tmp)
    except OSError:
        pass
    if dropped:
        log(f"      去掉 {dropped} 张和前一帧几乎相同的画面，保留 {len(frames)} 张")
    return frames


def make_grids(frames: list, out_dir: str, cols: int = 3, rows: int = 3, tile_w: int = 640) -> list:
    """把关键帧拼成总览图，一张图看 9 个画面，省得 AI 一张张翻。"""
    grids = []
    per = cols * rows
    for g in range(0, len(frames), per):
        chunk = frames[g:g + per]
        imgs = [Image.open(f["file"]).convert("RGB") for f in chunk]
        ratio = imgs[0].height / imgs[0].width
        tile_h = int(tile_w * ratio)
        n_rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * tile_w, n_rows * tile_h), (20, 20, 20))
        for k, im in enumerate(imgs):
            im = im.resize((tile_w, int(tile_w * im.height / im.width)))
            sheet.paste(im, ((k % cols) * tile_w, (k // cols) * tile_h))
        path = os.path.join(out_dir, f"grid_{g // per + 1:02d}.jpg")
        sheet.save(path, quality=85)
        grids.append({"file": path, "frames": [f["index"] for f in chunk],
                      "from": chunk[0]["time"], "to": chunk[-1]["time"]})
    return grids
