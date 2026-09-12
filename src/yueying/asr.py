"""语音识别：faster-whisper，本地离线；有 NVIDIA 显卡自动用 GPU，失败退回 CPU。"""
import glob
import os
import sys

from .models import MODEL_SIZES, model_repo, resolve_model  # noqa: F401  (re-exported; pure helpers live in models.py)


def _add_cuda_dlls() -> None:
    """pip 装的 nvidia-cublas-cu12 / nvidia-cudnn-cu12 把 DLL 放在 site-packages/nvidia/*/bin，
    Windows 下要手动加进搜索路径 ctranslate2 才找得到。"""
    if not sys.platform.startswith("win"):
        return
    for sp in sys.path:
        for d in glob.glob(os.path.join(sp, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def pick_device(device: str):
    """返回 (device, compute_type)。"""
    if device in ("cuda", "cpu"):
        return device, ("float16" if device == "cuda" else "int8")
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _load(model_name: str, device: str, compute_type: str, log):
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as e:
        msg = str(e).lower()
        # 国内访问 HuggingFace 常失败，自动换镜像再试一次
        if "HF_ENDPOINT" not in os.environ and ("huggingface" in msg or "connect" in msg or "timed out" in msg):
            log("  下载模型失败，改用镜像 hf-mirror.com 重试…")
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
            return WhisperModel(model_name, device=device, compute_type=compute_type)
        raise


# whisper 输出中文时经常不带标点，用 hotwords 在每个窗口都塞一句带标点的提示，引导它加标点
STYLE_HINT = {
    "zh": "以下是普通话的句子，带有标点符号。",
    "yue": "以下係廣東話嘅句子，有標點符號。",
    "ja": "以下は日本語の文章です。句読点があります。",
}


def _run(model, wav, language):
    from faster_whisper.audio import decode_audio
    audio = decode_audio(wav, sampling_rate=16000)
    if not language:
        language, _, _ = model.detect_language(audio, vad_filter=True)
    return model.transcribe(audio, language=language, beam_size=5, vad_filter=True,
                            condition_on_previous_text=False, hotwords=STYLE_HINT.get(language))


def _chain(first, rest):
    if first is not None:
        yield first
    yield from rest


def transcribe(wav: str, model_name: str, device: str, language, duration: float, log=print) -> tuple:
    """返回 (segments, info)。segments = [{start, end, text}]。

    model_name may be "auto": large-v3-turbo on cuda, small on cpu (and small on cpu when the GPU trial fails).
    info["model"] is the resolved name, info["requested_model"] what was asked for.
    """
    _add_cuda_dlls()
    requested = model_name
    dev, ct = pick_device(device)
    model_name = resolve_model(requested, dev)
    log(f"  模型 {model_name}，设备 {dev} ({ct})；首次使用会下载模型，请耐心等待")
    model = gen = info = None
    if dev == "cuda":
        try:
            model = _load(model_name, dev, ct, log)
            gen, info = _run(model, wav, language)
            # 真正算出第一段才知道 GPU 能不能用
            gen = iter(gen)
            gen = _chain(next(gen, None), gen)
        except Exception as e:
            log(f"  GPU 不可用（{type(e).__name__}: {str(e)[:150]}），退回 CPU")
            dev, ct = "cpu", "int8"
            model = None
            if requested == "auto":
                model_name = resolve_model(requested, dev)
                log(f"  模型 {model_name}，设备 {dev} ({ct})；首次使用会下载模型，请耐心等待")
    if model is None:
        model = _load(model_name, dev, ct, log)
        gen, info = _run(model, wav, language)
    log(f"  检测语言 {info.language}（置信度 {info.language_probability:.0%}）")
    segs = []
    last_pct = -1
    for s in gen:
        text = s.text.strip()
        if text:
            segs.append({"start": float(s.start), "end": float(s.end), "text": text})
        if duration:
            pct = int(min(s.end, duration) / duration * 100)
            if pct // 10 != last_pct // 10:
                last_pct = pct
                log(f"  识别进度 {pct}%")
    return segs, {"language": info.language, "language_probability": float(info.language_probability),
                  "model": model_name, "device": dev, "compute_type": ct, "requested_model": requested}


def _cache_dir(model_name: str) -> str:
    """Local snapshot folder of an already-downloaded model ("" if unknown)."""
    try:
        from faster_whisper.utils import download_model
        return download_model(model_name, local_files_only=True) or ""
    except Exception:
        return ""


def prepare_model(model_name: str = "auto", device: str = "auto", log=print) -> dict:
    """Download (if needed) and load the model once, e.g. for `yueying mcp --setup`.

    Returns {model, device, compute_type, cache_dir, requested_model}; falls back to CPU (and to
    `small` when "auto" was requested) if loading on the GPU fails.
    """
    _add_cuda_dlls()
    dev, ct = pick_device(device)
    name = resolve_model(model_name, dev)
    log(f"  模型 {name}，设备 {dev} ({ct})；首次使用会下载模型，请耐心等待")
    try:
        model = _load(name, dev, ct, log)
    except Exception as e:
        if dev != "cuda":
            raise
        log(f"  GPU 不可用（{type(e).__name__}: {str(e)[:150]}），退回 CPU")
        dev, ct = "cpu", "int8"
        name = resolve_model(model_name, dev)
        model = _load(name, dev, ct, log)
    del model
    return {"model": name, "device": dev, "compute_type": ct, "cache_dir": _cache_dir(name),
            "requested_model": model_name}
