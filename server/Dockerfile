# HumFinder matching server — Hugging Face Spaces (Docker SDK) image.
#
# Free CPU Spaces have no GPU, so we deliberately do NOT install torch/torchcrepe;
# melody.py falls back to librosa.pyin for pitch tracking, which keeps the image
# small and the cold start fast. ffmpeg + libsndfile cover audio decoding
# (webm/opus from the browser, plus wav/ogg/flac).
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face best practice: run as a non-root user.
RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY --chown=appuser:appuser requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=appuser:appuser . .
RUN mkdir -p /app/db && chown -R appuser:appuser /app

# numba (pulled in by librosa) and matplotlib refuse to start without a writable
# cache dir; point them at /tmp. HUMFINDER_DB is where app.py looks for the DB.
ENV HOME=/home/appuser \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/mpl \
    HUMFINDER_DB=/app/db \
    PORT=7860

USER appuser
EXPOSE 7860
# HF routes traffic to the port declared as app_port in README (7860); honour
# $PORT too so the same image runs anywhere.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-7860}"]
