# -*- coding: utf-8 -*-
"""Поиск точек реза: C++-бинарник, при его отсутствии — Python-фолбэк на numpy.

Оба варианта реализуют один алгоритм, поэтому дают одинаковый результат.
"""

import json
import logging
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

from . import config as cfg

ROOT = Path(__file__).resolve().parents[1]
CPP_BIN = ROOT / "cpp" / "bin" / ("silence_finder.exe" if sys.platform == "win32" else "silence_finder")

log = logging.getLogger(__name__)


def extract_pcm(video_path: Path) -> bytes:
    """Достаём mono 16 кГц s16le PCM из видео."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", str(cfg.SILENCE_SAMPLE_RATE),
        "-f", "s16le", "-",
    ]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg не смог извлечь аудио: {out.stderr.decode(errors='replace')[:300]}")
    return out.stdout


def _python_find(pcm: bytes) -> dict:
    """Python-фолбэк: ровно тот же алгоритм, что в silence_finder.cpp."""
    sr = cfg.SILENCE_SAMPLE_RATE
    frame = max(1, round(cfg.SILENCE_FRAME_MS * sr / 1000))

    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype=np.int16).astype(np.float64) / 32768.0
    duration = len(samples) / sr
    n_frames = len(samples) // frame
    if n_frames:
        block = samples[: n_frames * frame].reshape(n_frames, frame)
        rms = np.sqrt((block * block).mean(axis=1))
    else:
        rms = np.array([], dtype=np.float64)

    noise = float(np.percentile(rms, cfg.SILENCE_PERCENTILE)) if n_frames else 0.0
    median = float(np.percentile(rms, 50)) if n_frames else 0.0
    threshold = max(cfg.SILENCE_THRESHOLD_MIN,
                    min(noise * cfg.SILENCE_THRESHOLD_FACTOR, median * cfg.SILENCE_MEDIAN_FACTOR))

    min_frames = math.ceil(cfg.MIN_PAUSE * 1000 / cfg.SILENCE_FRAME_MS)
    silence, run = [], None
    for i in range(n_frames + 1):
        quiet = i < n_frames and rms[i] < threshold
        if quiet and run is None:
            run = i
        elif not quiet and run is not None:
            if i - run >= min_frames:
                silence.append([run * frame / sr, i * frame / sr])
            run = None

    cuts, previous = [], 0.0
    while previous + cfg.CLIP_DURATION <= duration + 1e-9:
        target = previous + cfg.CLIP_DURATION
        best, best_dist = target, cfg.CUT_SEARCH_WINDOW + 1e-9
        for start, end in silence:
            dist = abs((start + end) / 2 - target)
            if dist <= best_dist:
                best_dist, best = dist, (start + end) / 2
        cuts.append(round(best, 3))
        previous = best

    return {"duration": round(duration, 3), "threshold": round(threshold, 4),
            "cuts": cuts, "silences": silence}


def _cpp_find(pcm: bytes) -> dict:
    cmd = [
        str(CPP_BIN),
        "--clip-duration", str(cfg.CLIP_DURATION),
        "--window", str(cfg.CUT_SEARCH_WINDOW),
        "--frame-ms", str(cfg.SILENCE_FRAME_MS),
        "--percentile", str(cfg.SILENCE_PERCENTILE),
        "--factor", str(cfg.SILENCE_THRESHOLD_FACTOR),
        "--median-factor", str(cfg.SILENCE_MEDIAN_FACTOR),
        "--min-threshold", str(cfg.SILENCE_THRESHOLD_MIN),
        "--min-pause", str(cfg.MIN_PAUSE),
        "--sample-rate", str(cfg.SILENCE_SAMPLE_RATE),
    ]
    out = subprocess.run(cmd, input=pcm, capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(f"silence_finder: {out.stderr.decode(errors='replace')[:300]}")
    return json.loads(out.stdout.decode("utf-8"))


def find_cuts(pcm: bytes) -> dict:
    """Возвращает {'duration', 'threshold', 'cuts', 'silences'}."""
    if cfg.USE_CPP and CPP_BIN.exists():
        try:
            return _cpp_find(pcm)
        except Exception as e:  # битый бинарник и т.п. — не роняем прогон
            log.warning("C++-модуль не сработал (%s), переключаюсь на Python", e)
    return _python_find(pcm)


def make_segments(result: dict) -> list[tuple[float, float]]:
    """Список (start, end) из точек реза; короткий хвост отбрасывается."""
    duration = result["duration"]
    cuts = result["cuts"]
    segments, start = [], 0.0
    for end in cuts:
        if end - start > 0.5:
            segments.append((round(start, 3), round(end, 3)))
        start = end
    tail = duration - start
    if tail >= cfg.MIN_LAST_CLIP:
        segments.append((round(start, 3), round(duration, 3)))
    return segments
