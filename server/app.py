"""
app.py — HumFinder matching server (stage 2).

Exposes `POST /hum`: accepts a short audio recording, extracts its melody
contour, matches it against the pre-built database, and returns the top
candidates with their exact YouTube video IDs.

Run:
    pip install -r requirements.txt
    uvicorn app:app --reload --port 8000

The database is produced offline by ../build_db/ingest.py and lives in
../db/ as melodies.npy (object array of contours) + meta.json (aligned list
of {videoId, title, artist}).
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

import melody

DB_DIR = Path(os.environ.get("HUMFINDER_DB", Path(__file__).resolve().parent.parent / "db"))
MELODIES_PATH = DB_DIR / "melodies.npy"
META_PATH = DB_DIR / "meta.json"

app = FastAPI(title="HumFinder", version="1.0")

# The frontend is a static page served from anywhere (GitHub Pages, file host,
# localhost), so allow cross-origin calls. Tighten this in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_DATABASE: list[dict] = []


def load_database() -> list[dict]:
    """Load contours + metadata into the module-level cache. Missing DB is OK."""
    global _DATABASE
    if not MELODIES_PATH.exists() or not META_PATH.exists():
        _DATABASE = []
        return _DATABASE

    contours = np.load(MELODIES_PATH, allow_pickle=True)
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    if len(contours) != len(meta):
        raise RuntimeError(
            f"database mismatch: {len(contours)} contours vs {len(meta)} meta entries"
        )
    _DATABASE = [
        {**meta[i], "contour": np.asarray(contours[i], dtype=np.float32)}
        for i in range(len(meta))
    ]
    return _DATABASE


@app.on_event("startup")
def _startup() -> None:
    load_database()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "songs": len(_DATABASE)}


@app.post("/hum")
async def hum(file: UploadFile = File(...)) -> dict:
    """Match an uploaded hum/sing recording against the melody database."""
    if not _DATABASE:
        raise HTTPException(
            status_code=503,
            detail="Melody database is empty. Build it with build_db/ingest.py first.",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload.")

    try:
        audio, sr = _decode_audio(raw)
    except Exception as exc:  # noqa: BLE001 — surface any decode failure to the client
        raise HTTPException(status_code=400, detail=f"Could not decode audio: {exc}")

    query = melody.contour_from_audio(audio, sr)
    if query.size == 0:
        raise HTTPException(
            status_code=422,
            detail="Not enough pitched audio. Hum a clear tune for ~8-12 seconds.",
        )

    matches = melody.rank_matches(query, _DATABASE, top_k=5)
    return {
        "matches": [
            {
                "videoId": m.get("videoId"),
                "title": m.get("title"),
                "artist": m.get("artist"),
                "score": round(float(m["score"]), 4),
                "url": f"https://www.youtube.com/watch?v={m.get('videoId')}",
            }
            for m in matches
        ]
    }


def _decode_audio(raw: bytes) -> tuple[np.ndarray, int]:
    """Decode arbitrary audio bytes (webm/ogg/wav/mp3) to mono float32.

    Tries soundfile first (fast, handles wav/ogg/flac). Browser MediaRecorder
    usually produces webm/opus, which soundfile can't read, so we fall back to
    librosa+audioread (ffmpeg) for those.
    """
    try:
        import soundfile as sf

        audio, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio.astype(np.float32), int(sr)
    except Exception:
        import tempfile

        import librosa

        # librosa/audioread needs a real file path for the ffmpeg backend.
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=True) as tmp:
            tmp.write(raw)
            tmp.flush()
            audio, sr = librosa.load(tmp.name, sr=None, mono=True)
        return audio.astype(np.float32), int(sr)
