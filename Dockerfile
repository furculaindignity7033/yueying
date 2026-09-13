# Container image for MCP hosts and registry checks (Glama, Docker MCP catalog).
#
# The pipeline runs CPU-only here: containers get no GPU by default, so speech
# recognition defaults to the `small` Whisper model.  Mount a volume at /data to
# keep results and the downloaded model between runs, and mount your videos
# read-only, e.g.
#
#   docker run --rm -i -v yueying-data:/data -v "$PWD/videos:/videos:ro" yueying
#
# Then point the MCP client at container paths (/videos/lesson.mp4).
FROM python:3.12-slim

# ffmpeg from the distro: imageio-ffmpeg ships a static build, but the system one
# is smaller to trust on slim images and is picked up first by ffm.ffmpeg_exe().
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# PYTHONUNBUFFERED is deliberately NOT set: stdout is the JSON-RPC wire and
# unbuffered library writes could reach it before the server claims the stream.
ENV PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_PROGRESS_BARS=1 \
    TQDM_DISABLE=1 \
    YUEYING_OUT_DIR=/data/yueying_out \
    YUEYING_DEVICE=cpu \
    YUEYING_MODEL=small \
    HF_HOME=/data/huggingface

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && mkdir -p /data/yueying_out /data/huggingface

VOLUME ["/data"]
ENTRYPOINT ["yueying", "mcp"]
