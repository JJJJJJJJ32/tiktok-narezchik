# -*- coding: utf-8 -*-
"""Пробный рендер: синтетический клип + музыка + реклама + озвучка.

Проверяет, что режим «видео на всю ширину + чёрные поля» даёт настоящий
кадр 1080x1920 со звуком, субтитрами и вставкой посередине.

Запуск:  .venv\\Scripts\\python.exe tests\\render.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot import composer
from bot import config as cfg
from bot import subtitles

WORK = ROOT / "work" / "test_render"
CLIP_SECONDS = 24.0          # клип длиннее 20 с — попадает одна вставка посередине
BANNER_SECONDS = 4.0


def ffmpeg(*args: str) -> None:
    result = subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-500:])


def make_media() -> dict:
    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / "banners").mkdir(parents=True)
    (WORK / "music").mkdir(parents=True)

    clip = WORK / "clip.mp4"
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30:duration={CLIP_SECONDS}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_SECONDS}",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(clip))

    # Реклама без прозрачности: кладём её поверх размытого кадра клипа.
    banner = WORK / "banners" / "banner.mp4"
    ffmpeg("-f", "lavfi", "-i", f"color=c=0x00b140:s=320x240:r=30:d={BANNER_SECONDS}",
           "-f", "lavfi", "-i", f"sine=frequency=880:duration={BANNER_SECONDS}",
           "-vf", "drawbox=x=60:y=60:w=200:h=120:color=white:t=fill",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-shortest", str(banner))

    music = WORK / "music" / "track.mp3"
    ffmpeg("-f", "lavfi", "-i", "sine=frequency=220:duration=5",
           "-c:a", "libmp3lame", str(music))

    voice = WORK / "voice.wav"
    ffmpeg("-f", "lavfi", "-i", f"sine=frequency=330:duration={CLIP_SECONDS}",
           "-ac", "2", "-ar", "44100", str(voice))

    return {"clip": clip, "banner": banner, "music": music, "voice": voice}


def mean_rgb(png: Path, crop: str) -> tuple[int, int, int]:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(png), "-vf", f"crop={crop},scale=1:1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True)
    data = out.stdout
    if len(data) < 3:
        return -1, -1, -1
    return data[0], data[1], data[2]


def check_frame_black_bars(png: Path) -> None:
    """Верхняя и нижняя полосы должны быть чёрными, а по центру — картинка."""
    top = mean_rgb(png, "200:40:0:0")[0]
    middle = mean_rgb(png, "200:40:0:" + str(cfg.HEIGHT // 2 - 20))[0]
    bottom = mean_rgb(png, "200:40:0:" + str(cfg.HEIGHT - 40))[0]
    assert top < 24, f"сверху не чёрное поле: {top}"
    assert bottom < 24, f"снизу не чёрное поле: {bottom}"
    assert middle > 24, f"по центру пусто: {middle}"


def check_frame_banner_scene(png: Path) -> None:
    """Кадр рекламы: размытый стоп-кадр клипа и сам ролик поверх него."""
    top = mean_rgb(png, "200:100:0:0")
    bottom = mean_rgb(png, "200:100:0:" + str(cfg.HEIGHT - 100))
    assert max(top) > 40, f"сверху не размытый клип, а чёрное: {top}"
    assert max(bottom) > 40, f"снизу не размытый клип, а чёрное: {bottom}"
    # Сам ролик виден: белая плашка в центре.
    red, green, blue = mean_rgb(png, "200:100:440:910")
    assert min(red, green, blue) > 100, f"ролик пропал: {(red, green, blue)}"
    # Баннер вписан целиком по ширине кадра.
    red, green, blue = mean_rgb(png, "40:20:0:560")
    assert green > red and green > blue, f"ролик не на месте: {(red, green, blue)}"


def main() -> None:
    media = make_media()

    cfg.BACKGROUND_MODE = "width"
    cfg.BANNER_ENABLED = True
    cfg.BANNER_DIR = str(media["banner"].parent)
    cfg.BANNER_DURATION = int(BANNER_SECONDS)
    cfg.MUSIC_ENABLED = True
    cfg.MUSIC_DIR = str(media["music"].parent)
    cfg.MUSIC_VOLUME = 0.15
    cfg.KEEP_ORIGINAL_AUDIO = False

    y = composer.caption_y(composer.probe_size(media["clip"]))
    ass = subtitles.write_ass(
        [{"start": 0.5, "end": 3.0, "text": "ПРОВЕРКА СУБТИТРОВ"},
         {"start": 4.0, "end": 8.0, "text": "ВТОРАЯ СТРОКА"}],
        WORK / "sub.ass", y)

    out = WORK / "out.mp4"
    composer.render_clip({
        "clip_path": str(media["clip"]),
        "start": 0.0,
        "end": CLIP_SECONDS,
        "backgrounds": [],
        "ass_path": str(ass),
        "out_path": str(out),
        "voice_path": str(media["voice"]),
        "music_path": str(media["music"]),
    })

    width, height = composer.probe_size(out)
    assert (width, height) == (cfg.WIDTH, cfg.HEIGHT), (width, height)

    expected = CLIP_SECONDS + BANNER_SECONDS      # одна вставка посередине
    duration = composer.probe_duration(out)
    assert abs(duration - expected) <= 1.0, (duration, expected)

    assert composer.probe_audio(out), "нет звуковой дорожки"

    # Кадр из самого клипа (реклама идёт с 12-й секунды) и кадр рекламы.
    frame_clip = WORK / "frame_clip.png"
    frame_banner = WORK / "frame_banner.png"
    for moment, target in ((3.0, frame_clip), (CLIP_SECONDS / 2 + 1, frame_banner)):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{moment:.2f}",
                        "-i", str(out), "-frames:v", "1", str(target)],
                       check=True, capture_output=True)
    check_frame_black_bars(frame_clip)
    check_frame_banner_scene(frame_banner)

    print(f"кадр {width}x{height}, длительность {duration:.1f} с "
          f"(клип {CLIP_SECONDS:.0f} + реклама {BANNER_SECONDS:.0f})")
    print(f"файл: {out}")
    print("ВСЁ ОК")


if __name__ == "__main__":
    main()
