"""把一串截图拼成 README 用的动图：python docs/dev/make_gif.py <截图目录> <输出.gif> [宽度]

约定：文件名按顺序排（01_xxx.png、02_xxx.png…），文件名里写 @<毫秒> 可单独指定该帧停留时间，
例如 03_running@2500.png。默认每帧 1.6 秒，最后一帧 3 秒。
"""
import re
import sys
from pathlib import Path

from PIL import Image

DEFAULT_MS = 1600
LAST_MS = 3000


def load(folder: Path, width: int):
    frames, durations = [], []
    for f in sorted(folder.glob("*.png")) + sorted(folder.glob("*.jpg")):
        img = Image.open(f).convert("RGB")
        if img.width > width:
            img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
        m = re.search(r"@(\d+)", f.stem)
        frames.append(img)
        durations.append(int(m.group(1)) if m else DEFAULT_MS)
    if not frames:
        raise SystemExit(f"没有找到截图：{folder}")
    durations[-1] = max(durations[-1], LAST_MS)
    return frames, durations


def pad_to_same_size(frames):
    """截图尺寸可能差几个像素，统一到最大尺寸，避免 GIF 抖动。"""
    w = max(f.width for f in frames)
    h = max(f.height for f in frames)
    out = []
    for f in frames:
        if f.size != (w, h):
            canvas = Image.new("RGB", (w, h), (255, 255, 255))
            canvas.paste(f, (0, 0))
            f = canvas
        out.append(f)
    return out


def main() -> int:
    folder = Path(sys.argv[1])
    out = Path(sys.argv[2])
    width = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    frames, durations = load(folder, width)
    frames = pad_to_same_size(frames)
    # 统一调色板，否则每帧各自量化会闪
    palette = frames[0].quantize(colors=200, method=Image.MEDIANCUT)
    frames = [f.quantize(palette=palette, dither=Image.FLOYDSTEINBERG) for f in frames]
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True, disposal=2)
    kb = out.stat().st_size / 1024
    print(f"{out} · {len(frames)} 帧 · {frames[0].width}x{frames[0].height} · {kb:.0f} KB "
          f"· 总时长 {sum(durations)/1000:.1f}s")
    if kb > 6000:
        print("提示：超过 6 MB，GitHub 上加载会慢，考虑减帧或把宽度降到 800")
    return 0


sys.exit(main())
