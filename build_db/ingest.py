"""
ingest.py — build the HumFinder melody database (stage 2, offline).

For every YouTube URL in playlist.txt this script:
  1. downloads the audio + metadata with yt-dlp,
  2. (optionally) isolates the vocal stem with Demucs,
  3. extracts and normalizes the melody contour (server/melody.py),
  4. stores the contour + {videoId, title, artist} into ../db/.

Because each database entry already carries its own videoId, a match returns
an exact YouTube link with no extra search step that could go wrong.

Usage:
    pip install yt-dlp demucs librosa soundfile numpy torch torchcrepe
    #   (demucs/torch optional — see --no-demucs)
    python ingest.py --playlist playlist.txt --out ../db

Legal note: keep downloaded audio local, delete it after extraction (this
script does, unless --keep-audio), and don't redistribute the database. For a
fully clean corpus, build from MIDI / Free Music Archive / your own files
instead and reuse the same contour pipeline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

# Import the shared melody pipeline from ../server.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import melody  # noqa: E402


def read_playlist(path: Path) -> list[str]:
    """One URL per line; blank lines and `#` comments ignored."""
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def ytdlp_download(url: str, workdir: Path) -> dict | None:
    """Download audio to WAV and return {path, videoId, title, artist}."""
    out_tmpl = str(workdir / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp", "-x", "--audio-format", "wav",
        "--print-json", "--no-warnings", "--no-playlist",
        "-o", out_tmpl, url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"  ! yt-dlp failed for {url}: {exc}", file=sys.stderr)
        return None

    # yt-dlp prints one JSON object per download to stdout.
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    vid = info["id"]
    wav = workdir / f"{vid}.wav"
    if not wav.exists():
        print(f"  ! expected {wav} not found", file=sys.stderr)
        return None
    return {
        "path": wav,
        "videoId": vid,
        "title": info.get("title", ""),
        "artist": info.get("artist") or info.get("uploader") or "",
    }


def isolate_vocals(wav_path: Path, workdir: Path) -> Path:
    """Run Demucs and return the isolated vocals stem, or the input on failure."""
    try:
        subprocess.run(
            ["python", "-m", "demucs", "--two-stems", "vocals",
             "-o", str(workdir / "demucs"), str(wav_path)],
            capture_output=True, text=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"  ~ Demucs unavailable, using full mix: {exc}", file=sys.stderr)
        return wav_path

    stem = next((workdir / "demucs").rglob("vocals.wav"), None)
    return stem if stem else wav_path


def process_url(url: str, workdir: Path, use_demucs: bool, keep_audio: bool):
    """Full pipeline for a single URL -> (contour, meta) or None."""
    import librosa

    info = ytdlp_download(url, workdir)
    if not info:
        return None

    audio_path = info["path"]
    if use_demucs:
        audio_path = isolate_vocals(audio_path, workdir)

    audio, sr = librosa.load(str(audio_path), sr=None, mono=True)
    contour = melody.contour_from_audio(audio, sr)

    if not keep_audio:
        info["path"].unlink(missing_ok=True)

    if contour.size == 0:
        print(f"  ! no usable melody in {info['title']}", file=sys.stderr)
        return None

    print(f"  ✓ {info['title']}  ({contour.size} pts)")
    return contour, {
        "videoId": info["videoId"],
        "title": info["title"],
        "artist": info["artist"],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the HumFinder melody database.")
    ap.add_argument("--playlist", default="playlist.txt", type=Path)
    ap.add_argument("--out", default="../db", type=Path)
    ap.add_argument("--no-demucs", action="store_true", help="skip vocal isolation")
    ap.add_argument("--keep-audio", action="store_true", help="don't delete downloaded WAVs")
    args = ap.parse_args()

    urls = read_playlist(args.playlist)
    if not urls:
        print("playlist is empty — add YouTube URLs to", args.playlist)
        return

    args.out.mkdir(parents=True, exist_ok=True)
    contours: list[np.ndarray] = []
    meta: list[dict] = []

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")
            result = process_url(url, workdir, not args.no_demucs, args.keep_audio)
            if result:
                contour, m = result
                contours.append(contour)
                meta.append(m)

    if not contours:
        print("no songs ingested — nothing written.")
        return

    np.save(args.out / "melodies.npy", np.array(contours, dtype=object), allow_pickle=True)
    (args.out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nDone. {len(contours)} songs -> {args.out}/melodies.npy + meta.json")


if __name__ == "__main__":
    main()
