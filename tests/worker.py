# -*- coding: utf-8 -*-
"""Монтаж в дочернем процессе: настройки из меню должны доходить до рендера.

На Windows пул процессов запускает новые интерпретаторы, поэтому настройки
передаются внутри задания. Тест ловит регрессию: если передача сломается,
клип соберётся с заводским режимом кадра вместо выбранного в меню.

Запуск:  .venv\\Scripts\\python.exe tests\\worker.py
"""

import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import composer
import config as cfg
import subtitles

WORK = ROOT / "work" / "test_worker"


def render(job: dict) -> str:
    return composer.render_clip(job)


def mean_red(png: Path, y: int) -> int:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(png), "-vf", f"crop=200:40:0:{y},scale=1:1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    return out.stdout[0] if len(out.stdout) >= 3 else -1


def check_short_banner() -> None:
    """Ролик короче вставки зацикливается, а не обрывает клип."""
    banner = WORK / "short.mp4"
    if not banner.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=green:s=320x240:rate=30:duration=1",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             str(banner)], check=True)
    ready = composer._banner_ready(banner, 4.0)
    seconds = composer.probe_duration(ready)
    assert abs(seconds - 4.0) <= 0.3, seconds
    print(f"короткий ролик зациклен до {seconds:.1f} с")


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    check_short_banner()
    clip = WORK / "clip.mp4"
    if not clip.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(clip)], check=True)

    ass = subtitles.write_ass([], WORK / "sub.ass")
    out = WORK / "out.mp4"

    # В родителе — режим width, а заданию говорим «чёрный низ».
    old = cfg.BACKGROUND_MODE
    cfg.BACKGROUND_MODE = "width"
    try:
        job = {
            "clip_path": str(clip),
            "start": 0.0,
            "end": 2.0,
            "backgrounds": [],
            "ass_path": str(ass),
            "out_path": str(out),
            "settings": {"BACKGROUND_MODE": "black", "TOP_RATIO": 0.5},
        }
        with ProcessPoolExecutor(max_workers=1) as pool:
            pool.submit(render, job).result()
    finally:
        cfg.BACKGROUND_MODE = old

    assert composer.probe_size(out) == (cfg.WIDTH, cfg.HEIGHT)

    frame = WORK / "frame.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1", "-i", str(out),
                    "-frames:v", "1", str(frame)], check=True, capture_output=True)

    # В режиме width на y=500 ещё чёрное поле, в режиме black — уже видео.
    red = mean_red(frame, 500)
    assert red > 24, f"настройки не дошли до процесса рендера: y=500 красный={red}"
    print(f"настройки в процессе применены: y=500 красный={red}")
    print("ВСЁ ОК")


if __name__ == "__main__":
    main()
