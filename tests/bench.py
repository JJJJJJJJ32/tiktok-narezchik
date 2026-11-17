# -*- coding: utf-8 -*-
"""Замер: C++ против Python на одном и том же часовом аудио.

Запуск: python tests/bench.py [--seconds 3600]
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as cfg  # noqa: E402
import cutter  # noqa: E402


def make_pcm(seconds: int) -> bytes:
    """Час «речи»: синус + шум + паузы каждые ~7 с."""
    sr = cfg.SILENCE_SAMPLE_RATE
    rng = np.random.default_rng(1)
    t = np.arange(int(sr * seconds)) / sr
    signal = 0.3 * np.sin(2 * np.pi * 180 * t) + rng.normal(0, 0.02, len(t))
    for start in np.arange(6.4, seconds, 7.0):
        mask = (t >= start) & (t < start + 0.6)
        signal[mask] = rng.normal(0, 0.0005, mask.sum())
    return (np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=3600)
    args = parser.parse_args()

    pcm = make_pcm(args.seconds)
    print(f"Аудио: {args.seconds} с, {len(pcm) / 1e6:.1f} МБ PCM\n")

    t0 = time.perf_counter()
    py = cutter._python_find(pcm)
    py_time = time.perf_counter() - t0
    print(f"Python (numpy): {py_time:.2f} с, резов {len(py['cuts'])}")

    if not cutter.CPP_BIN.exists():
        print("C++-бинарник не собран: python build_cpp.py")
        return 1

    t0 = time.perf_counter()
    cpp = cutter._cpp_find(pcm)
    cpp_time = time.perf_counter() - t0
    print(f"C++:            {cpp_time:.2f} с, резов {len(cpp['cuts'])}")
    print(f"Ускорение:      {py_time / cpp_time:.2f}×")

    diff = max((abs(a - b) for a, b in zip(py["cuts"], cpp["cuts"])), default=0)
    print(f"Максимальная разница резов: {diff:.4f} с")
    if diff > 0.1:
        print("ВНИМАНИЕ: результаты расходятся больше чем на 0.1 с")
        return 1
    if py_time / cpp_time < 1.2:
        print("Выигрыш меньше 20% — на таком аудио C++ почти не нужен.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
