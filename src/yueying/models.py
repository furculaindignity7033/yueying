"""Whisper model catalogue: pure data + tiny helpers, no heavy imports.

Safe to import from the MCP server process (it must never import faster_whisper / ctranslate2).
"""

MODEL_SIZES = {
    "tiny": "75 MB",
    "base": "140 MB",
    "small": "480 MB",
    "medium": "1.5 GB",
    "large-v3": "3 GB",
    "large-v3-turbo": "1.6 GB",
}

MODEL_NAMES = ("auto",) + tuple(MODEL_SIZES)


def model_repo(model_name: str) -> str:
    """Hugging Face repo id that faster-whisper downloads for a model size name."""
    if model_name in ("large-v3-turbo", "turbo"):
        return "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    if "/" in model_name:          # already a repo id
        return model_name
    if model_name == "large":      # faster-whisper aliases "large" to large-v3
        model_name = "large-v3"
    return f"Systran/faster-whisper-{model_name}"


def resolve_model(model_name: str, device: str) -> str:
    """'auto' -> large-v3-turbo on cuda, small on cpu; any other name is returned unchanged."""
    if model_name == "auto":
        return "large-v3-turbo" if device == "cuda" else "small"
    return model_name
