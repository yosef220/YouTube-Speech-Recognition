"""
melody.py — melody contour extraction, normalization, and matching.

The whole HumFinder engine rests on one idea: represent both the reference
songs and the user's hum as a *pitch contour in semitones, with the median
removed*. Removing the median makes the representation key-invariant — it no
longer matters in which key you happened to hum — leaving only the intervals
between notes, which is what actually identifies a melody.

Matching a short hum against a full song is a *subsequence* problem (you hum a
snippet from the middle), so we use subsequence-DTW rather than plain DTW.

Extraction uses torchcrepe when available (best quality) and falls back to
librosa.pyin otherwise, so the module is usable without a GPU-class setup.
"""

from __future__ import annotations

import numpy as np

# Target resolution for every contour we store or compare.
POINTS_PER_SEC = 20
# Ignore pitch estimates weaker than this confidence.
MIN_CONFIDENCE = 0.50
# Human singing range, in Hz — anything outside is treated as unvoiced.
FMIN, FMAX = 65.0, 1200.0


# --------------------------------------------------------------------------- #
# Pitch extraction
# --------------------------------------------------------------------------- #
def extract_f0(audio: np.ndarray, sr: int) -> np.ndarray:
    """Return a per-frame fundamental-frequency track in Hz (0.0 = unvoiced)."""
    try:
        return _extract_f0_crepe(audio, sr)
    except Exception:
        return _extract_f0_pyin(audio, sr)


def _extract_f0_crepe(audio: np.ndarray, sr: int) -> np.ndarray:
    import torch
    import torchcrepe

    target_sr = 16000
    if sr != target_sr:
        import librosa

        audio = librosa.resample(audio, orig_sr=sr, target_sr=target_sr)
        sr = target_sr

    tensor = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
    hop = sr // 100  # 10 ms frames -> 100 fps
    device = "cuda" if torch.cuda.is_available() else "cpu"

    pitch, periodicity = torchcrepe.predict(
        tensor, sr, hop_length=hop, fmin=FMIN, fmax=FMAX,
        model="full", batch_size=512, device=device, return_periodicity=True,
    )
    pitch = pitch.squeeze(0).cpu().numpy()
    periodicity = periodicity.squeeze(0).cpu().numpy()
    pitch[periodicity < MIN_CONFIDENCE] = 0.0
    return pitch  # 100 fps


def _extract_f0_pyin(audio: np.ndarray, sr: int) -> np.ndarray:
    import librosa

    hop = sr // 100  # 100 fps to match the CREPE path
    f0, _, voiced_prob = librosa.pyin(
        audio, fmin=FMIN, fmax=FMAX, sr=sr, hop_length=hop,
    )
    f0 = np.nan_to_num(f0, nan=0.0)
    f0[voiced_prob < MIN_CONFIDENCE] = 0.0
    return f0


# --------------------------------------------------------------------------- #
# Normalization  (Hz -> key-invariant semitone contour)
# --------------------------------------------------------------------------- #
def normalize_contour(f0_hz: np.ndarray, src_fps: int = 100) -> np.ndarray:
    """Turn a raw Hz track into the canonical contour used for matching.

    Steps: keep voiced frames -> Hz to semitones -> subtract the median
    (key-invariance) -> light smoothing -> resample to POINTS_PER_SEC.
    Returns an empty array if there is not enough voiced audio to compare.
    """
    voiced = f0_hz[f0_hz > 0]
    if voiced.size < src_fps // 2:  # under ~0.5 s of pitch — unusable
        return np.array([], dtype=np.float32)

    semitones = 12.0 * np.log2(np.where(f0_hz > 0, f0_hz, np.nan) / 440.0)
    semitones = semitones - np.nanmedian(semitones)  # key-invariance

    # Interpolate across unvoiced gaps so DTW sees a continuous line.
    idx = np.arange(semitones.size)
    good = ~np.isnan(semitones)
    semitones = np.interp(idx, idx[good], semitones[good])

    semitones = _smooth(semitones, win=5)
    return _resample(semitones, src_fps, POINTS_PER_SEC).astype(np.float32)


def _smooth(x: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or x.size < win:
        return x
    kernel = np.ones(win) / win
    return np.convolve(x, kernel, mode="same")


def _resample(x: np.ndarray, src_fps: int, dst_fps: int) -> np.ndarray:
    if x.size == 0:
        return x
    duration = x.size / src_fps
    n_out = max(2, int(round(duration * dst_fps)))
    src_t = np.linspace(0.0, duration, x.size)
    dst_t = np.linspace(0.0, duration, n_out)
    return np.interp(dst_t, src_t, x)


def contour_from_audio(audio: np.ndarray, sr: int) -> np.ndarray:
    """Convenience: raw audio -> canonical contour in one call."""
    return normalize_contour(extract_f0(audio, sr), src_fps=100)


# --------------------------------------------------------------------------- #
# Matching  (subsequence-DTW)
# --------------------------------------------------------------------------- #
def subsequence_dtw_distance(query: np.ndarray, reference: np.ndarray) -> float:
    """Best per-frame cost of aligning `query` to any sub-span of `reference`.

    Lower is better. The query is matched against the closest-fitting window
    inside the (longer) reference, so humming any fragment of a song works.
    """
    n, m = query.size, reference.size
    if n == 0 or m == 0:
        return float("inf")
    if n > m:  # query longer than reference: compare the other way around
        query, reference = reference, query
        n, m = m, n

    # cost[i, j] = |query[i] - reference[j]|, accumulated row by row. The first
    # query frame may align to any reference frame at no extra cost (free start),
    # so the initial row is just the raw per-frame cost.
    prev = np.abs(query[0] - reference).astype(np.float64)

    for i in range(1, n):
        row_cost = np.abs(query[i] - reference)
        cur = np.empty(m, dtype=np.float64)
        cur[0] = prev[0] + row_cost[0]
        for j in range(1, m):
            cur[j] = row_cost[j] + min(prev[j], prev[j - 1], cur[j - 1])
        prev = cur

    # Free end: the alignment may finish at any reference frame.
    return float(prev.min() / n)


def rank_matches(query: np.ndarray, database: list[dict], top_k: int = 5) -> list[dict]:
    """Score `query` against every entry in `database` and return the best.

    Each database entry must have a "contour" (np.ndarray) plus metadata such
    as videoId/title/artist. Returns copies with an added "score" (lower =
    better), sorted ascending.
    """
    scored = []
    for entry in database:
        ref = entry.get("contour")
        if ref is None or len(ref) == 0:
            continue
        dist = subsequence_dtw_distance(query, np.asarray(ref, dtype=np.float32))
        item = {k: v for k, v in entry.items() if k != "contour"}
        item["score"] = dist
        scored.append(item)

    scored.sort(key=lambda e: e["score"])
    return scored[:top_k]


# --------------------------------------------------------------------------- #
# Coarse pre-filter  (Parsons code)
# --------------------------------------------------------------------------- #
def parsons_code(contour: np.ndarray, tol: float = 0.6) -> str:
    """Reduce a contour to an Up/Down/Same string of melodic direction.

    Cheap to compute and compare, so it makes a good first-pass filter before
    the (more expensive) DTW ranking on a large database.
    """
    if contour.size < 2:
        return ""
    out = []
    for a, b in zip(contour[:-1], contour[1:]):
        diff = b - a
        out.append("S" if abs(diff) < tol else ("U" if diff > 0 else "D"))
    return "".join(out)
